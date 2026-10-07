"""Rule-based qualitative gate detectors (no LLM) and the event-type quote check for the LLM gate.

Detectors (each logged by name when it fires):
  going_concern       the auditor's report contains "substantial doubt" and "going concern" together
  material_weakness   the auditor's ICFR opinion is adverse, or Item 9A concludes controls are not effective
                      / reports a material weakness
  restatement         an 8-K Item 4.02 (corporate_actions.check) or a 10-K/A with restated XBRL
                      financial data (restated_10ka) filed since the last 10-K
  major_impairment    goodwill/intangible impairment in the latest fiscal year >= 5% of total assets
"""
import re

IMPAIRMENT_THRESHOLD = 0.05

AUDITOR_REPORT = re.compile(r"report\s+of\s+independent\s+registered\s+public\s+accounting\s+firm", re.I)
AUDITOR_REPORT_SPAN = 25_000          # characters scanned after each auditor's report heading
ITEM_9A = re.compile(r"\n[ \t]*item\s*9a\b", re.I)
ITEM_9A_END = re.compile(r"\n[ \t]*item\s*9b\b", re.I)

# The auditor's going-concern paragraph, in the auditor's third-person voice ("its ability", "the
# Company's ability"). Management's accounting-policy wording ("...substantial doubt about our
# ability...", e.g. WTW's going-concern evaluation policy) deliberately does not match.
GOING_CONCERN = re.compile(r"substantial\s+doubt\s+about\s+(its|the\s+\w+['’]s)\s+ability\s+to\s+continue\s+as\s+a\s+going\s+concern",
                           re.I)
AUDITOR_REPORT_END = re.compile(r"we\s+have\s+served\s+as\s+[^.]{0,80}?auditor", re.I)
# Formal adverse conclusions only. Clean reports' definition sentence ("A material weakness is a
# deficiency...") and mentions of acquired companies' or remediated past weaknesses ("Prior to
# the acquisition, X reported an identified material weakness") deliberately do not match.
MATERIAL_WEAKNESS = re.compile(
    r"(did\s+not\s+maintain,?\s+in\s+all\s+material\s+respects,?\s+effective\s+internal\s+control"
    r"|material\s+weakness(es)?\s+(has|have)\s+been\s+identified\s+and\s+included\s+in\s+management"
    r"|(internal\s+control\s+over\s+financial\s+reporting|disclosure\s+controls\s+and\s+procedures)"
    r"\s+(was|were)\s+not\s+effective)", re.I)


def _auditor_text(text: str) -> str:
    """Each auditor's report from its heading to its sign-off (capped), so notes to the financial
    statements that follow are not scanned."""
    spans = []
    for m in AUDITOR_REPORT.finditer(text):
        end = AUDITOR_REPORT_END.search(text, m.end(), m.start() + AUDITOR_REPORT_SPAN)
        spans.append(text[m.start(): end.end() if end else m.start() + AUDITOR_REPORT_SPAN])
    return "\n".join(spans)


def _item_9a(text: str) -> str:
    best = ""
    for m in ITEM_9A.finditer(text):
        e = ITEM_9A_END.search(text, m.end())
        span = text[m.start(): e.start() if e else m.start() + 30_000]
        best = span if len(span) > len(best) else best
    return best


def _snippet(m: re.Match, text: str) -> str:
    return re.sub(r"\s+", " ", text[max(0, m.start() - 80): m.end() + 80]).strip()


def text_detectors(text: str) -> dict:
    """Going-concern and material-weakness detectors on a 10-K's full text."""
    auditor = _auditor_text(text)
    out = {"going_concern": False, "material_weakness": False, "evidence": []}
    m = GOING_CONCERN.search(auditor)
    if m:
        out["going_concern"] = True
        out["evidence"].append(f"going_concern: {_snippet(m, auditor)}")
    for source, body in (("auditor ICFR opinion", auditor), ("Item 9A", _item_9a(text))):
        m = MATERIAL_WEAKNESS.search(body)
        if m:
            out["material_weakness"] = True
            out["evidence"].append(f"material_weakness ({source}): {_snippet(m, body)}")
            break
    return out


# ---- event-type quote check for the LLM gate ----
EVENT_KEYWORDS = {
    "investigation_or_enforcement": r"investigat|subpoena|inquir|probe|enforcement|civil investigative demand|indict|prosecut",
    "adverse_verdict_or_judgment": r"verdict|judgment|jury|damages|ruled|ruling|award",
    "material_weakness": r"material weakness",
    "going_concern_doubt": r"going concern|substantial doubt",
    "covenant_breach_or_default": r"breach|default|waiver",
    "loss_of_key_customer_or_contract": r"terminat|loss of|lost|non-?renew|not renew|cancel",
    "major_impairment": r"impair|write-?down|written down|write-?off",
    "recall": r"recall|withdraw|safety",
}


def restated_10ka(facts, latest_10k_filed: dict) -> dict:
    """{cik: evidence} for companies with a 10-K/A filed after their latest original 10-K that
    reports financial-statement XBRL facts. Part III-only amendments carry only cover-page data
    (the dei share count), so they don't count."""
    f = facts[(facts["form"] == "10-K/A") & (facts["concept"] != "shares_outstanding")]
    out = {}
    for cik, grp in f.groupby("cik"):
        last = latest_10k_filed.get(cik)
        later = grp[grp["filed"] > last] if isinstance(last, str) else grp.iloc[0:0]
        if len(later):
            concepts = ", ".join(sorted(later["concept"].unique())[:4])
            out[cik] = f"10-K/A filed {later['filed'].max()} restates XBRL financial data ({concepts})"
    return out


def quote_matches_event(event_type: str, quote: str) -> bool:
    pattern = EVENT_KEYWORDS.get(event_type)
    return bool(pattern and re.search(pattern, quote or "", re.I))
