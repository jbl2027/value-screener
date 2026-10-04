"""Scan 8-K Item 8.01 filings for completed spin-offs/separations (candidates for manual review).

Spin-offs are sometimes announced under Item 8.01 rather than 2.01 (Comcast/Versant), which
the automatic corporate-action check misses. This lists candidates only; approved events are
added by hand to rules.MANUAL_CORPORATE_ACTIONS.

Output: data/spinoff_candidates.csv
Usage:  python scripts/scan_spinoffs.py [--days 365]
"""
import argparse
import html
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.edgar import related_ciks  # noqa: E402
from screener.rules import MANUAL_CORPORATE_ACTIONS  # noqa: E402
from screener.sec_client import SECClient  # noqa: E402

SEPARATION = r"(separation|spin-?off|spin off|split-?off)"
COMPLETION = r"(complet\w*|consummat\w*|effected)"
# Sentence-level: a completion word and a separation word together, or "completed the
# distribution of ... shares of <another company>".
PATTERNS = [
    re.compile(rf"[^.]{{0,300}}{COMPLETION}[^.]{{0,200}}{SEPARATION}[^.]{{0,300}}", re.I),
    re.compile(rf"[^.]{{0,300}}{SEPARATION}[^.]{{0,200}}{COMPLETION}[^.]{{0,300}}", re.I),
    re.compile(rf"[^.]{{0,300}}{COMPLETION}[^.]{{0,80}}distribution of[^.]{{0,200}}(shares|common stock)[^.]{{0,300}}", re.I),
]
# Phrases that indicate a plan, not a completed event.
PROGRESS = DATA_DIR / "cache" / "spinoff_scan_progress.csv"  # gitignored (data/cache/)
FORWARD = re.compile(r"\b(expected to|will be completed|intends? to|plans? to|anticipat\w+|is expected)\b", re.I)


def text_of(sec: SECClient, cik: int, accn: str, doc: str) -> str:
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{doc}"
    sec._throttle()
    resp = sec._session.get(url, timeout=60)
    resp.raise_for_status()
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", resp.text)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--count-only", action="store_true")
    args = parser.parse_args()
    cutoff = (date.today() - timedelta(days=args.days)).isoformat()

    universe = pd.read_csv(DATA_DIR / "universe.csv").drop_duplicates("cik")
    sec = SECClient()
    filings = []
    for r in universe.itertuples():
        for source_cik in related_ciks(int(r.cik)):
            recent = sec.submissions(source_cik)["filings"]["recent"]
            for form, filed, items, accn, doc in zip(recent["form"], recent["filingDate"], recent["items"],
                                                     recent["accessionNumber"], recent["primaryDocument"]):
                if form in ("8-K", "8-K/A") and "8.01" in (items or "") and filed >= cutoff:
                    filings.append((r.ticker, source_cik, filed, items, accn, doc))
    print(f"{len(filings)} Item 8.01 8-Ks since {cutoff}; scanning text...", flush=True)
    if args.count_only:
        return

    # Resumable: every scanned filing is appended to a progress file as it completes.
    PROGRESS.parent.mkdir(exist_ok=True)
    done = set(pd.read_csv(PROGRESS)["accn"]) if PROGRESS.exists() else set()
    for i, (ticker, cik, filed, items, accn, doc) in enumerate(filings, 1):
        if accn in done:
            continue
        row = {"ticker": ticker, "cik": cik, "filed": filed, "items": items, "accn": accn,
               "already_manual": ticker in MANUAL_CORPORATE_ACTIONS, "snippet": None, "error": None}
        try:
            text = text_of(sec, cik, accn, doc)
            hits = [m.group(0).strip() for p in PATTERNS for m in p.finditer(text)]
            completed = [h for h in hits if not FORWARD.search(h)]
            row["snippet"] = completed[0][:400] if completed else None
        except Exception as exc:
            row["error"] = type(exc).__name__
        pd.DataFrame([row]).to_csv(PROGRESS, mode="a", header=not PROGRESS.exists(), index=False)
        if i % 100 == 0:
            print(f"  {i}/{len(filings)}", flush=True)

    out = pd.read_csv(PROGRESS)
    out = out[out["filed"] >= cutoff]
    out[out["snippet"].notna()].to_csv(DATA_DIR / "spinoff_candidates.csv", index=False)
    cands = out[out["snippet"].notna()]
    print(f"\n{len(out)} filings scanned: {len(cands)} candidates ({cands['ticker'].nunique()} companies); "
          f"{int(out['error'].notna().sum())} fetch errors")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 200)
    if len(cands):
        print(cands[["ticker", "filed", "already_manual", "snippet"]].to_string(index=False))


if __name__ == "__main__":
    main()
