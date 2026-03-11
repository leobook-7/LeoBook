# match_resolver.py: Intelligent match resolution using Google GenAI (GrokMatcher)
# Part of LeoBook Modules — Football.com
#
# Classes: GrokMatcher

import os
import re
import json
import sqlite3
import asyncio                          # FIX: moved to top-level (was inside _llm_resolve)
from typing import List, Dict, Optional, Tuple, Set
from Levenshtein import distance, ratio

# Try importing Google GenAI (New Package)
try:
    from google import genai
    from google.genai import types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

# ── Name normalization ──────────────────────────────────────────────────
# Only strip suffixes/prefixes that are genuinely decorative
_NOISE = re.compile(
    r'\b(?:fc|sc|sk|cf|afc|bsc|fk|nk|cd|ud|rc|rcd|og|'
    r'de|del|von|und|'
    r'sport(?:ing)?|club|athletic|athletico|association|'
    r'\d{4})\b',
    re.IGNORECASE,
)
_MULTI_SPACE = re.compile(r'\s+')


def _normalize(name: str) -> str:
    """Strip noise words, punctuation, extra spaces → lowercase tokens."""
    name = name.lower().strip()
    name = re.sub(r'[^\w\s]', ' ', name)    # drop punctuation
    name = _NOISE.sub(' ', name)
    result = _MULTI_SPACE.sub(' ', name).strip()
    # If stripping noise removed everything, return original lowercase
    return result if result else name.lower().strip()


def _tokenize(name: str) -> Set[str]:
    """Return set of meaningful tokens from a name."""
    return set(_normalize(name).split())


def _acronym_match(short: str, long: str) -> bool:
    """Check if `short` could be an acronym of `long`.
    e.g. 'psg' matches 'paris saint germain'
    """
    long_tokens = long.split()
    if len(short) < 2 or len(long_tokens) < 2:
        return False
    initials = ''.join(t[0] for t in long_tokens if t)
    return short == initials


def _best_token_lev(token: str, tokens: Set[str]) -> float:
    """Best Levenshtein ratio between a single token and any token in a set.
    Handles 'man' matching 'manchester', 'leverkusen' matching 'leverkusen'.
    """
    if not tokens:
        return 0.0
    best = 0.0
    for t in tokens:
        # Prefix match: if shorter token is a prefix of the longer one
        shorter, longer = (token, t) if len(token) <= len(t) else (t, token)
        if longer.startswith(shorter) and len(shorter) >= 3:
            best = max(best, 0.85)
        best = max(best, ratio(token, t))
    return best


def _team_score(fs_team: str, fb_team: str) -> float:
    """Score 0-1 how likely two team names refer to the same team.
    
    Uses the BEST of multiple complementary signals:
      1. Levenshtein ratio on normalized names
      2. Token Jaccard overlap
      3. Substring containment
      4. Acronym matching
      5. Per-token best-Levenshtein (handles prefix matches like man→manchester)
    """
    fs_n = _normalize(fs_team)
    fb_n = _normalize(fb_team)

    if not fs_n or not fb_n:
        return 0.0

    # 1. Exact match after normalization
    if fs_n == fb_n:
        return 1.0

    # 2. Acronym check (psg == paris saint germain)
    if _acronym_match(fs_n, fb_n) or _acronym_match(fb_n, fs_n):
        return 0.92

    # 3. Levenshtein ratio on normalized strings
    lev = ratio(fs_n, fb_n)

    # 4. Token Jaccard
    fs_tok = set(fs_n.split())
    fb_tok = set(fb_n.split())
    if fs_tok and fb_tok:
        intersection = fs_tok & fb_tok
        union = fs_tok | fb_tok
        jaccard = len(intersection) / len(union) if union else 0
    else:
        jaccard = 0.0

    # 5. Per-token best-Levenshtein average
    #    For each token in the shorter set, find its best match in the longer set
    if fs_tok and fb_tok:
        shorter_set, longer_set = (fs_tok, fb_tok) if len(fs_tok) <= len(fb_tok) else (fb_tok, fs_tok)
        token_scores = [_best_token_lev(t, longer_set) for t in shorter_set]
        token_avg = sum(token_scores) / len(token_scores) if token_scores else 0
    else:
        token_avg = 0.0

    # 6. Substring containment (handles "salzburg" in "red bull salzburg")
    shorter, longer = (fs_n, fb_n) if len(fs_n) <= len(fb_n) else (fb_n, fs_n)
    if shorter in longer:
        substr = max(len(shorter) / len(longer), 0.80) if len(shorter) >= 3 else 0.0
    else:
        substr = 0.0

    return max(lev, jaccard, token_avg, substr)


class GrokMatcher:
    def __init__(self):
        self.use_llm = HAS_GEMINI
        if not self.use_llm:
            print("    [GrokMatcher] google-genai not available. Falling back to Fuzzy.")

    @staticmethod
    def _get_name(m: Dict, role: str) -> str:
        """Extract team name from a match dict with key fallback chain."""
        if role == 'home':
            return (m.get('home_team_name') or m.get('home_team') or m.get('home') or m.get('home_id') or '').strip()
        return (m.get('away_team_name') or m.get('away_team') or m.get('away') or m.get('away_id') or '').strip()

    def _get_team_id(self, m: Dict, role: str) -> Optional[str]:
        """Extract team_id from a match dict (usually from FS fixture)."""
        if role == 'home':
            return m.get('home_team_id') or m.get('home_id')
        return m.get('away_team_id') or m.get('away_id')

    def _get_search_terms(self, conn, team_id: str) -> Set[str]:
        """Load search_terms for a team from DB."""
        if not conn or not team_id:
            return set()
        try:
            cur = conn.execute("SELECT search_terms FROM teams WHERE team_id = ?", (team_id,))
            row = cur.fetchone()
            if row and row[0]:
                return set(json.loads(row[0]))
        except Exception:
            pass
        return set()

    def _auto_learn(self, conn, team_id: str, new_alias: str) -> None:
        """Add new_alias to teams.search_terms for team_id (normalized)."""
        if not conn or not team_id or not new_alias:
            return
        
        normalized_alias = _normalize(new_alias)
        if not normalized_alias:
            return

        try:
            terms = self._get_search_terms(conn, team_id)
            if normalized_alias not in terms:
                terms.add(normalized_alias)
                conn.execute(
                    "UPDATE teams SET search_terms = ? WHERE team_id = ?",
                    (json.dumps(list(terms)), team_id)
                )
                conn.commit()
                print(f"    [AutoLearn] Added '{normalized_alias}' to team {team_id}")
        except Exception as e:
            print(f"    [AutoLearn] Failed for {team_id}: {e}")

    async def resolve_with_cascade(
        self, 
        fs_fix: Dict, 
        fb_matches: List[Dict], 
        conn: sqlite3.Connection = None
    ) -> Tuple[Optional[Dict], float, str]:
        """
        Three-layer cascade resolver:
        1. Exact / SearchTerms (Score 100)
        2. Hybrid Fuzzy (Score 65-100)
        3. LLM Escalation (Score 99 on success) + AutoLearn
        """
        home = self._get_name(fs_fix, 'home')
        away = self._get_name(fs_fix, 'away')
        home_id = self._get_team_id(fs_fix, 'home')
        away_id = self._get_team_id(fs_fix, 'away')
        
        fs_name = f"{home} vs {away}"
        if not home or not away:
            return None, 0.0, "failed"

        # --- LAYER 1: Exact / SearchTerms ---
        home_terms = self._get_search_terms(conn, home_id) if home_id else set()
        away_terms = self._get_search_terms(conn, away_id) if away_id else set()
        
        h_norm = _normalize(home)
        a_norm = _normalize(away)

        for m in fb_matches:
            fb_h = _normalize(self._get_name(m, 'home'))
            fb_a = _normalize(self._get_name(m, 'away'))
            
            h_match = (fb_h == h_norm) or (fb_h in home_terms)
            a_match = (fb_a == a_norm) or (fb_a in away_terms)
            
            if h_match and a_match:
                print(f"    [Resolver] {fs_name} -> {fb_h} vs {fb_a} | layer1 (exact/terms) | score=100")
                return m, 100.0, "search_terms"

        # --- LAYER 2: Hybrid Fuzzy ---
        best_fuzzy, fuzzy_score = self._fuzzy_resolve(fs_name, fb_matches)

        # FIX: Auto-learn on high-confidence fuzzy hits (≥ 90), not just LLM.
        # This captures systematic patterns (e.g. "Guarani FC SP" ↔ "Guarani SP")
        # that fuzzy resolves correctly but Layer 1 would miss on the next run.
        if fuzzy_score >= 90 and best_fuzzy:
            final_home = self._get_name(best_fuzzy, 'home')
            final_away = self._get_name(best_fuzzy, 'away')
            if home_id: self._auto_learn(conn, home_id, final_home)
            if away_id: self._auto_learn(conn, away_id, final_away)
        
        if fuzzy_score >= 85:
            print(f"    [Resolver] {fs_name} -> {self._get_name(best_fuzzy, 'home')} vs {self._get_name(best_fuzzy, 'away')} | layer2 (fuzzy) | score={fuzzy_score:.1f}")
            return best_fuzzy, fuzzy_score, "fuzzy"

        # --- LAYER 3: LLM Escalation ---
        if not self.use_llm:
            if fuzzy_score >= 65:
                 print(f"    [Resolver] {fs_name} -> {self._get_name(best_fuzzy, 'home')} vs {self._get_name(best_fuzzy, 'away')} | layer2-weak (fuzzy) | score={fuzzy_score:.1f}")
                 return best_fuzzy, fuzzy_score, "fuzzy"
            return None, fuzzy_score, "failed"

        # Escalate to LLM if fuzzy is weak or failed
        # Sort candidates by fuzzy score and take top 5
        sorted_candidates = []
        for m in fb_matches:
             _, s = self._fuzzy_resolve(fs_name, [m])
             sorted_candidates.append((m, s))
        sorted_candidates.sort(key=lambda x: x[1], reverse=True)
        top_candidates = [x[0] for x in sorted_candidates[:5]]

        print(f"    [Resolver] {fs_name} -> ESCALATING to LLM (fuzzy_score={fuzzy_score:.1f})...")
        llm_match, llm_score = await self._llm_resolve(fs_name, top_candidates, best_fuzzy, fuzzy_score)
        
        if llm_score >= 90:
            # AUTO-LEARN: Teach Layer 1 for next time
            final_home = self._get_name(llm_match, 'home')
            final_away = self._get_name(llm_match, 'away')
            
            if home_id: self._auto_learn(conn, home_id, final_home)
            if away_id: self._auto_learn(conn, away_id, final_away)
            
            print(f"    [Resolver] {fs_name} -> {final_home} vs {final_away} | layer3 (llm) | score={llm_score:.1f}")
            return llm_match, llm_score, "llm"

        print(f"    [Resolver] {fs_name} -> FAILED | tried: fuzzy({fuzzy_score:.1f}), llm(no_match)")
        return None, fuzzy_score, "failed"

    # Backward compatibility wrapper
    async def resolve(self, fs_name: str, fb_matches: List[Dict]) -> Tuple[Optional[Dict], float]:
        res, score, _ = await self.resolve_with_cascade({'home_team_name': fs_name, 'away_team_name': ''}, fb_matches)
        return res, score

    def _fuzzy_resolve(self, fs_name: str, fb_matches: List[Dict]) -> Tuple[Optional[Dict], float]:
        """Hybrid token-based fuzzy matcher.
        
        Scores each candidate per-team (home vs home, away vs away)
        using normalization + Levenshtein ratio + token Jaccard + 
        substring containment. Takes the min of home/away scores
        (both teams must match for a valid resolution).
        """
        fs_raw = (fs_name or '').strip().lower()
        if not fs_raw:
            return None, 0.0

        # Split FS name into home/away
        for sep in (' vs ', ' v ', ' - '):
            if sep in fs_raw:
                parts = fs_raw.split(sep, 1)
                fs_home_raw, fs_away_raw = parts[0].strip(), parts[1].strip()
                break
        else:
            # Can't split — fall back to whole-string Levenshtein
            fs_home_raw, fs_away_raw = fs_raw, ''

        best_match = None
        best_score = 0.0

        for m in fb_matches:
            fb_home = self._get_name(m, 'home').lower()
            fb_away = self._get_name(m, 'away').lower()
            if not fb_home or not fb_away:
                continue

            if fs_away_raw:
                # Per-team scoring: both teams must match
                home_s = _team_score(fs_home_raw, fb_home)
                away_s = _team_score(fs_away_raw, fb_away)
                score = min(home_s, away_s) * 100
            else:
                # Whole-string fallback (legacy path)
                candidate = f"{fb_home} vs {fb_away}"
                max_len = max(len(fs_raw), len(candidate), 1)
                dist = distance(fs_raw, candidate)
                score = max(0, (1 - dist / max_len) * 100)

            if score > best_score:
                best_score = score
                best_match = m

        return best_match, best_score

    async def _llm_resolve(
        self,
        fs_name: str,
        fb_matches: List[Dict],
        fallback_match,
        fallback_score,
    ) -> Tuple[Optional[Dict], float]:
        """Call Gemini via LLMHealthManager for multi-key/model rotation.

        FIX: Rotates through ALL available keys per model (while-loop), not
        just one. A single 429 on key K no longer discards the entire model.
        FIX: Calls on_gemini_fatal_error() for 403, replacing the non-existent
             on_gemini_403() which caused a silent AttributeError.
        FIX: Passes err_str to on_gemini_429() so daily-limit detection works.
        FIX: Skips models already marked daily-exhausted before attempting.
        """
        from Core.Intelligence.llm_health_manager import health_manager
        await health_manager.ensure_initialized()

        candidates = [
            f"{self._get_name(m, 'home')} vs {self._get_name(m, 'away')}"
            for m in fb_matches
        ]
        
        prompt_text = (
            f"I have a football match named: '{fs_name}'.\n"
            f"Which of the following options represents the same match? Return ONLY the exact option string. "
            f"If none match clearly, return 'None'.\n\n"
            f"Options:\n" + "\n".join([f"- {c}" for c in candidates])
        )
        
        # Use DESCENDING chain (intelligence-critical task)
        model_chain = health_manager.get_model_chain("aigo")

        for model_name in model_chain:
            # FIX: skip models already daily-exhausted — avoids hammering a dead model
            if health_manager.is_model_daily_exhausted(model_name):
                print(f"    [GrokMatcher] Skipping {model_name} — daily quota exhausted.")
                continue

            # FIX: rotate through ALL available keys per model, not just one.
            # Mirrors the while-True pattern from build_search_dict.py.
            while True:
                api_key = health_manager.get_next_gemini_key(model=model_name)
                if not api_key:
                    # All keys for this model are cooled or model is daily-dead
                    print(f"    [GrokMatcher] No available keys for {model_name}, trying next model...")
                    break
                try:
                    client = genai.Client(api_key=api_key)
                    response = await asyncio.to_thread(
                        client.models.generate_content,
                        model=model_name,
                        contents=prompt_text
                    )
                    
                    answer = response.text.strip().lower() if response.text else ""
                    
                    if "none" in answer or not answer:
                        return None, 0.0
                    
                    for i, cand in enumerate(candidates):
                        if cand.lower() in answer or answer in cand.lower():
                            return fb_matches[i], 99.0
                    
                    return None, 0.0
                    
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        # FIX: pass err_str for daily-limit detection
                        health_manager.on_gemini_429(api_key, model=model_name, err_str=err_str)
                        if health_manager.is_model_daily_exhausted(model_name):
                            # Daily limit hit — no point trying more keys for this model
                            break
                        # Per-minute throttle — try next key for same model
                        continue
                    elif "403" in err_str:
                        # FIX: was on_gemini_403() which doesn't exist — caused AttributeError
                        health_manager.on_gemini_fatal_error(api_key, "403 Forbidden")
                        # Key is gone; loop will get the next one
                        continue
                    elif "401" in err_str or "UNAUTHORIZED" in err_str:
                        health_manager.on_gemini_fatal_error(api_key, "401 Unauthorized")
                        continue
                    elif "400" in err_str and "INVALID_ARGUMENT" in err_str:
                        health_manager.on_gemini_fatal_error(api_key, "400 Invalid Argument")
                        continue
                    # Non-retryable error — log and abandon this model
                    print(f"    [GrokMatcher] LLM error on {model_name}: {e}")
                    break

        return None, 0.0
