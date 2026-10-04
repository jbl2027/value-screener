"""Detect corporate actions from the EDGAR submissions API.

Event: an 8-K (or 8-K/A) reporting Item 2.01 (completion of acquisition or disposition), or a
manual entry in rules.MANUAL_CORPORATE_ACTIONS (spin-offs are sometimes reported under Item
8.01 instead, e.g. Comcast/Versant). For the latest event this returns:
  pending        the event is within the lookback window and no 10-Q/10-K covering a period
                 ending >= 30 days after it has been filed (financials predate the transaction
                 while the price reflects it)
  first periodic the first 10-Q/10-K filed after the event (the valuation step decides the
                 post-event regime by comparing the event with the latest 10-K's fiscal year end)
"""
from datetime import date, timedelta

from screener.edgar import related_ciks
from screener.sec_client import SECClient

PERIODIC_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}
# A periodic report only reflects the event if its period ends well after it: HON's spin 8-K
# was filed 2026-06-29, but its Q2 10-Q (period ending 06-30) still consolidates the business.
MIN_DAYS_PERIOD_AFTER_EVENT = 30


def check(cik: int, sec: SECClient, as_of: str, lookback_days: int,
          manual_event: tuple[str, str] | None = None) -> dict:
    cutoff = (date.fromisoformat(as_of) - timedelta(days=lookback_days)).isoformat()
    events, periodic = [], []
    for source_cik in related_ciks(cik):
        recent = sec.submissions(source_cik)["filings"]["recent"]
        for form, filed, items, period in zip(recent["form"], recent["filingDate"],
                                              recent.get("items", [""] * len(recent["form"])),
                                              recent["reportDate"]):
            if filed > as_of:
                continue
            if form in ("8-K", "8-K/A") and "2.01" in (items or ""):
                events.append((filed, "8-K Item 2.01"))
            elif form in PERIODIC_FORMS:
                periodic.append((filed, form, period))
    if manual_event and manual_event[0] <= as_of:
        events.append((manual_event[0], f"manual: {manual_event[1]}"))
    if not events:
        return {"cik": cik, "corporate_action_pending": False}
    event_date, source = max(events)
    min_period = (date.fromisoformat(event_date) + timedelta(days=MIN_DAYS_PERIOD_AFTER_EVENT)).isoformat()
    after = sorted(p for p in periodic if p[0] > event_date and (p[2] or "") >= min_period)
    return {
        "cik": cik,
        "corporate_action_pending": not after and event_date >= cutoff,
        "event_filed": event_date,
        "event_source": source,
        "first_periodic_after": f"{after[0][1]} {after[0][0]}" if after else None,
    }
