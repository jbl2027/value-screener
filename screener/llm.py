"""Milestone 8: Gemini qualitative scoring of 10-K text with a strict rubric and JSON output.

Inputs per company: current Risk Factors, MD&A, and Critical Audit Matters; last year's CAMs;
and the year-over-year Risk Factors change (added/removed sentences, share of sentences new or
reworded, cosine similarity), computed here in the spirit of "Lazy Prices".

The model scores moat and management candor on 1-10 rubrics, and classifies (but does not score)
new risk factors and CAMs. Risk-change and CAM scores are computed here from those
classifications, as is the composite input `llm_score` (0-100). Every judgment cites verbatim
quotes, which are verified against the text sent to the model.

Prompt v2 changes from v1: moat scaled by share of revenue affected by a lost advantage; risk
change and CAM scores computed from classified items; CAMs compared with the prior year and
judged routine/unusual for the industry; llm_score weights moat 35, risk change 30, CAM 20,
candor 15; qualitative status gate.
Prompt v2.1: the model estimates materiality (share of operating income, or revenue, potentially
affected) for new risks and new unusual CAMs; the gate fires only at >= 10%. The risk-change
deduction scale is unchanged pending calibration on the 20-company test.
Prompt v2.2: each new risk gets an event type; the gate fires only for new company-specific
serious/existential risks of a listed event type (investigation or enforcement, adverse verdict,
material weakness, going-concern doubt, covenant breach or default, loss of a key customer or
contract, major impairment, recall) affecting >= 10%, or new unusual CAMs affecting >= 10%.
The risk-change deduction scale was kept (1 of 20 at the floor in the v2.1 test).
Gate refinements (same prompt v2.2): "not estimable" fires for the listed event types; a risk only
fires if its quote is verified in the filing and contains language matching its event type
(otherwise it is downgraded to review); flag/watch companies get a second classification run at
SELF_CONSISTENCY_TEMPERATURE and the gate fires if either run fires. Rule-based detectors (going
concern, material weakness, restatement, impairment) live in gate_rules.py.
"""
import json
import math
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import requests

from screener.config import require
from screener.gate_rules import quote_matches_event

# Pinned. gemini-3.8-flash (free tier: 20 requests/day, frequent 503 overload) proved unusable for a
# 500-company run; switched 2026-10-06 to the newest stable Flash-Lite, which was available when
# the other stable Flash models returned 503. All companies are scored with one model.
MODEL = "gemini-3.5-flash-lite"
PROMPT_VERSION = "v2.2"  # v2.1 + event types; the gate fires only for listed event types
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Input caps (characters). ~4 characters per token.
CAP = {"risk_factors": 80_000, "mdna": 120_000, "cam": 20_000, "added": 40_000, "removed": 20_000}

LLM_WEIGHTS = {"moat": 0.35, "risk_change": 0.30, "cam": 0.20, "candor": 0.15}
# Risk-change deductions from 10 per new risk, by (scope, severity).
RISK_DEDUCTIONS = {("company_specific", "moderate"): 2, ("company_specific", "serious"): 4,
                   ("company_specific", "existential"): 8, ("industry_wide", "moderate"): 1,
                   ("industry_wide", "serious"): 2, ("industry_wide", "existential"): 4}
# CAM deductions from 10.
CAM_NEW_UNUSUAL, CAM_NEW_ROUTINE, CAM_CONTINUING_UNUSUAL = 3, 1, 1
GATE_SEVERITIES = {"serious", "existential"}
SELF_CONSISTENCY_TEMPERATURE = 0.7  # second gate run for flag/watch companies
# Share of operating income (or revenue if operating income isn't estimable) potentially affected.
MATERIALITY = ["lt_5pct", "5_to_10pct", "10_to_25pct", "gt_25pct", "not_estimable"]
CAM_MATERIALITY = MATERIALITY + ["not_applicable"]
GATE_MATERIALITY = {"10_to_25pct", "gt_25pct"}  # the gate fires only at >= 10%
# The gate fires only for these kinds of new company-specific risk (plus new unusual CAMs).
GATE_EVENT_TYPES = ["investigation_or_enforcement", "adverse_verdict_or_judgment", "material_weakness",
                    "going_concern_doubt", "covenant_breach_or_default", "loss_of_key_customer_or_contract",
                    "major_impairment", "recall"]
EVENT_TYPES = GATE_EVENT_TYPES + ["other"]

SYSTEM = """You are a forensic accountant reviewing a company's annual report (Form 10-K) for an \
investment screening tool. Use ONLY the text provided, with one exception: when judging whether \
a critical audit matter is unusual for the company's industry, you may use general knowledge of \
which audit matters are typical for that industry. Be skeptical and literal: score what the text \
shows, not what the company claims about itself. Support each judgment with verbatim quotes \
(each under 30 words) copied exactly from the provided text."""

RUBRIC = """Follow these instructions exactly.

1. MOAT DURABILITY (integer 1-10, from Risk Factors and MD&A). Judge the business as a \
revenue-weighted whole; for diversified companies, weigh each business by its share of revenue.
 9-10: specific evidence of durable advantages (network effects, high switching costs, \
regulatory licenses, structural cost advantage, essential patents) with stable or rising \
pricing power and no material share loss.
 7-8: clear advantages, some competitive pressure.
 4-6: generic or unsupported claims; moderate competition; signs of commoditization.
 2-3: intense competition, pricing pressure, share loss, low barriers to entry.
 1: only when advantages covering the MAJORITY of revenue are disclosed as lost or ending.
Lost advantages (patent expiry, loss of a license or major customer) must be scaled by the share \
of total revenue they affect, using the company's own figures where given. Report that share in \
revenue_share_affected_by_lost_advantage (0-1, or null if none). A loss affecting 25-50% of \
revenue supports 2-3. A loss affecting under ~15% of revenue lowers the score by at most 1-2 \
points from what the rest of the business supports.

2. MANAGEMENT CANDOR (integer 1-10, from MD&A): directness about bad news.
 9-10: plainly explains negative results and their causes, quantifies headwinds, uses active \
voice, acknowledges its own mistakes.
 7-8: mostly direct, some boilerplate.
 4-6: heavy boilerplate, vague causes, selective emphasis on positives.
 2-3: obfuscation: passive voice for failures, blames only external factors, emphasizes \
non-GAAP measures without explaining GAAP results.
 1: statements contradicted by figures in the same text.

3. NEW RISK FACTORS (classify; do not score). From the ADDED OR REWORDED sentences, list each \
genuinely new or materially expanded risk (ignore editorial rewording, updated dates or figures, \
and risks that merely moved). For each give:
 severity: minor (little plausible financial impact), moderate (could dent results), serious \
(material weakness, going-concern language, major investigation or litigation, covenant \
pressure, loss of a key customer or supplier, a large impairment), existential (threatens the \
business's survival).
 scope: industry_wide (affects peers similarly: macro, tariffs, sector regulation) or \
company_specific.
 evidence: one verbatim quote from the added text.
 event_type: what the risk is about: investigation_or_enforcement (a government or regulatory \
investigation, subpoena, or enforcement action), adverse_verdict_or_judgment (a court or arbitration \
ruling against the company), material_weakness (in internal control over financial reporting), \
going_concern_doubt, covenant_breach_or_default (on debt or other obligations), \
loss_of_key_customer_or_contract, major_impairment (a large write-down of assets or goodwill), recall \
(product recall or safety withdrawal), or other (anything else, including strategy, investment, \
competition, financing, macro, regulation in general, or a planned transaction).
 materiality: the share of the company's operating income potentially affected if the risk \
materializes (use revenue if operating income cannot be estimated): lt_5pct, 5_to_10pct, \
10_to_25pct, gt_25pct, or not_estimable. Base it on figures in the text (segment results, \
revenue from the affected product, customer, or arrangement); state the basis in \
materiality_basis (operating_income or revenue) and give a one-sentence materiality_rationale.
Set assessable to false only if last year's Risk Factors were not provided.

4. CRITICAL AUDIT MATTERS (classify; do not score). For each CAM in the current auditor's report:
 topic (a few words), category, new_vs_prior_year (true if no comparable CAM appears in the prior \
year's report), unusual_for_industry (true if this kind of CAM is not typical for companies in \
this industry; goodwill impairment, revenue recognition, reserves, and income taxes are routine \
for most industries), and a one-sentence rationale. For CAMs that are both new and unusual, also \
estimate materiality as above (the share of operating income, or revenue, that the matter could \
affect); use not_applicable for other CAMs or where a share is meaningless. Set \
prior_year_available to false if the prior year's CAM text was not provided."""

QUOTED = {"type": "OBJECT", "properties": {
    "score": {"type": "INTEGER"}, "rationale": {"type": "STRING"},
    "evidence": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["score", "rationale", "evidence"]}
CAM_CATEGORIES = ["goodwill_intangibles_impairment", "revenue_recognition", "loss_contingencies_litigation",
                  "reserves_and_estimates", "income_taxes", "business_combination", "fair_value_measurement",
                  "regulatory_accounting", "inventory_valuation", "other"]
SCHEMA = {"type": "OBJECT", "properties": {
    "moat": {"type": "OBJECT", "properties": {
        "score": {"type": "INTEGER"},
        "revenue_share_affected_by_lost_advantage": {"type": "NUMBER", "nullable": True},
        "rationale": {"type": "STRING"}, "evidence": {"type": "ARRAY", "items": {"type": "STRING"}}},
        "required": ["score", "revenue_share_affected_by_lost_advantage", "rationale", "evidence"]},
    "management_candor": QUOTED,
    "risk_factor_change": {"type": "OBJECT", "properties": {
        "assessable": {"type": "BOOLEAN"},
        "new_risks": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "risk": {"type": "STRING"},
            "severity": {"type": "STRING", "enum": ["minor", "moderate", "serious", "existential"]},
            "scope": {"type": "STRING", "enum": ["industry_wide", "company_specific"]},
            "evidence": {"type": "STRING"},
            "event_type": {"type": "STRING", "enum": EVENT_TYPES},
            "materiality": {"type": "STRING", "enum": MATERIALITY},
            "materiality_basis": {"type": "STRING", "enum": ["operating_income", "revenue"]},
            "materiality_rationale": {"type": "STRING"}},
            "required": ["risk", "severity", "scope", "evidence", "event_type", "materiality",
                         "materiality_basis", "materiality_rationale"]}},
        "rationale": {"type": "STRING"}},
        "required": ["assessable", "new_risks", "rationale"]},
    "critical_audit_matters": {"type": "OBJECT", "properties": {
        "prior_year_available": {"type": "BOOLEAN"},
        "cams": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "topic": {"type": "STRING"},
            "category": {"type": "STRING", "enum": CAM_CATEGORIES},
            "new_vs_prior_year": {"type": "BOOLEAN"},
            "unusual_for_industry": {"type": "BOOLEAN"},
            "rationale": {"type": "STRING"},
            "materiality": {"type": "STRING", "enum": CAM_MATERIALITY}},
            "required": ["topic", "category", "new_vs_prior_year", "unusual_for_industry", "rationale",
                         "materiality"]}},
        "evidence": {"type": "ARRAY", "items": {"type": "STRING"}}},
        "required": ["prior_year_available", "cams", "evidence"]},
    "data_quality": {"type": "OBJECT", "properties": {
        "sections_adequate": {"type": "BOOLEAN"}, "notes": {"type": "STRING"}},
        "required": ["sections_adequate", "notes"]},
}, "required": ["moat", "management_candor", "risk_factor_change", "critical_audit_matters", "data_quality"]}


# ---- year-over-year risk factor change ----
def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) > 40]


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", s)).strip()


def risk_change(current: str, prior: str) -> dict:
    """Share of sentences new or reworded (normalized match), the added/removed sentences, and
    cosine similarity of word counts (kept for reference; it barely moves between years)."""
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
def build_prompt(name: str, ticker: str, sub_industry: str, filed: str, sections: dict,
                 change: dict | None, prior_cam: str | None) -> str:
    def cap(text, key):
        return text if len(text) <= CAP[key] else text[:CAP[key]] + "\n[... truncated ...]"
    if change and change["added_share"] is not None:
        rf_change = (f"{change['added_share']:.0%} of this year's Risk Factors sentences are new or reworded "
                     f"versus last year.\n\nADDED OR REWORDED SENTENCES:\n{cap(chr(10).join(change['added']), 'added')}"
                     f"\n\nREMOVED SENTENCES:\n{cap(chr(10).join(change['removed']), 'removed')}")
    else:
        rf_change = "Last year's Risk Factors were not available. Set assessable to false."
    return (f"Company: {name} ({ticker}). GICS sub-industry: {sub_industry}. Form 10-K filed {filed}.\n\n"
            f"{RUBRIC}\n\n"
            f"===== RISK FACTORS (current year) =====\n{cap(sections['risk_factors'], 'risk_factors')}\n\n"
            f"===== RISK FACTORS: CHANGE VS PRIOR YEAR =====\n{rf_change}\n\n"
            f"===== MD&A =====\n{cap(sections['mdna'], 'mdna')}\n\n"
            f"===== AUDITOR'S REPORT: CRITICAL AUDIT MATTERS (current year) =====\n"
            f"{cap(sections['cam'], 'cam') or '(not found in the filing text)'}\n\n"
            f"===== AUDITOR'S REPORT: CRITICAL AUDIT MATTERS (prior year) =====\n"
            f"{cap(prior_cam, 'cam') if prior_cam else '(not available)'}")


# ---- Gemini call ----
class DailyQuotaExhausted(RuntimeError):
    """The free tier's requests-per-day limit is used up (resets at midnight Pacific)."""


class ServiceUnavailable(RuntimeError):
    """Gemini keeps returning 5xx. Failed attempts count against the free-tier daily quota,
    so the run stops instead of retrying further."""


MAX_5XX_RETRIES = 2
RETRY_5XX_DELAYS = (60, 180)  # seconds
# Wall-clock limit for one request. requests' own timeout applies per socket operation, so a
# response that trickles in never trips it (one request hung a run for ~50 minutes). A request
# over the limit counts like a 5xx: retried under MAX_5XX_RETRIES, then ServiceUnavailable.
REQUEST_TIMEOUT = 300
SOCKET_TIMEOUT = (30, 120)  # connect, per-read


class Gemini:
    def __init__(self, model: str = MODEL, rpm: float = 4, tpm: int = 200_000, max_retries: int = 6):
        self.model, self.max_retries = model, max_retries
        self.min_interval, self.tpm = 60 / rpm, tpm
        self.last_call, self.last_tokens = 0.0, 0
        self.session = requests.Session()
        self.session.headers.update({"x-goog-api-key": require("GEMINI_KEY"), "Content-Type": "application/json"})

    def _pace(self) -> None:
        # Space calls by requests/minute and by the previous call's tokens against tokens/minute.
        wait = max(self.min_interval, 60 * self.last_tokens / self.tpm) - (time.monotonic() - self.last_call)
        if wait > 0:
            time.sleep(wait)

    def _post(self, body: dict) -> requests.Response:
        """POST with a wall-clock limit (REQUEST_TIMEOUT). The request runs in a worker thread so a
        stalled response can be abandoned; the thread is left to finish or fail on its own."""
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self.session.post, API.format(model=self.model), json=body, timeout=SOCKET_TIMEOUT)
        pool.shutdown(wait=False)
        try:
            return future.result(timeout=REQUEST_TIMEOUT)
        except FutureTimeout:
            raise requests.Timeout(f"no complete response within {REQUEST_TIMEOUT}s") from None

    def generate(self, prompt: str, temperature: float = 0) -> tuple[dict, dict]:
        body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature, "responseMimeType": "application/json",
                                     "responseSchema": SCHEMA}}
        server_errors = 0
        for attempt in range(self.max_retries):
            self._pace()
            self.last_call = time.monotonic()
            try:
                resp = self._post(body)
            except requests.Timeout as exc:
                # Hung or too slow: may have been processed (and counted against the quota), so it
                # is treated like a 5xx: retried sparingly, then the run stops.
                if server_errors >= MAX_5XX_RETRIES:
                    raise ServiceUnavailable(f"Gemini request timed out {server_errors + 1} times ({exc}); "
                                             f"stopping to preserve quota") from None
                time.sleep(RETRY_5XX_DELAYS[server_errors])
                server_errors += 1
                continue
            except requests.ConnectionError:
                time.sleep(2 ** attempt * 15)  # transient network failure (no request sent): retry
                continue
            if resp.status_code == 429 and "PerDay" in resp.text:
                # Retrying is pointless until midnight Pacific; stop so the run can resume later.
                raise DailyQuotaExhausted(f"Daily request quota exhausted for {self.model}")
            if resp.status_code >= 500:
                # Each failed attempt still counts against the daily quota: retry sparingly.
                if server_errors >= MAX_5XX_RETRIES:
                    try:
                        detail = resp.json()["error"]["message"]
                    except (ValueError, KeyError):
                        detail = resp.text[:200]
                    raise ServiceUnavailable(f"Gemini returned {resp.status_code} {server_errors + 1} times "
                                             f"({detail}); stopping to preserve quota")
                time.sleep(RETRY_5XX_DELAYS[server_errors])
                server_errors += 1
                continue
            if resp.status_code == 429:  # per-minute limit
                m = re.search(r'"retryDelay":\s*"(\d+)s"', resp.text)
                time.sleep(int(m.group(1)) + 2 if m else 2 ** attempt * 10)
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


def _quotes(result: dict) -> list[str]:
    q = list(result["moat"]["evidence"]) + list(result["management_candor"]["evidence"])
    q += [r["evidence"] for r in result["risk_factor_change"]["new_risks"]]
    q += list(result["critical_audit_matters"]["evidence"])
    return q


def verify_evidence(result: dict, source: str) -> tuple[int, int]:
    """(verified, total) quotes found verbatim (normalized) in the text sent to the model."""
    src = _norm_quote(source)
    quotes = _quotes(result)
    found = 0
    for q in quotes:
        parts = [p for p in re.split(r"\.\.\.", _norm_quote(q)) if len(p.strip()) > 8]
        if parts and all(p.strip() in src for p in parts):
            found += 1
    return found, len(quotes)


def component_scores(result: dict) -> dict:
    """Moat and candor from the model; risk-change and CAM scores computed from classifications."""
    rc, cam = result["risk_factor_change"], result["critical_audit_matters"]
    risk = None
    if rc["assessable"]:
        risk = max(1, 10 - sum(RISK_DEDUCTIONS.get((r["scope"], r["severity"]), 0) for r in rc["new_risks"]))
    prior = cam["prior_year_available"]
    cams = cam["cams"]
    new_unusual = sum(1 for c in cams if prior and c["new_vs_prior_year"] and c["unusual_for_industry"])
    new_routine = sum(1 for c in cams if prior and c["new_vs_prior_year"] and not c["unusual_for_industry"])
    cont_unusual = sum(1 for c in cams if c["unusual_for_industry"] and not (prior and c["new_vs_prior_year"]))
    cam_score = max(1, 10 - CAM_NEW_UNUSUAL * new_unusual - CAM_NEW_ROUTINE * new_routine
                    - CAM_CONTINUING_UNUSUAL * cont_unusual)
    return {"moat": min(max(int(result["moat"]["score"]), 1), 10),
            "candor": min(max(int(result["management_candor"]["score"]), 1), 10),
            "risk_change": risk, "cam": cam_score, "cam_count": len(cams), "cam_new": new_unusual + new_routine,
            "cam_unusual": new_unusual + cont_unusual, "cam_prior_available": prior}


def llm_score(components: dict) -> float:
    """0-100: weighted 1-10 component scores rescaled; an unassessable risk change counts as neutral."""
    total = 0.0
    for k, w in LLM_WEIGHTS.items():
        s = components[k]
        total += w * ((s - 1) / 9 if s is not None else 0.5)
    return round(100 * total, 1)


AVERAGED_COMPONENTS = ("moat", "candor", "risk_change", "cam")


def average_runs(first: dict, second: dict) -> tuple[dict, float]:
    """Mean of two runs' component scores (a component missing in one run, e.g. an unassessable
    risk change, takes the other run's value) and the mean of the two runs' llm_scores."""
    avg = dict(first)
    for k in AVERAGED_COMPONENTS:
        vals = [v for v in (first[k], second[k]) if v is not None]
        avg[k] = sum(vals) / len(vals) if vals else None
    return avg, round((llm_score(first) + llm_score(second)) / 2, 1)


def _quote_found(quote: str, source_norm: str) -> bool:
    parts = [p for p in re.split(r"\.\.\.", _norm_quote(quote)) if len(p.strip()) > 8]
    return bool(parts) and all(p.strip() in source_norm for p in parts)


def gate_candidates(result: dict, source: str) -> list[dict]:
    """New serious/existential company-specific risks of a gate event type, and new unusual CAMs.
    Each candidate gets `fires` and, if it doesn't fire, `why_not`:
      risks: materiality >= 10% or not estimable, AND the risk's quote is verified in the source
             text AND contains language matching its event type (gate_rules.EVENT_KEYWORDS)
      CAMs:  materiality >= 10%"""
    src = _norm_quote(source)
    out = []
    for r in result["risk_factor_change"]["new_risks"]:
        if not (r["scope"] == "company_specific" and r["severity"] in GATE_SEVERITIES
                and r.get("event_type") in GATE_EVENT_TYPES):
            continue
        mat = r.get("materiality", "not_estimable")
        c = {"kind": "risk", "text": f"new {r['severity']} company-specific risk [{r['event_type']}]: {r['risk']}",
             "materiality": mat, "basis": r.get("materiality_basis"), "fires": True, "why_not": None}
        if mat not in GATE_MATERIALITY and mat != "not_estimable":
            c.update(fires=False, why_not="materiality below 10%")
        elif not _quote_found(r.get("evidence", ""), src):
            c.update(fires=False, why_not="quote not verified in the filing")
        elif not quote_matches_event(r["event_type"], r.get("evidence", "")):
            c.update(fires=False, why_not=f"quote lacks {r['event_type']} language")
        out.append(c)
    cam = result["critical_audit_matters"]
    if cam["prior_year_available"]:
        for x in cam["cams"]:
            if x["new_vs_prior_year"] and x["unusual_for_industry"]:
                mat = x.get("materiality", "not_applicable")
                fires = mat in GATE_MATERIALITY
                out.append({"kind": "cam", "text": f"new unusual CAM: {x['topic']}", "materiality": mat,
                            "basis": None, "fires": fires, "why_not": None if fires else "materiality below 10%"})
    return out


def _describe(c: dict) -> str:
    basis = f" of {c['basis'].replace('_', ' ')}" if c.get("basis") else ""
    why = f"; review: {c['why_not']}" if c.get("why_not") else ""
    return f"{c['text']} ({c['materiality']}{basis}{why})"


def qualitative_gate(result: dict, source: str) -> list[str]:
    """Reasons to move a flag to watch (LLM part of the gate)."""
    return [_describe(c) for c in gate_candidates(result, source) if c["fires"]]


def gate_review(result: dict, source: str) -> list[str]:
    """Gate candidates that did not fire, with the reason."""
    return [_describe(c) for c in gate_candidates(result, source) if not c["fires"]]
