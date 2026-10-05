"""Fetch 10-K documents from EDGAR and extract Risk Factors, MD&A, and Critical Audit Matters.

Section boundaries are found from headings. A 10-K's table of contents repeats every heading,
so for each section the candidate with the longest span to its end heading wins. Filers that
use a cross-reference index instead of "Item" numbering fall back to plain headings.
"""
import html
import re

from screener.edgar import related_ciks
from screener.sec_client import SECClient

BLOCK_TAGS = re.compile(r"</?(p|div|br|tr|li|h[1-6]|table|section)[^>]*>", re.I)

def _h(phrase: str) -> str:
    """Heading regex tolerant of small-caps styling that splits words ("R ISK F ACTORS")."""
    words = [r"\s?".join(re.escape(ch) for ch in w) for w in phrase.split()]
    return r"\s+".join(words)


LINE = r"\n[ \t]*"  # headings start a line; mid-sentence references ("see Item 7...") don't count
SECTION_PATTERNS = {
    "risk_factors": (
        [LINE + r"item\s*1a\.?[\s\-–—:]*" + _h("risk factors"), LINE + _h("risk factors") + r"[ \t]*\n"],
        [LINE + r"item\s*1b\b", LINE + r"item\s*1c\b", LINE + r"item\s*2\b", LINE + r"item\s*3\b",
         LINE + _h("unresolved staff comments"), LINE + _h("cybersecurity") + r"[ \t]*\n",
         LINE + _h("properties") + r"[ \t]*\n", LINE + _h("legal proceedings") + r"[ \t]*\n",
         LINE + _h("quantitative and qualitative disclosures"), LINE + _h("report of independent registered")],
    ),
    "mdna": (
        [LINE + r"item\s*7\.?[\s\-–—:]*" + _h("management") + r"['’]?\s?s\s+" + _h("discussion"),
         LINE + _h("management") + r"['’]?\s?s\s+" + _h("discussion and analysis")],
        [LINE + r"item\s*7a\b", LINE + r"item\s*8\b", LINE + _h("quantitative and qualitative disclosures"),
         LINE + _h("management") + r"['’]?\s?s\s+" + _h("report on internal control"),
         LINE + _h("report of independent registered"), LINE + _h("controls and procedures"),
         LINE + _h("consolidated statements of income"), LINE + _h("consolidated statement of income")],
    ),
}
MIN_SECTION_CHARS = 5_000
CAM_START = re.compile(r"(the\s+critical\s+audit\s+matters?\s+communicated\s+below"
                       r"|critical\s+audit\s+matters?\s+are\s+matters?\s+arising)", re.I)
# "the Company's auditor", "NEE's and FPL's auditor", "the Company's or its predecessor's auditor"
CAM_END = re.compile(r"we\s+have\s+served\s+as\s+[^.]{0,80}?auditor", re.I)
NO_CAM = re.compile(r"(there\s+are\s+no|we\s+determined\s+that\s+there\s+are\s+no)\s+critical\s+audit\s+matters", re.I)


def to_text(raw_html: str) -> str:
    text = BLOCK_TAGS.sub("\n", raw_html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text)


def ten_k_filings(cik: int, sec: SECClient, as_of: str, n: int = 2) -> list[dict]:
    """The latest n original 10-Ks filed on or before as_of (current CIK first, then predecessors)."""
    out = []
    for source_cik in related_ciks(cik):
        sub = sec.submissions(source_cik)
        pages = [sub["filings"]["recent"]]
        # "recent" holds only the last 1,000 filings; heavy filers (e.g. JPM's prospectus
        # supplements) push older 10-Ks into the paged history files.
        older = [f["name"] for f in sub["filings"].get("files", [])]
        while True:
            for r in pages:
                for form, filed, accn, doc, period in zip(r["form"], r["filingDate"], r["accessionNumber"],
                                                          r["primaryDocument"], r["reportDate"]):
                    if form == "10-K" and filed <= as_of:
                        out.append({"cik": source_cik, "filed": filed, "accn": accn, "doc": doc, "period": period})
            if sum(f["cik"] == source_cik for f in out) >= n or not older:
                break
            pages = [sec.get_json(f"https://data.sec.gov/submissions/{older.pop(0)}")]
    return sorted(out, key=lambda f: f["filed"], reverse=True)[:n]


def fetch_text(f: dict, sec: SECClient) -> str:
    url = f"https://www.sec.gov/Archives/edgar/data/{f['cik']}/{f['accn'].replace('-', '')}/{f['doc']}"
    return to_text(sec.get_text(url))


def _longest_span(text: str, starts: list[str], ends: list[str]) -> str:
    end_re = re.compile("|".join(ends), re.I)
    best = ""
    for pattern in starts:
        for m in re.finditer(pattern, text, re.I):
            e = end_re.search(text, m.end())
            span = text[m.start(): e.start() if e else len(text)]
            if e and len(span) > len(best):
                best = span
        if len(best) >= MIN_SECTION_CHARS:
            break  # primary (Item-numbered) pattern worked; don't use the fallback
    return best.strip()


def extract_sections(text: str) -> dict:
    sections = {name: _longest_span(text, s, e) for name, (s, e) in SECTION_PATTERNS.items()}
    m = CAM_START.search(text)
    if m:
        e = CAM_END.search(text, m.end())
        sections["cam"] = text[m.start(): e.start() if e else m.start() + 20_000].strip()
    elif NO_CAM.search(text):
        sections["cam"] = "The auditor's report states that there are no critical audit matters."
    else:
        sections["cam"] = ""
    return sections
