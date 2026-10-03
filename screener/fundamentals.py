"""Extract point-in-time fundamental facts from EDGAR companyfacts using the tag fallback map.

Output is long format: one row per (concept, period, distinct reported value), with the
filing that first reported it. A later filing that repeats the same value is dropped (no new
information); a later filing that reports a *different* value for the same period is kept as
a restatement, with its own `filed` date. This preserves exactly what was knowable when.

Note: companyfacts `fy`/`fp` describe the *filing*, not the fact's period (a FY2025 10-K's
prior-year comparatives also carry fy=2025). Use period_start/period_end to identify periods.

Note: a ~365-day duration is not always a fiscal year. Some filers (e.g. AMZN) report
trailing-twelve-month figures in 10-Qs. Identify fiscal years from 10-K-family filings.
"""
from datetime import date

import pandas as pd

from screener.edgar import load_companyfacts, related_ciks
from screener.sec_client import SECClient
from screener.tag_map import TAG_MAP

HISTORY_START = "2014-01-01"  # ~10 fiscal years: enough for 5-yr R&D capitalization and 3-yr averages
FORMS = ("10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A")

FACT_COLUMNS = ["cik", "source_cik", "concept", "taxonomy", "tag", "unit", "value", "period_start",
                "period_end", "duration_days", "fy", "fp", "form", "filed", "accn", "frame"]


def _tag_facts(facts: dict, taxonomy: str, tag: str, unit: str) -> list[dict]:
    """Facts for a tag, or for a sum tag "A+B" (A required; B added from the same filing/period)."""
    parts = tag.split("+")
    nodes = [facts.get(taxonomy, {}).get(p) for p in parts]
    if not nodes[0] or unit not in nodes[0]["units"]:
        return []
    base = nodes[0]["units"][unit]
    if len(parts) == 1:
        return base
    addends: dict[tuple, float] = {}
    for node in nodes[1:]:
        for f in (node or {}).get("units", {}).get(unit, []):
            key = (f["accn"], f.get("start"), f["end"])
            addends[key] = addends.get(key, 0) + f["val"]
    return [{**f, "val": f["val"] + addends.get((f["accn"], f.get("start"), f["end"]), 0)} for f in base]


def _raw_facts(cf: dict, source_cik: int) -> list[dict]:
    rows = []
    facts = cf.get("facts", {})
    for concept, (unit, period_type, candidates) in TAG_MAP.items():
        for tag_rank, (taxonomy, tag) in enumerate(candidates):
            for f in _tag_facts(facts, taxonomy, tag, unit):
                if f.get("form") not in FORMS or f["end"] < HISTORY_START:
                    continue
                start = f.get("start")
                if (period_type == "duration") != (start is not None):
                    continue
                duration = (date.fromisoformat(f["end"]) - date.fromisoformat(start)).days if start else None
                rows.append({
                    "source_cik": source_cik, "concept": concept, "taxonomy": taxonomy, "tag": tag,
                    "unit": unit, "value": f["val"], "period_start": start, "period_end": f["end"],
                    "duration_days": duration, "fy": f.get("fy"), "fp": f.get("fp"), "form": f["form"],
                    "filed": f["filed"], "accn": f["accn"], "frame": f.get("frame"),
                    "_tag_rank": tag_rank,
                })
    return rows


def extract_facts(cik: int, sec: SECClient) -> pd.DataFrame:
    rows = []
    for source_cik in related_ciks(cik):
        rows += _raw_facts(load_companyfacts(source_cik, sec), source_cik)
    if not rows:
        return pd.DataFrame(columns=FACT_COLUMNS)

    df = pd.DataFrame(rows)
    df["cik"] = cik
    period = ["concept", "period_start", "period_end"]
    # Within each filing, take the highest-priority tag for each period. Resolving per filing
    # (not per period) keeps a 10-K's value even if a later 10-Q reports it under another tag.
    best = df.groupby(period + ["accn"], dropna=False)["_tag_rank"].transform("min")
    df = df[df["_tag_rank"] == best]
    # Keep the first filing of each distinct value (restatements survive as separate rows).
    df = df.sort_values(["filed", "accn"]).drop_duplicates(period + ["value"], keep="first")
    return df[FACT_COLUMNS].sort_values(["concept", "period_end", "filed"]).reset_index(drop=True)


def latest_annual_snapshot(facts: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Values as reported in the company's most recent original 10-K, for tie-out to the document.

    Returns (filing_info, rows) where rows has one row per concept found in that 10-K.
    """
    tenk = facts[facts["form"] == "10-K"]
    if tenk.empty:
        return {}, pd.DataFrame()
    accn = tenk.sort_values("filed")["accn"].iloc[-1]
    in_filing = facts[facts["accn"] == accn]
    fy_end = in_filing.loc[in_filing["concept"] == "total_assets", "period_end"].max()
    if pd.isna(fy_end):
        fy_end = in_filing["period_end"].max()
    annual = in_filing[(in_filing["period_end"] == fy_end)
                       & (in_filing["duration_days"].isna() | in_filing["duration_days"].between(350, 380))]
    # Cover-page share count is dated near the filing date, not the fiscal year end.
    shares = in_filing[in_filing["concept"] == "shares_outstanding"].sort_values("period_end").tail(1)
    rows = pd.concat([annual[annual["concept"] != "shares_outstanding"], shares])
    info = {"accn": accn, "filed": in_filing["filed"].iloc[0], "fy_end": fy_end}
    return info, rows.drop_duplicates("concept", keep="last").set_index("concept")
