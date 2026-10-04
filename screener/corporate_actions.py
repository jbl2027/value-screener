"""Detect pending corporate actions from the EDGAR submissions API.

A company is `corporate_action_pending` when it filed an 8-K (or 8-K/A) reporting Item 2.01
(completion of acquisition or disposition of assets) within the lookback window and has not
filed a periodic report (10-Q or 10-K) since. Until then, its XBRL financials predate the
transaction while its price reflects it.
"""
from datetime import date, timedelta

from screener.edgar import related_ciks
from screener.sec_client import SECClient

PERIODIC_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}


def check(cik: int, sec: SECClient, as_of: str, lookback_days: int) -> dict:
    cutoff = (date.fromisoformat(as_of) - timedelta(days=lookback_days)).isoformat()
    events, periodic = [], []
    for source_cik in related_ciks(cik):
        recent = sec.submissions(source_cik)["filings"]["recent"]
        for form, filed, items, accn in zip(recent["form"], recent["filingDate"],
                                            recent.get("items", [""] * len(recent["form"])),
                                            recent["accessionNumber"]):
            if filed > as_of:
                continue
            if form in ("8-K", "8-K/A") and "2.01" in (items or "") and filed >= cutoff:
                events.append((filed, accn))
            elif form in PERIODIC_FORMS:
                periodic.append((filed, form))
    if not events:
        return {"cik": cik, "corporate_action_pending": False}
    last_event, accn = max(events)
    cleared = [p for p in periodic if p[0] > last_event]
    return {
        "cik": cik,
        "corporate_action_pending": not cleared,
        "item_2_01_filed": last_event,
        "item_2_01_accn": accn,
        "cleared_by": f"{min(cleared)[1]} {min(cleared)[0]}" if cleared else None,
    }
