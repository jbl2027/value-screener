"""Dev helper: list which XBRL tags matching a pattern each company uses, with 10-K fact counts.

Usage: python scripts/explore_tags.py PATTERN TICKER [TICKER ...]
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.edgar import load_companyfacts, related_ciks  # noqa: E402
from screener.sec_client import SECClient  # noqa: E402


def show(cf: dict, pattern: re.Pattern) -> None:
    for taxonomy, tags in cf.get("facts", {}).items():
        for tag, node in tags.items():
            if not pattern.search(tag):
                continue
            for unit, facts in node["units"].items():
                tenk = [f for f in facts if f.get("form") == "10-K"]
                latest = max((f["end"] for f in tenk), default="-")
                print(f"  {taxonomy}:{tag} [{unit}] 10-K facts={len(tenk)} latest_end={latest}")


def main() -> None:
    pattern = re.compile(sys.argv[1], re.IGNORECASE)
    tickers = [t.upper() for t in sys.argv[2:]]
    universe = pd.read_csv(DATA_DIR / "universe.csv")
    sec = SECClient()
    for ticker in tickers:
        cik = int(universe.loc[universe["ticker"] == ticker, "cik"].iloc[0])
        for source_cik in related_ciks(cik):
            print(f"\n== {ticker} (CIK {source_cik}) ==")
            show(load_companyfacts(source_cik, sec), pattern)


if __name__ == "__main__":
    main()
