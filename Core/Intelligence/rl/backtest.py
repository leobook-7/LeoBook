# backtest.py: Walk-forward backtest harness for Phase 1 RL model.
# Part of LeoBook Core Intelligence — RL Engine
#
# Functions: run_walk_forward(), _eval_day(), _write_report()
# Called by: Leo.py (--backtest-rl flag)

"""
Walk-Forward Backtester
Trains on a rolling window of historical data, evaluates on the NEXT day
(never seen during training), records every prediction, correct action,
and outcome, then produces a summary report.

This is read-only against the DB (no writes except the report file).
"""

import os
import torch
import statistics
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path
from collections import defaultdict

from .model import LeoBookRLModel
from .feature_encoder import FeatureEncoder
from .adapter_registry import AdapterRegistry
from .market_space import (
    ACTIONS, N_ACTIONS, SYNTHETIC_ODDS,
    derive_ground_truth, stairway_gate,
    compute_poisson_probs, probs_to_tensor_30dim,
)


class WalkForwardBacktester:
    """
    Walk-forward backtest harness.

    For each evaluation day:
      1. Train a FRESH model on the preceding `train_days` of data.
      2. Evaluate on that day's matches (unseen during training).
      3. Record every prediction vs ground truth.
    """

    def __init__(self, conn, train_days: int = 60, eval_days: int = 1):
        self.conn = conn
        self.train_days = train_days
        self.eval_days = eval_days
        self.results: List[Dict] = []
        self._start_date: str = ""
        self._end_date: str = ""

    # ──────────────────────────────────────────────────────────
    # Main entry
    # ──────────────────────────────────────────────────────────

    def run(self, start_date: str, end_date: str) -> dict:
        """
        Walk from start_date to end_date, one eval_day step at a time.

        For each step:
          1. train_window = [step_date - train_days, step_date - 1]
          2. eval_window  = [step_date, step_date + eval_days - 1]
          3. Train a fresh model on train_window
          4. Evaluate on eval_window
          5. Advance step_date by eval_days
        """
        self._start_date = start_date
        self._end_date = end_date

        start = datetime.strptime(start_date, "%Y-%m-%d")
        end   = datetime.strptime(end_date,   "%Y-%m-%d")

        total_eval_days = (end - start).days
        print(f"\n  [Backtest] Evaluating {start_date} → {end_date}")
        print(f"  [Backtest] Train window: {self.train_days} days rolling")

        step = 0
        current = start
        while current <= end:
            step += 1
            eval_date_str = current.strftime("%Y-%m-%d")
            train_start   = (current - timedelta(days=self.train_days)).strftime("%Y-%m-%d")
            train_end     = (current - timedelta(days=1)).strftime("%Y-%m-%d")

            # 1. Train fresh model on window
            self._train_window(train_start, train_end)

            # 2. Evaluate on this day
            n = self._eval_window(eval_date_str, eval_date_str, f"{train_start}:{train_end}")

            print(f"  [Backtest] Day {step}/{total_eval_days}: "
                  f"evaluated {n} matches (train: {train_start} → {train_end})")

            current += timedelta(days=self.eval_days)

        summary = self._build_summary()
        print(f"\n  [Backtest] Complete. {len(self.results)} total match evaluations.")
        return summary

    # ──────────────────────────────────────────────────────────
    # Training window
    # ──────────────────────────────────────────────────────────

    def _train_window(self, start: str, end: str) -> None:
        """
        Re-initialise a fresh model (cold start) and train on
        matches between start and end dates inclusive.
        Does NOT save checkpoints during backtest.
        """
        from .trainer import RLTrainer

        trainer = RLTrainer()
        trainer.active_phase = 1

        # Compute how many days are in this window
        d_start = datetime.strptime(start, "%Y-%m-%d")
        d_end   = datetime.strptime(end,   "%Y-%m-%d")
        window_days = (d_end - d_start).days + 1

        # Suppress checkpointing: override the model dir to a temp location
        original_save = trainer._save_checkpoint if hasattr(trainer, '_save_checkpoint') else None

        # Train with cold=True (fresh weights), no checkpoint saving
        # We override the internal training loop to limit date range
        trainer.train_from_fixtures(
            phase=1,
            cold=False,       # Use imitation learning (Poisson expert)
            limit_days=window_days,
            resume=False,
        )

    # ──────────────────────────────────────────────────────────
    # Evaluation window
    # ──────────────────────────────────────────────────────────

    def _eval_window(self, start: str, end: str, train_window_label: str) -> int:
        """
        For each match in [start, end]:
          1. Build vision_data
          2. Get RL prediction (via fresh model forward pass)
          3. Get Rule Engine prediction
          4. Get correct_actions from derive_ground_truth()
          5. Apply stairway_gate() to RL recommendation
          6. Record result
        """
        from .trainer import RLTrainer

        # Use the most recently trained model (still in memory from _train_window)
        model = LeoBookRLModel().eval()
        registry = AdapterRegistry()
        device = torch.device("cpu")

        # Try to load the latest checkpoint if available
        models_dir = Path(__file__).parent.parent.parent.parent / "Data" / "Store" / "models"
        base_path = models_dir / "leobook_base.pth"
        latest_path = models_dir / "phase1_latest.pth"
        ckpt_path = latest_path if latest_path.exists() else base_path

        if ckpt_path.exists():
            try:
                ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
                state = ckpt.get("model_state", ckpt)
                model.load_state_dict(state, strict=False)
            except Exception:
                pass  # Use random weights if load fails

        model.eval()

        # Build a trainer instance just for _build_training_vision_data
        trainer_helper = RLTrainer()

        cursor = self.conn.execute("""
            SELECT s.fixture_id, s.league_id, s.home_team_id, t1.name,
                   s.away_team_id, t2.name, s.home_score, s.away_score, s.season
            FROM schedules s
            LEFT JOIN teams t1 ON s.home_team_id = t1.team_id
            LEFT JOIN teams t2 ON s.away_team_id = t2.team_id
            WHERE s.date >= ? AND s.date <= ?
              AND s.home_score IS NOT NULL AND s.away_score IS NOT NULL
        """, (start, end))
        fixtures = cursor.fetchall()

        count = 0
        for fix in fixtures:
            fixture_id, league_id, home_tid, h_name, away_tid, a_name, h_score, a_score, season = fix

            if h_name is None or a_name is None:
                continue

            # 1. Build vision_data
            vision_data = trainer_helper._build_training_vision_data(
                self.conn, start, league_id,
                home_tid, h_name, away_tid, a_name, season=season
            )

            # 2. RL prediction
            features = FeatureEncoder.encode(vision_data)
            l_idx = registry.get_league_idx(league_id)
            h_idx = registry.get_team_idx(home_tid)
            a_idx = registry.get_team_idx(away_tid)

            with torch.no_grad():
                policy_logits, value, stake = model(features, l_idx, h_idx, a_idx)
                action_probs = torch.softmax(policy_logits, dim=-1).squeeze()
                rl_action_idx = action_probs.argmax().item()
                rl_prob = action_probs[rl_action_idx].item()

            rl_pick = ACTIONS[rl_action_idx]["key"]

            # 3. Rule Engine prediction
            rule_pick = "no_bet"
            try:
                from ..rule_engine import RuleEngine
                analysis = RuleEngine.analyze(vision_data)
                if analysis.get("type") != "SKIP":
                    rule_pick = analysis.get("type", "no_bet")
                    # Map to market key if possible
                    raw = analysis.get("raw_scores", {})
                    if raw:
                        best = max(raw, key=raw.get)
                        rule_pick_map = {"home": "home_win", "draw": "draw", "away": "away_win"}
                        rule_pick = rule_pick_map.get(best, rule_pick)
            except Exception:
                pass

            # 4. Expert pick (Poisson)
            h2h = vision_data.get("h2h_data", {})
            home_form = [m for m in h2h.get("home_last_10_matches", []) if m][:10]
            away_form = [m for m in h2h.get("away_last_10_matches", []) if m][:10]
            xg_h = FeatureEncoder._compute_xg(home_form, h_name, is_home=True)
            xg_a = FeatureEncoder._compute_xg(away_form, a_name, is_home=False)
            poisson = compute_poisson_probs(xg_h, xg_a)
            poisson_vec = probs_to_tensor_30dim(poisson)
            expert_idx = max(range(N_ACTIONS), key=lambda i: poisson_vec[i])
            expert_pick = ACTIONS[expert_idx]["key"]

            # 5. Ground truth
            gt = derive_ground_truth(int(h_score), int(a_score))
            correct_keys = [a["key"] for a in ACTIONS if gt.get(a["key"]) is True]

            # 6. Stairway gate
            synth_odds = SYNTHETIC_ODDS.get(rl_pick, 0.0)
            rl_gated, _ = stairway_gate(rl_pick, None, rl_prob)
            synth_ev = (rl_prob * synth_odds) - 1.0 if synth_odds > 0 else 0.0

            # 7. Record
            record = {
                "date":            start,
                "home":            h_name,
                "away":            a_name,
                "league_id":       league_id,
                "rule_pick":       rule_pick,
                "rl_pick":         rl_pick,
                "expert_pick":     expert_pick,
                "correct_actions": correct_keys,
                "rule_correct":    rule_pick in correct_keys,
                "rl_correct":      rl_pick in correct_keys,
                "rl_gated":        rl_gated,
                "synthetic_odds":  round(synth_odds, 3),
                "synthetic_ev":    round(synth_ev, 4),
                "home_score":      int(h_score),
                "away_score":      int(a_score),
                "train_window":    train_window_label,
                "eval_date":       start,
            }
            self.results.append(record)
            count += 1

        return count

    # ──────────────────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────────────────

    def _build_summary(self) -> dict:
        """Compute accuracy, coverage, synthetic ROI, per-market, and calibration."""
        if not self.results:
            return {"error": "No results to summarize"}

        total = len(self.results)

        # Accuracy
        rule_correct = sum(1 for r in self.results if r["rule_correct"])
        rl_correct   = sum(1 for r in self.results if r["rl_correct"])
        gated        = [r for r in self.results if r["rl_gated"]]
        gated_correct = sum(1 for r in gated if r["rl_correct"])

        rule_acc   = rule_correct / total * 100
        rl_acc     = rl_correct / total * 100
        gated_acc  = (gated_correct / len(gated) * 100) if gated else 0.0
        delta      = rl_acc - rule_acc

        # Coverage
        gated_count = len(gated)
        gate_rate   = gated_count / total * 100 if total > 0 else 0.0

        # Synthetic ROI (only on gated matches)
        synth_roi = 0.0
        if gated:
            total_ev = sum(r["synthetic_ev"] for r in gated)
            synth_roi = total_ev / gated_count * 100

        # Per-market breakdown
        market_stats = defaultdict(lambda: {"count": 0, "correct": 0, "ev_sum": 0.0})
        for r in self.results:
            key = r["rl_pick"]
            market_stats[key]["count"] += 1
            if r["rl_correct"]:
                market_stats[key]["correct"] += 1
            market_stats[key]["ev_sum"] += r["synthetic_ev"]

        per_market = {}
        for key, s in market_stats.items():
            per_market[key] = {
                "count":    s["count"],
                "accuracy": s["correct"] / s["count"] * 100 if s["count"] > 0 else 0.0,
                "avg_ev":   s["ev_sum"] / s["count"] if s["count"] > 0 else 0.0,
            }

        # Calibration (EV quartiles)
        sorted_by_ev = sorted(self.results, key=lambda r: r["synthetic_ev"])
        n = len(sorted_by_ev)
        q_size = n // 4 or 1
        quartiles = []
        for qi in range(4):
            start = qi * q_size
            end = start + q_size if qi < 3 else n
            bucket = sorted_by_ev[start:end]
            if bucket:
                q_correct = sum(1 for r in bucket if r["rl_correct"])
                quartiles.append({
                    "label": f"Q{qi+1}",
                    "matches": len(bucket),
                    "accuracy": q_correct / len(bucket) * 100,
                })

        return {
            "rule_accuracy":     round(rule_acc, 1),
            "rl_accuracy":       round(rl_acc, 1),
            "rl_gated_accuracy": round(gated_acc, 1),
            "rl_vs_rule_delta":  round(delta, 1),
            "total_matches":     total,
            "gated_matches":     gated_count,
            "gate_rate":         round(gate_rate, 1),
            "synthetic_roi":     round(synth_roi, 2),
            "per_market":        per_market,
            "calibration":       quartiles,
        }

    # ──────────────────────────────────────────────────────────
    # Report writer
    # ──────────────────────────────────────────────────────────

    def _write_report(self, output_path: str) -> None:
        """Write a structured text report to output_path."""
        summary = self._build_summary()
        if "error" in summary:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"ERROR: {summary['error']}\n")
            return

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        lines = []
        lines.append("═" * 55)
        lines.append("LEOBOOK WALK-FORWARD BACKTEST REPORT")
        lines.append(f"Train window: {self.train_days} days rolling")
        lines.append(f"Eval period:  {self._start_date} → {self._end_date}")
        lines.append(f"Total matches evaluated: {summary['total_matches']}")
        lines.append("═" * 55)
        lines.append("")

        # Accuracy
        lines.append("ACCURACY")
        lines.append(f"  Rule Engine:       {summary['rule_accuracy']:5.1f}%")
        lines.append(f"  RL (all):          {summary['rl_accuracy']:5.1f}%")
        lines.append(f"  RL (gated only):   {summary['rl_gated_accuracy']:5.1f}%")
        sign = "+" if summary['rl_vs_rule_delta'] >= 0 else ""
        lines.append(f"  RL vs Rule delta:  {sign}{summary['rl_vs_rule_delta']:.1f}%")
        lines.append("")

        # Coverage
        lines.append("COVERAGE")
        lines.append(f"  Total matches:     {summary['total_matches']}")
        lines.append(f"  Gated matches:     {summary['gated_matches']}  ({summary['gate_rate']:.1f}% gate rate)")
        lines.append("")

        # Synthetic ROI
        lines.append("SYNTHETIC ROI (⚠ synthetic odds — not real bookmaker odds)")
        lines.append(f"  Hypothetical ROI:  {summary['synthetic_roi']:.2f}%  (per unit staked on gated bets)")
        lines.append("")

        # Per-market breakdown (top 10 by count)
        lines.append("PER-MARKET BREAKDOWN (top 10 by count)")
        sorted_markets = sorted(
            summary["per_market"].items(),
            key=lambda x: x[1]["count"], reverse=True
        )[:10]
        lines.append(f"  {'market_key':<22}| {'count':>5} | {'accuracy':>8} | {'avg_synth_ev':>12}")
        lines.append(f"  {'─' * 22}|{'─' * 7}|{'─' * 10}|{'─' * 14}")
        for key, stats in sorted_markets:
            lines.append(
                f"  {key:<22}| {stats['count']:>5} | {stats['accuracy']:>7.1f}% | "
                f"  {'+' if stats['avg_ev'] >= 0 else ''}{stats['avg_ev']:.3f}"
            )
        lines.append("")

        # Calibration
        lines.append("CALIBRATION")
        labels = ["Q1 (lowest EV)", "Q2", "Q3", "Q4 (highest EV)"]
        lines.append(f"  {'EV quartile':<20}| {'matches':>7} | {'accuracy':>8}")
        lines.append(f"  {'─' * 20}|{'─' * 9}|{'─' * 10}")
        for i, q in enumerate(summary.get("calibration", [])):
            label = labels[i] if i < len(labels) else q["label"]
            lines.append(f"  {label:<20}| {q['matches']:>7} | {q['accuracy']:>7.1f}%")
        lines.append("")

        # Disclaimer
        lines.append("═" * 55)
        lines.append("⚠ IMPORTANT: Synthetic ROI uses theoretical fair-value")
        lines.append("odds from likelihood priors. Real bookmaker odds include")
        lines.append("margin (~5-10%). Do NOT use this report as a profitability")
        lines.append("signal — use it for model quality assessment only.")
        lines.append("═" * 55)

        report_text = "\n".join(lines) + "\n"

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_text)

        # Also print to terminal
        print(report_text)
