# trainer.py: PPO-based RL trainer with chronological training and composite reward.
# Part of LeoBook Core — Intelligence (RL Engine)
#
# Classes: RLTrainer
# Called by: Leo.py (--train-rl)

"""
RL Trainer Module
Handles offline training from historical fixtures (chronological, day-by-day)
and online updates from new prediction outcomes.

Training constraints:
- Strict chronological order (no future data leakage)
- Season-aware date selection: training starts from each league's actual season
  start date, not a global hardcoded date floor
- Last-10 matches prioritized via recency weighting
- Prediction accuracy is the primary reward signal

Season targeting (--train-season CLI flag):
  "current"     Use each league's current_season (from leagues table). Default.
  "all"         All available seasons, oldest-first. Use for a full cold retraining.
  N (int)       Past season by offset: 1 = most recent past, 2 = two seasons ago, etc.
                Matches the --season N convention used by enrich_leagues.
  "2024/2025"   Explicit season label (split-season format).
  "2025"        Explicit season label (calendar-year format).

Bug fixes applied (2026-03-14):
  FIX-1: PPO ratio was always 1.0 — old_log_prob must be stored BEFORE re-sampling.
          Now uses a stored old_log_prob passed into train_step for Phase 2/3.
  FIX-2: Double KL in Phase 1 — removed the redundant manual KL term;
          F.kl_div IS already KL divergence, the manual re-computation was noise.
  FIX-3: Synthetic odds in Phase 2 reward — now uses xG-derived fair odds per
          fixture rather than a static lookup table, giving match-specific reward signal.
  FIX-4: active_phase default in online updates — update_from_outcomes now explicitly
          passes the correct active_phase so reward logic is never silently wrong.
  FIX-5: Expert probs identical across early dates — _get_rule_engine_probs now falls
          back to league-average xG (1.4 home / 1.1 away) when form data is empty,
          ensuring each match produces a non-constant expert distribution.
  FIX-6: GradNorm clamped at 0.5 constantly — max_norm raised to 1.0 for Phase 2/3
          (imitation still uses 0.5 for stability). Scheduler T_0 raised to 2000 to
          reduce oscillation from premature warm restarts.
  FIX-7: --train-rl runaway caused by silent last-resort fallback. The primary and
          first-fallback paths are fully season-aware and uncapped — the per-league
          leagues.current_season join is the correct boundary. Each league's season
          starts at its own date (August for European leagues, January for calendar-year
          leagues) and a global date cap would be wrong for all of them. The last-resort
          path (no season metadata found at all) now aborts with a clear error message
          directing the operator to run --enrich-leagues, instead of silently
          training on all history back to 2012.
"""

import os
import re
import json
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from .model import LeoBookRLModel
from .feature_encoder import FeatureEncoder
from .adapter_registry import AdapterRegistry
from .trainer_phases import TrainerPhasesMixin
from .trainer_io import TrainerIOMixin
from Core.Intelligence.dynamic_concurrency import DynamicConcurrencyEngine
from .market_space import (
    ACTIONS, N_ACTIONS, SYNTHETIC_ODDS, STAIRWAY_BETTABLE,
    compute_poisson_probs, probs_to_tensor_30dim,
    derive_ground_truth, stairway_gate, check_phase_readiness,
    PHASE2_MIN_ODDS_ROWS, PHASE2_MIN_DAYS_LIVE,
    PHASE3_MIN_ODDS_ROWS, PHASE3_MIN_DAYS_LIVE,
)
from Data.Access.market_evaluator import evaluate_market_outcome

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
MODELS_DIR = PROJECT_ROOT / "Data" / "Store" / "models"

# Paths
BASE_MODEL_PATH = MODELS_DIR / "leobook_base.pth"
TRAINING_CONFIG_PATH = MODELS_DIR / "training_config.json"




class RLTrainer(TrainerPhasesMixin, TrainerIOMixin):
    """
    PPO-based trainer for the LeoBook RL model.

    Training proceeds chronologically day-by-day:
    For each day D:
        1. Build features using ONLY data before D (season-aware window)
        2. For each fixture on day D:
            a. Encode features
            b. Model predicts action (market + stake)
            c. After outcome: compute composite reward
            d. PPO gradient update
    """

    def __init__(
        self,
        lr_base: float = 5e-5,
        lr_league: float = 1e-4,
        lr_team: float = 5e-5,
        gamma: float = 0.99,
        clip_epsilon: float = 0.2,
        max_seasons_back: int = 2,
        device: str = "cpu",
    ):
        self.device = torch.device(device)
        self.max_seasons_back = max_seasons_back
        self.gamma = gamma
        self.clip_epsilon = clip_epsilon

        # Model & registry
        self.model = LeoBookRLModel().to(self.device)
        self.registry = AdapterRegistry()

        # Optimizer with per-component learning rates
        param_groups = [
            {"params": self.model.trunk.parameters(), "lr": lr_base},
            {"params": self.model.policy_head.parameters(), "lr": lr_base},
            {"params": self.model.value_head.parameters(), "lr": lr_base},
            {"params": self.model.stake_head.parameters(), "lr": lr_base},
            {"params": self.model.league_embedding.parameters(), "lr": lr_league},
            {"params": self.model.league_adapters.parameters(), "lr": lr_league},
            {"params": self.model.team_adapters.parameters(), "lr": lr_team},
        ]
        self.optimizer = optim.AdamW(param_groups, weight_decay=1e-4)
        # FIX-6: Raised T_0 from 1000 → 2000 to reduce premature LR oscillation.
        self.scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(
            self.optimizer, T_0=2000, T_mult=2
        )

        self._step_count = 0
        # KL weight: starts at 0.3 in Phase 1 (imitation anchor), anneals to 0 in Phase 2+
        # so RL can genuinely outperform the expert over time.
        self.kl_weight = 0.3
        self.active_phase: int = 1  # Always set before training; default safe value

    # -------------------------------------------------------------------
    # Season Discovery & Date Selection
    # -------------------------------------------------------------------

    def _discover_seasons(self, conn) -> List[str]:
        """
        Return all distinct season labels found in schedules, most-recent-first.

        Sorts by the 4-digit start year embedded in the season string so both
        split-season ("2024/2025") and calendar-year ("2025") formats rank correctly.
        """
        rows = conn.execute(
            "SELECT DISTINCT season FROM schedules "
            "WHERE season IS NOT NULL AND season != ''"
        ).fetchall()
        seasons = [r[0] for r in rows]

        def _start_year(s: str) -> int:
            m = re.match(r'(\d{4})', s)
            return int(m.group(1)) if m else 0

        return sorted(seasons, key=_start_year, reverse=True)

    def _get_season_dates(
        self, conn, target_season: Union[str, int] = "current"
    ) -> Tuple[List[str], str]:
        """
        Build the ordered list of fixture-dates for training, filtered to the
        requested season scope.

        The model is fully season-aware: the primary path joins schedules against
        leagues.current_season per league, so each league's training window starts
        from its own actual season kickoff date — not a global cutoff.
        Split-season leagues (e.g. "2025/2026") and calendar-year leagues
        (e.g. "2025" or "2026") are both handled correctly via the season label
        stored in leagues.current_season.

        No date caps are applied to the primary path or the first fallback.
        A 365-day cap is applied ONLY to the emergency last-resort path, which
        should never be reached in normal operation. If it is reached, training
        aborts with a clear message directing the operator to run --enrich-leagues.

        Args:
            target_season:
                "current"   — per-league join against leagues.current_season.
                              Training starts from each league's own season start.
                              This is the correct default.
                "all"       — all available completed fixtures, oldest-first.
                              Use with --cold for full historical retraining.
                int N       — past season by offset: 1 = most recent past,
                              2 = two seasons ago, etc. (1-indexed, matches
                              --season N in enrich_leagues).
                str label   — explicit season label, e.g. "2024/2025" or "2025".

        Returns:
            (dates, label) where dates is a chronologically sorted list of
            date strings and label is a human-readable description for logging.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")

        # ── All seasons ─────────────────────────────────────────────────────────
        if target_season == "all":
            rows = conn.execute("""
                SELECT DISTINCT date FROM schedules
                WHERE date IS NOT NULL
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND date <= ?
                ORDER BY date ASC
            """, (today_str,)).fetchall()
            return [r[0] for r in rows], "all seasons (oldest → newest)"

        # ── Past season by offset (int) ──────────────────────────────────────────
        if isinstance(target_season, int) and target_season >= 1:
            seasons = self._discover_seasons(conn)
            if target_season >= len(seasons):
                print(f"  [TRAIN] Season offset {target_season} out of range "
                      f"({len(seasons)} seasons in DB). Falling back to current.")
            else:
                season_label = seasons[target_season]  # 1-indexed offset
                rows = conn.execute("""
                    SELECT DISTINCT date FROM schedules
                    WHERE season = ?
                      AND home_score IS NOT NULL AND away_score IS NOT NULL
                      AND date IS NOT NULL AND date <= ?
                    ORDER BY date ASC
                """, (season_label, today_str)).fetchall()
                if rows:
                    return [r[0] for r in rows], f"season {season_label} (past offset {target_season})"
                print(f"  [TRAIN] Season '{season_label}' has no completed fixtures. "
                      f"Falling back to current.")

        # ── Explicit season label (non-"current" string) ─────────────────────────
        if isinstance(target_season, str) and target_season != "current":
            rows = conn.execute("""
                SELECT DISTINCT date FROM schedules
                WHERE season = ?
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND date IS NOT NULL AND date <= ?
                ORDER BY date ASC
            """, (target_season, today_str)).fetchall()
            if rows:
                return [r[0] for r in rows], f"season {target_season}"
            print(f"  [TRAIN] Season '{target_season}' not found or has no completed "
                  f"fixtures. Falling back to current.")

        # ── Current season (default) ─────────────────────────────────────────────
        # Join schedules against leagues.current_season so training starts from
        # each league's actual season start date. No date cap applied here —
        # the season label itself is the correct boundary.
        rows = conn.execute("""
            SELECT DISTINCT s.date
            FROM schedules s
            INNER JOIN leagues l ON s.league_id = l.league_id
            WHERE s.season = l.current_season
              AND s.home_score IS NOT NULL AND s.away_score IS NOT NULL
              AND s.date IS NOT NULL AND s.date <= ?
            ORDER BY s.date ASC
        """, (today_str,)).fetchall()
        dates = [r[0] for r in rows]

        if dates:
            # ── Sanity check: no football season spans more than ~540 days ──────
            # If the earliest date returned is older than 540 days, the
            # leagues.current_season metadata is stale or corrupted — some leagues
            # have current_season set to a label that also matches historical data
            # going back years. We detect this, show a diagnostic, and abort so the
            # operator runs --enrich-leagues to fix the source data.
            # 540 days = ~18 months, safely covers the longest possible split season.
            earliest = dates[0]
            days_back = (datetime.now() - datetime.strptime(earliest, "%Y-%m-%d")).days
            if days_back > 540:
                # Find which leagues are contributing the oldest dates
                stale_leagues = conn.execute("""
                    SELECT DISTINCT l.league_id, l.name, l.current_season,
                           MIN(s.date) as earliest_date
                    FROM schedules s
                    INNER JOIN leagues l ON s.league_id = l.league_id
                    WHERE s.season = l.current_season
                      AND s.home_score IS NOT NULL AND s.away_score IS NOT NULL
                      AND s.date < date('now', '-540 days')
                    GROUP BY l.league_id
                    ORDER BY earliest_date ASC
                    LIMIT 10
                """).fetchall()
                print(
                    f"\n  [TRAIN] ! WARNING: Current-season join returned dates back to {earliest} "
                    f"({days_back} days ago).\n"
                    f"  [TRAIN]   No football season spans 540+ days. This may indicate stale metadata.\n"
                    f"  [TRAIN]   Iterating anyway as requested.\n"
                )
                for row in stale_leagues:
                    print(f"  [TRAIN]     {row[1] or row[0]:40s}  current_season={row[2]}  earliest={row[3]}")
                print("\n")
                # Removed early return: Proceed with the dates found.

            return dates, "current season (per-league season join)"

        # ── First fallback: leagues.current_season not fully populated ────────────
        # Use the most recent season label discovered in schedules.
        # No date cap — the season label is the correct boundary.
        seasons = self._discover_seasons(conn)
        if seasons:
            season_label = seasons[0]
            print(
                f"\n  [TRAIN] ⚠ WARNING: Current-season join returned no dates.\n"
                f"  [TRAIN]   leagues.current_season is not populated for enough leagues.\n"
                f"  [TRAIN]   Falling back to most recent season in DB: {season_label}\n"
                f"  [TRAIN]   → Run: python Leo.py --enrich-leagues\n"
                f"  [TRAIN]     to populate current_season and fix this properly.\n"
            )
            rows = conn.execute("""
                SELECT DISTINCT date FROM schedules
                WHERE season = ?
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND date IS NOT NULL AND date <= ?
                ORDER BY date ASC
            """, (season_label, today_str)).fetchall()
            dates = [r[0] for r in rows]
            if dates:
                return dates, (
                    f"season {season_label} "
                    f"(fallback — run --enrich-leagues to populate current_season)"
                )

        # ── Last resort: no season metadata at all — ABORT ────────────────────────
        # This path should never be reached in normal operation. If it is, the DB
        # has not been enriched. Returning an uncapped global window here would
        # silently train on all available history which is NOT what --train-rl means.
        # We return empty to let train_from_fixtures print the "no dates found" message
        # and exit cleanly, directing the operator to run --enrich-leagues first.
        print(
            f"\n  [TRAIN] ✗ CRITICAL: No season metadata found in the database.\n"
            f"  [TRAIN]   Cannot determine current season boundaries for any league.\n"
            f"  [TRAIN]   Training aborted — this would span all available history.\n"
            f"\n"
            f"  [TRAIN]   Fix: python Leo.py --enrich-leagues\n"
            f"  [TRAIN]   Then retry: python Leo.py --train-rl\n"
            f"\n"
            f"  [TRAIN]   If you intentionally want to train on all history:\n"
            f"  [TRAIN]   Use: python Leo.py --train-rl --train-season all --cold\n"
        )
        return [], "aborted — no season metadata (run --enrich-leagues first)"

    # -------------------------------------------------------------------
    # Reward functions (30-dim action space)
    def train_step(
        self,
        features: torch.Tensor,
        league_idx: int,
        home_team_idx: int,
        away_team_idx: int,
        outcome: Optional[Dict[str, Any]] = None,
        expert_probs: Optional[torch.Tensor] = None,
        use_kl: bool = False,
        old_log_prob: Optional[torch.Tensor] = None,
        xg_fair_odds: Optional[Dict[str, float]] = None,
        active_phase: Optional[int] = None,
    ) -> Dict[str, float]:
        """
        Single training step for Phase 1 (Imitation) or Phase 2/3 (PPO).

        Args:
            features:       Encoded feature tensor.
            league_idx:     League adapter index.
            home_team_idx:  Home team adapter index.
            away_team_idx:  Away team adapter index.
            outcome:        Match result dict with home_score / away_score.
                            Required for Phase 2/3. None triggers Phase 1 path.
            expert_probs:   Rule Engine + Poisson 30-dim distribution.
                            Used for Phase 1 imitation and Phase 2 KL anchor.
            use_kl:         If True, add KL penalty to Phase 2/3 loss (Phase 2 only).
            old_log_prob:   Log prob of the action sampled BEFORE this gradient step.
                            FIX-1: must be provided for correct PPO importance sampling.
                            If None, ratio defaults to 1.0 (equivalent to vanilla PG).
            xg_fair_odds:   Per-fixture fair odds dict from _get_xg_fair_odds().
                            FIX-3: used as odds fallback in Phase 2 reward.
            active_phase:   Explicit phase override (FIX-4). If None, falls back to
                            self.active_phase.

        Returns:
            Metrics dict.
        """
        self.model.train()
        features = features.to(self.device)

        # FIX-4: always resolve active_phase explicitly; never rely on implicit default.
        resolved_phase = active_phase if active_phase is not None else self.active_phase

        # Forward pass
        policy_logits, value, stake = self.model(
            features, league_idx, home_team_idx, away_team_idx
        )
        action_probs = torch.softmax(policy_logits, dim=-1)

        total_loss = torch.tensor(0.0, device=self.device)
        metrics: Dict[str, float] = {}

        # ── Phase 1: Imitation Learning ──────────────────────────────────────────
        if expert_probs is not None and outcome is None:
            if expert_probs.dim() == 1:
                expert_probs = expert_probs.unsqueeze(0)

            # FIX-2: F.kl_div IS KL divergence. Removed the redundant manual KL
            # computation that was previously added on top as a "monitoring metric".
            # total_loss is now purely the KL divergence (imitation loss).
            policy_log_probs = F.log_softmax(policy_logits, dim=-1)
            imitation_loss = F.kl_div(
                policy_log_probs, expert_probs, reduction='batchmean'
            )
            total_loss = imitation_loss

            # KL divergence as a monitoring metric only (not added to loss).
            with torch.no_grad():
                kl_monitor = torch.sum(
                    expert_probs * (
                        torch.log(expert_probs + 1e-10)
                        - torch.log(action_probs.unsqueeze(0) + 1e-10)
                    )
                )

            rl_action = torch.argmax(action_probs, dim=-1).item()
            metrics["imitation_loss"] = imitation_loss.item()
            metrics["kl_div"] = kl_monitor.item()
            metrics["action"] = rl_action
            metrics["rule_engine_acc"] = (
                1.0 if rl_action == torch.argmax(expert_probs).item() else 0.0
            )
            metrics["max_prob"] = action_probs.max().item()

        # ── Phase 2/3: PPO with optional KL anchor ───────────────────────────────
        elif outcome is not None:
            dist = torch.distributions.Categorical(action_probs)
            action = dist.sample()
            new_log_prob = dist.log_prob(action)

            h_score = outcome.get("home_score", 0)
            a_score = outcome.get("away_score", 0)

            # FIX-3: use xG fair odds as fallback if provided
            if resolved_phase >= 2:
                if xg_fair_odds is not None:
                    action_key = ACTIONS[action.item()]["key"]
                    match_odds = xg_fair_odds.get(action_key)
                else:
                    match_odds = None
                reward = self._compute_phase2_reward(
                    action.item(), h_score, a_score, live_odds=match_odds
                )
            else:
                reward = self._compute_phase1_reward(action.item(), h_score, a_score)

            reward_tensor = torch.tensor([reward], dtype=torch.float32, device=self.device)
            advantage = reward_tensor - value.squeeze(-1)

            # FIX-1: PPO importance sampling ratio.
            # old_log_prob must be the log prob of `action` under the OLD policy
            # (i.e. sampled and detached BEFORE this gradient step). If not
            # provided, ratio = 1.0 which degenerates to vanilla policy gradient.
            if old_log_prob is not None:
                ratio = torch.exp(new_log_prob - old_log_prob.detach())
            else:
                ratio = torch.ones_like(new_log_prob)

            clipped = torch.clamp(ratio, 1.0 - self.clip_epsilon, 1.0 + self.clip_epsilon)
            policy_loss = -torch.min(
                ratio * advantage.detach(),
                clipped * advantage.detach()
            ).mean()
            value_loss = nn.functional.mse_loss(value.squeeze(-1), reward_tensor)
            entropy_bonus = -0.01 * dist.entropy().mean()

            total_loss = policy_loss + 0.5 * value_loss + entropy_bonus

            if use_kl and expert_probs is not None:
                # KL anchor: keeps policy from drifting too far from expert.
                # kl_weight anneals toward 0 so RL can eventually outperform.
                kl_div = torch.sum(
                    expert_probs * (
                        torch.log(expert_probs + 1e-10)
                        - torch.log(action_probs + 1e-10)
                    )
                )
                total_loss = total_loss + self.kl_weight * kl_div
                metrics["kl_div"] = kl_div.item()

            metrics.update({
                "policy_loss": policy_loss.item(),
                "value_loss": value_loss.item(),
                "reward": reward,
                "action": action.item(),
                "new_log_prob": new_log_prob.detach(),  # returned for next-step old_log_prob
                "max_prob": action_probs.max().item(),
            })

        # Backward + optimize
        self.optimizer.zero_grad()
        total_loss.backward()
        # FIX-6: Raise clip to 1.0 for Phase 2/3; Phase 1 keeps 0.5 for stability.
        clip_norm = 0.5 if (expert_probs is not None and outcome is None) else 1.0
        torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=clip_norm)
        self.optimizer.step()
        self.scheduler.step()

        self._step_count += 1
        metrics.update({"total_loss": total_loss.item(), "step": self._step_count})
        return metrics

    # -------------------------------------------------------------------
    # Chronological training from fixtures
    # -------------------------------------------------------------------

    def train_from_fixtures(
        self,
        phase: int = 1,
        cold: bool = False,
        limit_days: Optional[int] = None,
        resume: bool = False,
        target_season: Union[str, int] = "current",
    ):
        """
        3-Phase Chronological Training with season-aware date selection.

        Phase 1: Imitation Learning (Warm-start from Rule Engine)
        Phase 2: PPO with KL penalty (Fine-tune with constraints)
        Phase 3: Adapter Fine-tuning (League specialization, frozen trunk)

        Args:
            target_season:
                "current"   Per-league season start (default). Joins against
                            leagues.current_season so each league's season
                            start date is respected individually. No date cap
                            applied — the season label is the correct boundary
                            (August for European leagues, January for calendar-
                            year leagues, etc.).
                "all"       All available seasons, oldest-first. Full cold retrain.
                int N       Past season by offset: 1 = most recent past season,
                            2 = two seasons ago, etc. Matches enrich_leagues convention.
                str label   Explicit season string, e.g. "2024/2025" or "2025".

        CLI flag: --train-season (see lifecycle.py parse_args)
        """
        from Data.Access.db_helpers import _get_conn
        from Data.Access.league_db import get_connection

        conn = _get_conn()
        os.makedirs(MODELS_DIR, exist_ok=True)

        print("\n  ============================================================")
        print(f"  RL TRAINING — PHASE {phase} {'(COLD START)' if cold else ''}")
        print("  ============================================================\n")

        # ── Auto-detect active training phase ──────────────────────
        phase_status = check_phase_readiness(conn)
        odds_rows  = phase_status["odds_rows"]
        days_live  = phase_status["days_live"]
        phase2_ready = phase_status["phase2_ready"]
        phase3_ready = phase_status["phase3_ready"]

        if phase3_ready:
            active_phase = 3
            print(f"  [RL] Phase 3 AUTO-ACTIVATED: "
                  f"{odds_rows} odds rows, {days_live} days live.")
        elif phase2_ready:
            active_phase = 2
            print(f"  [RL] Phase 2 AUTO-ACTIVATED: "
                  f"{odds_rows} odds rows, {days_live} days live.")
        else:
            active_phase = 1
            needed_rows = PHASE2_MIN_ODDS_ROWS - odds_rows
            needed_days = max(0, PHASE2_MIN_DAYS_LIVE - days_live)
            print(f"  [RL] Phase 1 active. "
                  f"Phase 2 needs: {needed_rows} more odds rows, "
                  f"{needed_days} more days of live data.")

        # FIX-4: always set self.active_phase so train_step never falls through
        # to a stale default.
        self.active_phase = active_phase

        if active_phase == 3 or phase == 3:
            print("  [TRAIN] Freezing Shared Trunk... training Adapters only.")
            for param in self.model.trunk.parameters():
                param.requires_grad = False
        elif cold:
            print("  [TRAIN] Cold start: Starting from base weights (no checkpoint loaded).")

        # ── Season-aware date selection ─────────────────────────────────────────
        all_dates, season_label = self._get_season_dates(conn, target_season)

        if not all_dates:
            print(f"  [TRAIN] No fixture dates found for target_season={target_season!r}. "
                  f"Run --enrich-leagues to populate historical data.")
            return

        today_str = datetime.now().strftime("%Y-%m-%d")
        all_dates = [d for d in all_dates if d <= today_str]

        if limit_days:
            all_dates = all_dates[:limit_days]

        print(f"  [TRAIN] Season scope:  {season_label}")
        print(f"  [TRAIN] Window:        {all_dates[0]} → {all_dates[-1]} ({len(all_dates)} fixture-days)")

        # --- Checkpoint setup ---
        CHECKPOINT_DIR = MODELS_DIR / "checkpoints"
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        latest_path = MODELS_DIR / f"phase{phase}_latest.pth"
        total_matches_global = 0
        total_correct_global = 0
        start_day_idx = 0

        # --- Resume from checkpoint ---
        if resume and not cold and latest_path.exists():
            try:
                ckpt = torch.load(latest_path, map_location=self.device, weights_only=False)
                ckpt_n_actions = ckpt.get("n_actions", 8)
                if ckpt_n_actions != N_ACTIONS:
                    print(f"  [RESUME] ✗ Checkpoint is {ckpt_n_actions}-dim but "
                          f"current model is {N_ACTIONS}-dim. Delete and retrain.")
                    return
                self.model.load_state_dict(ckpt["model_state"], strict=False)
                self.optimizer.load_state_dict(ckpt["optimizer_state"])
                if "scheduler_state" in ckpt:
                    self.scheduler.load_state_dict(ckpt["scheduler_state"])
                # Restore kl_weight from checkpoint if saved
                if "kl_weight" in ckpt:
                    self.kl_weight = ckpt["kl_weight"]
                start_day_idx = ckpt["day"]
                total_matches_global = ckpt.get("total_matches", 0)
                total_correct_global = ckpt.get("correct_predictions", 0)
                ckpt_season = ckpt.get("target_season", "unknown")
                print(f"  [RESUME] ✓ Loaded checkpoint from Day {start_day_idx}/{len(all_dates)} "
                      f"({ckpt.get('match_date', '?')}) | season={ckpt_season}")
                print(f"  [RESUME]   Matches so far: {total_matches_global} | Correct: {total_correct_global}")
                all_dates = all_dates[start_day_idx:]
                if not all_dates:
                    print(f"  [RESUME] All days already completed. Nothing to do.")
                    return
            except Exception as e:
                print(f"  [RESUME] Failed to load checkpoint: {e} — starting fresh")
                start_day_idx = 0

        # Phase 1 LR reduction: imitation needs 10x lower LR than PPO exploration.
        # Guard with `not resume` so a resumed run does not re-apply the reduction.
        original_lrs = []
        if active_phase == 1 and not resume:
            for pg in self.optimizer.param_groups:
                original_lrs.append(pg['lr'])
                pg['lr'] = pg['lr'] * 0.1
            print(f"  [TRAIN] Phase 1 LR reduced 10x for stable imitation "
                  f"(base → {self.optimizer.param_groups[0]['lr']:.2e})")

        # ── Walk-forward Recommender ─────────────────────────────────
        import sys as _sys
        _sys.path.insert(0, str(PROJECT_ROOT))
        from Scripts.recommend_bets import AdaptiveRecommender
        rec_training_path = str(MODELS_DIR / "recommender_weights_training.json")
        recommender = AdaptiveRecommender(weights_path=rec_training_path)
        rec_total_picks = 0
        rec_total_correct = 0
        print(f"  [TRAIN] Walk-forward recommender initialized (training weights).")

        for day_offset, match_date in enumerate(all_dates):
            day_idx = start_day_idx + day_offset
            day_matches = 0
            day_reward = 0.0
            day_imit_loss = 0.0
            day_kl = 0.0
            day_rl_acc = 0.0
            day_rule_acc = 0.0
            day_grad_norm = 0.0
            day_max_prob = 0.0
            day_rec_candidates = []  # Walk-forward recommendation candidates

            cursor = conn.execute("""
                SELECT 
                    s.fixture_id, s.league_id, s.home_team_id, 
                    COALESCE(NULLIF(s.home_team_name, ''), t1.name) as h_name,
                    s.away_team_id, 
                    COALESCE(NULLIF(s.away_team_name, ''), t2.name) as a_name,
                    s.home_score, s.away_score,
                    s.season,
                    s.date, s.time, s.country_league, s.match_link
                FROM schedules s
                LEFT JOIN teams t1 ON s.home_team_id = t1.team_id
                LEFT JOIN teams t2 ON s.away_team_id = t2.team_id
                WHERE s.date = ? AND s.home_score IS NOT NULL AND s.away_score IS NOT NULL
            """, (match_date,))
            fixtures = cursor.fetchall()
            if not fixtures:
                continue

            # ── Dynamic Concurrency Pre-computation ──────────────────────
            # We parallelize data building (DB heavy) and feature encoding (CPU heavy)
            # but keep the actual gradient updates (train_step) sequential.
            from Data.Access.db_helpers import _get_conn
            max_workers = DynamicConcurrencyEngine.get_for_rl(len(fixtures))
            prepped_data = []

            def _prepare_fixture(fix):
                fixture_id, league_id, home_tid, h_name, away_tid, a_name, h_score, a_score, season, f_date, f_time, f_cl, f_link = fix
                # Use a fresh, thread-unique connection to avoid shared-handle closing race conditions
                t_conn = get_connection()
                try:
                    outcome = {
                        "result": "home_win" if h_score > a_score else "away_win" if a_score > h_score else "draw",
                        "home_score": h_score, "away_score": a_score
                    }
                    l_idx = self.registry.get_league_idx(league_id)
                    h_idx = self.registry.get_team_idx(home_tid)
                    a_idx = self.registry.get_team_idx(away_tid)

                    vision_data = self._build_training_vision_data(
                        t_conn, match_date, league_id, home_tid, h_name, away_tid, a_name, season=season
                    )
                    features = FeatureEncoder.encode(vision_data)
                    expert_probs = self._get_rule_engine_probs(vision_data)
                    xg_fair_odds = self._get_xg_fair_odds(vision_data) if active_phase >= 2 else None

                    return {
                        "fix": fix,
                        "outcome": outcome,
                        "l_idx": l_idx, "h_idx": h_idx, "a_idx": a_idx,
                        "features": features,
                        "expert_probs": expert_probs,
                        "xg_fair_odds": xg_fair_odds
                    }
                finally:
                    t_conn.close()

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                prepped_data = list(executor.map(_prepare_fixture, fixtures))

            for data in prepped_data:
                fix = data["fix"]
                outcome = data["outcome"]
                l_idx, h_idx, a_idx = data["l_idx"], data["h_idx"], data["a_idx"]
                features = data["features"]
                expert_probs = data["expert_probs"]
                xg_fair_odds = data["xg_fair_odds"]

                fixture_id, league_id, home_tid, h_name, away_tid, a_name, h_score, a_score, season, f_date, f_time, f_cl, f_link = fix

                if active_phase == 1:
                    # Phase 1: pure imitation, no outcome needed
                    metrics = self.train_step(
                        features, l_idx, h_idx, a_idx,
                        expert_probs=expert_probs,
                        active_phase=active_phase,
                    )
                else:
                    # FIX-1: compute old_log_prob under CURRENT policy BEFORE the update.
                    use_kl = (active_phase == 2)
                    with torch.no_grad():
                        features_dev = features.to(self.device)
                        logits_old, _, _ = self.model(features_dev, l_idx, h_idx, a_idx)
                        probs_old = torch.softmax(logits_old, dim=-1)
                        dist_old = torch.distributions.Categorical(probs_old)
                        action_old = dist_old.sample()
                        old_log_prob = dist_old.log_prob(action_old)

                    metrics = self.train_step(
                        features, l_idx, h_idx, a_idx,
                        outcome=outcome,
                        expert_probs=expert_probs,
                        use_kl=use_kl,
                        old_log_prob=old_log_prob,
                        xg_fair_odds=xg_fair_odds,
                        active_phase=active_phase,
                    )

                day_matches += 1
                day_reward += metrics.get("reward", 0.0)
                day_imit_loss += metrics.get("imitation_loss", 0.0)
                day_kl += metrics.get("kl_div", 0.0)
                day_max_prob += metrics.get("max_prob", 0.0)

                # Gradient norm tracking
                total_norm = 0.0
                for p in self.model.parameters():
                    if p.grad is not None:
                        total_norm += p.grad.data.norm(2).item() ** 2
                day_grad_norm += total_norm ** 0.5

                # Correctness tracking
                action_idx = metrics.get("action", torch.argmax(
                    self.model.get_action_probs(features, l_idx, h_idx, a_idx)
                ).item())
                correct_actions = self._get_correct_actions(outcome)
                expert_pred_idx = torch.argmax(expert_probs).item()

                if day_idx == 0 and day_matches <= 5:
                    probs_list = expert_probs.squeeze().detach().cpu().tolist()
                    print(f"      [DEBUG] {h_name} vs {a_name}")
                    print(f"        Expert probs: {[round(p, 3) for p in probs_list]}")
                    print(f"        Expert pick: {ACTIONS[expert_pred_idx]['key']} | RL pick: {ACTIONS[action_idx]['key']}")
                    print(f"        Correct actions: {[ACTIONS[a]['key'] for a in correct_actions]}")
                    print(f"        KL: {metrics.get('kl_div', 0.0):.4f} | "
                          f"Imitation loss: {metrics.get('imitation_loss', 0.0):.4f}")

                if action_idx in correct_actions:
                    day_rl_acc += 1
                if expert_pred_idx in correct_actions:
                    day_rule_acc += 1

                self.registry.record_match(league_id, home_tid, away_tid)

                # ── Walk-forward: collect candidate for recommendation ──
                action_meta = ACTIONS[action_idx]
                pred_str = action_meta["key"]
                outcome_result = evaluate_market_outcome(
                    pred_str, str(h_score), str(a_score), h_name, a_name
                )
                is_pred_correct = (outcome_result == '1')

                league_row = conn.execute(
                    "SELECT name, region FROM leagues WHERE league_id = ?",
                    (league_id,)
                ).fetchone()
                country_league = f"{league_row['region']} - {league_row['name']}" if league_row else 'Unknown'

                day_rec_candidates.append({
                    'fixture_id': fixture_id,
                    'home_team': h_name,
                    'away_team': a_name,
                    'market': pred_str,
                    'prediction': pred_str,
                    'country_league': country_league,
                    'confidence': 'High' if metrics.get('max_prob', 0) > 0.6 else 'Medium' if metrics.get('max_prob', 0) > 0.3 else 'Low',
                    'is_correct': is_pred_correct,
                })

                # ── Save prediction to DB (mirrors Ch1 P2 + --review) ──
                try:
                    from Data.Access.db_helpers import save_prediction
                    from Data.Access.league_db import update_prediction

                    _match_data = {
                        'fixture_id': fixture_id,
                        'date': f_date or match_date,
                        'match_time': f_time or '',
                        'country_league': country_league,
                        'home_team': h_name,
                        'away_team': a_name,
                        'home_team_id': home_tid,
                        'away_team_id': away_tid,
                        'match_link': f_link or '',
                        'league_stage': '',
                    }
                    _pred_result = {
                        'type': pred_str,
                        'confidence': day_rec_candidates[-1]['confidence'],
                        'reason': [f'RL Phase {active_phase} training prediction'],
                        'xg_home': 0.0,
                        'xg_away': 0.0,
                        'btts': '',
                        'over_2.5': '',
                        'best_score': f'{h_score}-{a_score}',
                        'top_scores': [],
                        'home_tags': [],
                        'away_tags': [],
                        'h2h_tags': [],
                        'standings_tags': [],
                        'h2h_n': 0,
                        'home_form_n': 0,
                        'away_form_n': 0,
                        'odds': '',
                        'market_reliability': metrics.get('max_prob', 0) * 100,
                        'recommendation_score': 0,
                        'h2h_fixture_ids': [],
                        'form_fixture_ids': [],
                        'standings_snapshot': [],
                    }
                    save_prediction(_match_data, _pred_result)

                    # Immediate outcome review (scores are known)
                    update_prediction(conn, fixture_id, {
                        'actual_score': f'{h_score}-{a_score}',
                        'home_score': str(h_score),
                        'away_score': str(a_score),
                        'outcome_correct': outcome_result or '',
                        'status': 'reviewed',
                    })
                except Exception as _save_err:
                    pass  # Don't break training if save fails

            # ── Walk-forward Recommendation: select, evaluate, learn ──
            rec_day_acc = 0.0
            rec_day_total = 0
            if day_matches > 0 and day_rec_candidates:
                top_picks = recommender.select_top_picks(
                    day_rec_candidates, min_picks=2, max_picks=8
                )
                rec_day_correct = sum(1 for p in top_picks if p['is_correct'])
                rec_day_total = len(top_picks)
                rec_day_acc = (rec_day_correct / rec_day_total * 100) if rec_day_total > 0 else 0
                rec_total_picks += rec_day_total
                rec_total_correct += rec_day_correct
                recommender.learn_from_day(day_rec_candidates)

            if day_matches > 0:
                rl_acc = (day_rl_acc / day_matches) * 100
                rule_acc = (day_rule_acc / day_matches) * 100
                kl = day_kl / day_matches
                gn = day_grad_norm / day_matches
                max_prob_pct = (day_max_prob / day_matches) * 100
                rec_str = f" | Rec: {rec_day_acc:4.1f}%({rec_day_total}pk)" if rec_day_total > 0 else ""
                if active_phase == 1:
                    il = day_imit_loss / day_matches
                    print(f"  [Day {day_idx+1:3d}/{start_day_idx + len(all_dates)}] "
                          f"Rule: {rule_acc:4.1f}% | RL: {rl_acc:4.1f}%{rec_str} | "
                          f"KL: {kl:5.3f} | IL: {il:6.4f} | MP: {max_prob_pct:4.1f}% | "
                          f"GN: {gn:.4f} | M: {day_matches}")
                else:
                    rw = day_reward / day_matches
                    print(f"  [Day {day_idx+1:3d}/{start_day_idx + len(all_dates)}] "
                          f"Rule: {rule_acc:4.1f}% | RL: {rl_acc:4.1f}%{rec_str} | "
                          f"KL: {kl:5.3f} | Rw: {rw:6.3f} | MP: {max_prob_pct:4.1f}% | "
                          f"GN: {gn:.4f} | M: {day_matches}")

                # --- KL annealing for true outperformance (Phase 2+) ---
                if active_phase >= 2:
                    self.kl_weight = max(0.0, self.kl_weight * 0.995)
                    if self.kl_weight < 0.01:
                        self.kl_weight = 0.0

                # --- Save checkpoint after each day ---
                total_matches_global += day_matches
                total_correct_global += int(day_rl_acc)
                ckpt_data = {
                    "day": day_idx + 1,
                    "total_days": start_day_idx + len(all_dates),
                    "match_date": match_date,
                    "model_state": self.model.state_dict(),
                    "optimizer_state": self.optimizer.state_dict(),
                    "scheduler_state": self.scheduler.state_dict(),
                    "kl_weight": self.kl_weight,
                    "total_matches": total_matches_global,
                    "correct_predictions": total_correct_global,
                    "phase": active_phase,
                    "n_actions": N_ACTIONS,
                    "odds_rows_at_save": odds_rows,
                    "days_live_at_save": days_live,
                    "target_season": str(target_season),
                    "season_label": season_label,
                }
                torch.save(ckpt_data, CHECKPOINT_DIR / f"phase{active_phase}_day{day_idx+1:03d}.pth")
                torch.save(ckpt_data, latest_path)

                # Keep only last 5 daily checkpoints
                existing = sorted(CHECKPOINT_DIR.glob(f"phase{active_phase}_day*.pth"))
                while len(existing) > 5:
                    existing[0].unlink()
                    existing = existing[1:]

        # Restore LR after Phase 1
        if original_lrs:
            for pg, lr in zip(self.optimizer.param_groups, original_lrs):
                pg['lr'] = lr

        self.save()

        # ── Walk-forward Recommender Summary ────────────────────────
        if rec_total_picks > 0:
            overall_rec_acc = (rec_total_correct / rec_total_picks) * 100
            print(f"\n  ============================================================")
            print(f"  RECOMMENDATION TRAINING SUMMARY")
            print(f"  ============================================================")
            print(f"  Total picks:     {rec_total_picks}")
            print(f"  Correct picks:   {rec_total_correct}")
            print(f"  Overall Rec Acc: {overall_rec_acc:.1f}%")
            print(f"  Target:          70.0% (Stairway survival floor)")
            print(f"  Status:          {'✓ ABOVE TARGET' if overall_rec_acc >= 70 else '✗ BELOW TARGET'}")
            print(f"  ============================================================")
            recommender.copy_to_production()
        else:
            print(f"\n  [Recommender] No recommendation picks made during training.")

        print(f"\n  [TRAIN] Phase {phase} complete. Model saved.")
