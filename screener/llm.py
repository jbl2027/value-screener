"""Milestone 8: Gemini qualitative scoring of 10-K text with a strict rubric and JSON output.

Inputs per company: current Risk Factors, MD&A, and Critical Audit Matters, plus the
year-over-year Risk Factors change (added/removed sentences and a cosine similarity computed
here, in the spirit of "Lazy Prices"; the LLM judges the content of the change, not its size).

The model scores moat, management candor, and risk-factor change on 1-10 rubrics and lists CAM
topics. Every score must cite verbatim quotes; quotes are verified against the source text.
The composite input `llm_score` (0-100) is computed here from the rubric scores, not chosen by
the model.
"""
import json
import math
import re
import time
from collections import Counter

import requests

from screener.config import require

MODEL = "gemini-3.8-flash"   # pinned; listed by the API on 2026-10-04 as the newest stable Flash
PROMPT_VERSION = "v1"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Input caps (characters). ~4 characters per token.
CAP = {"risk_factors": 80_000, "mdna": 120_000, "cam": 20_000, "added": 40_000, "removed": 20_000}

SYSTEM = """You are a forensic accountant reviewing a company's annual report (Form 10-K) for an \
investment screening tool. Use ONLY the text provided. Do not use outside knowledge about the \
company, its stock, or its industry. Be skeptical and literal: score what the text shows, not \
what the company claims about itself. Every score needs 1-3 verbatim quotes (each under 30 \
words) copied exactly from the provided text. If the text is insufficient to judge an item, \
give it a 5 and set sections_adequate to false with a note."""

RUBRIC = """Score each item using these anchors (integers 1-10).

MOAT DURABILITY (from Risk Factors and MD&A): durability of competitive advantage.
 9-10: specific evidence of durable advantages (network effects, high switching costs, \
regulatory licenses, structural cost advantage, essential patents) with stable or rising \
pricing power and no material share loss.
 7-8: clear advantages, some competitive pressure.
 4-6: generic or unsupported claims; moderate competition; signs of commoditization.
 2-3: intense competition, pricing pressure, share loss, low barriers to entry.
 1: a key advantage is disclosed as lost or ending (patent cliff, loss of major customer or license).

MANAGEMENT CANDOR (from MD&A): directness about bad news.
 9-10: plainly explains negative results and their causes, quantifies headwinds, uses active \
voice, acknowledges its own mistakes.
 7-8: mostly direct, some boilerplate.
 4-6: heavy boilerplate, vague causes, selective emphasis on positives.
 2-3: obfuscation: passive voice for failures, blames only external factors, emphasizes \
non-GAAP measures without explaining GAAP results.
 1: statements contradicted by figures in the same text.

RISK FACTOR CHANGE (from the added and removed Risk Factors text vs last year; 10 = benign).
 9-10: changes are editorial, updates of dates or figures, or removal of risks; nothing new and material.
 7-8: new risks are general or industry-wide (macro, tariffs, AI regulation).
 4-6: new company-specific risks of moderate severity (new litigation, customer concentration, \
a regulatory inquiry, a restructuring).
 2-3: serious new company-specific risks (material weakness, going-concern language, a major \
investigation, covenant pressure, loss of a key customer or supplier).
 1: new risks that threaten the business's survival.

CRITICAL AUDIT MATTERS (from the auditor's report): list each CAM topic in a few words. Set \
revenue_recognition true if any CAM concerns revenue recognition (e.g. variable consideration, \
multiple performance obligations, percentage of completion). Set goodwill_or_intangible_impairment \
true if any CAM concerns goodwill or intangible asset impairment testing."""

ITEM = {"type": "OBJECT", "properties": {
    "score": {"type": "INTEGER"}, "rationale": {"type": "STRING"},
    "evidence": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["score", "rationale", "evidence"]}
SCHEMA = {"type": "OBJECT", "properties": {
    "moat": ITEM,
    "management_candor": ITEM,
    "risk_factor_change": {"type": "OBJECT", "properties": {
        "score": {"type": "INTEGER"}, "rationale": {"type": "STRING"},
        "new_risks": {"type": "ARRAY", "items": {"type": "STRING"}},
        "evidence": {"type": "ARRAY", "items": {"type": "STRING"}}},
        "required": ["score", "rationale", "new_risks", "evidence"]},
    "critical_audit_matters": {"type": "OBJECT", "properties": {
        "topics": {"type": "ARRAY", "items": {"type": "STRING"}},
        "revenue_recognition": {"type": "BOOLEAN"},
        "goodwill_or_intangible_impairment": {"type": "BOOLEAN"},
        "evidence": {"type": "ARRAY", "items": {"type": "STRING"}}},
        "required": ["topics", "revenue_recognition", "goodwill_or_intangible_impairment", "evidence"]},
    "data_quality": {"type": "OBJECT", "properties": {
        "sections_adequate": {"type": "BOOLEAN"}, "notes": {"type": "STRING"}},
        "required": ["sections_adequate", "notes"]},
}, "required": ["moat", "management_candor", "risk_factor_change", "critical_audit_matters", "data_quality"]}

CAM_PENALTY = 10  # points off llm_score when the CAM risk flag is set


# ---- year-over-year risk factor change ----
def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) > 40]


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", s)).strip()


def risk_change(current: str, prior: str) -> dict:
    """Cosine similarity of word counts, plus sentences added and removed (normalized match)."""
    def vec(t):
        return Counter(re.findall(r"[a-z']+", t.lower()))
    a, b = vec(current), vec(prior)
    dot = sum(a[w] * b[w] for w in a)
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    cur_s, pri_s = _sentences(current), _sentences(prior)
    pri_n, cur_n = {_norm(s) for s in pri_s}, {_norm(s) for s in cur_s}
    added = [s for s in cur_s if _norm(s) not in pri_n]
    removed = [s for s in pri_s if _norm(s) not in cur_n]
    return {"similarity": dot / norm if norm else None, "added": added, "removed": removed,
            "added_share": len(added) / len(cur_s) if cur_s else None}


# ---- prompt ----
def build_prompt(name: str, ticker: str, filed: str, sections: dict, change: dict | None) -> str:
    def cap(text, key):
        return text if len(text) <= CAP[key] else text[:CAP[key]] + "\n[... truncated ...]"
    if change and change["similarity"] is not None:
        rf_change = (f"Cosine similarity of this year's Risk Factors to last year's: {change['similarity']:.3f} "
                     f"({change['added_share']:.0%} of sentences are new or reworded).\n\n"
                     f"ADDED OR REWORDED SENTENCES:\n{cap(chr(10).join(change['added']), 'added')}\n\n"
                     f"REMOVED SENTENCES:\n{cap(chr(10).join(change['removed']), 'removed')}")
    else:
        rf_change = "Last year's Risk Factors were not available; judge risk factor change as 5 and say so."
    return (f"Company: {name} ({ticker}). Form 10-K filed {filed}.\n\n{RUBRIC}\n\n"
            f"===== RISK FACTORS (current year) =====\n{cap(sections['risk_factors'], 'risk_factors')}\n\n"
            f"===== RISK FACTORS: CHANGE VS PRIOR YEAR =====\n{rf_change}\n\n"
            f"===== MD&A =====\n{cap(sections['mdna'], 'mdna')}\n\n"
            f"===== AUDITOR'S REPORT: CRITICAL AUDIT MATTERS =====\n"
            f"{cap(sections['cam'], 'cam') or '(not found in the filing text)'}")


# ---- Gemini call ----
class DailyQuotaExhausted(RuntimeError):
    """The free tier's requests-per-day limit is used up (resets at midnight Pacific)."""


class Gemini:
    def __init__(self, model: str = MODEL, rpm: float = 4, tpm: int = 200_000, max_retries: int = 6):
        self.model, self.max_retries = model, max_retries
        self.min_interval, self.tpm = 60 / rpm, tpm
        self.last_call, self.last_tokens = 0.0, 0
        self.session = requests.Session()
        self.session.headers.update({"x-goog-api-key": require("GEMINI_KEY"), "Content-Type": "application/json"})

    def _pace(self, est_tokens: int) -> None:
        # Space calls by requests/minute and by the previous call's tokens against tokens/minute.
        wait = max(self.min_interval, 60 * self.last_tokens / self.tpm) - (time.monotonic() - self.last_call)
        if wait > 0:
            time.sleep(wait)

    def generate(self, prompt: str) -> tuple[dict, dict]:
        body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                     "responseSchema": SCHEMA}}
        for attempt in range(self.max_retries):
            self._pace(len(prompt) // 4)
            self.last_call = time.monotonic()
            resp = self.session.post(API.format(model=self.model), json=body, timeout=300)
            if resp.status_code == 429 and "PerDay" in resp.text:
                # Retrying is pointless until midnight Pacific; stop so the run can resume later.
                raise DailyQuotaExhausted(f"Daily request quota exhausted for {self.model}")
            if resp.status_code in (429, 500, 503):
                delay = 2 ** attempt * 10
                m = re.search(r'"retryDelay":\s*"(\d+)s"', resp.text)
                if m:
                    delay = int(m.group(1)) + 2
                time.sleep(delay)
                continue
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usageMetadata", {})
            self.last_tokens = usage.get("totalTokenCount", len(prompt) // 4)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text), usage
        raise RuntimeError(f"Gemini failed after {self.max_retries} attempts (last status {resp.status_code})")


# ---- verification and scoring ----
def _norm_quote(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("…", "...").strip(' ."\'')
    return re.sub(r"\s+", " ", s)


def verify_evidence(result: dict, source: str) -> tuple[int, int]:
    """(verified, total) quotes found verbatim (normalized) in the text sent to the model."""
    src = _norm_quote(source)
    quotes = [q for k in ("moat", "management_candor", "risk_factor_change", "critical_audit_matters")
              for q in result.get(k, {}).get("evidence", [])]
    found = 0
    for q in quotes:
        parts = [p for p in re.split(r"\.\.\.", _norm_quote(q)) if len(p.strip()) > 8]
        if parts and all(p.strip() in src for p in parts):
            found += 1
    return found, len(quotes)


def llm_score(result: dict) -> tuple[float, bool]:
    """0-100 from the three rubric scores (equal weight, 1-10 rescaled) minus the CAM penalty."""
    def scaled(k):
        return (min(max(int(result[k]["score"]), 1), 10) - 1) / 9
    base = 100 * (scaled("moat") + scaled("management_candor") + scaled("risk_factor_change")) / 3
    cam = result["critical_audit_matters"]
    cam_flag = bool(cam["revenue_recognition"] or cam["goodwill_or_intangible_impairment"])
    return max(0.0, min(100.0, base - CAM_PENALTY * cam_flag)), cam_flag
