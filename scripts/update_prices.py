"""Milestone 4: daily closing prices for the universe (and SPY, the Milestone 10 benchmark) from
one Tiingo bulk IEX request.

Run after the US close (the GitHub Actions schedule will do this on weekdays). A row is stored
only when the snapshot timestamp is at or after 16:00 New York time, i.e. the session closed
and tngoLast is the official close. Re-running for the same session is idempotent.

Output: data/prices.parquet (gitignored; loaded to Supabase in Milestone 9)

Usage: python scripts/update_prices.py [--verify N]
  --verify N  cross-check N tickers' stored close against the EOD endpoint (uses N of the
              500 unique symbols/month on the free tier)
"""
import argparse
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.tiingo import TiingoClient  # noqa: E402

PRICES_PATH = DATA_DIR / "prices.parquet"
NEW_YORK = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)
KEY = ["ticker", "date"]
BENCHMARKS = {"SPY"}  # stored alongside the universe (performance tracking benchmark)


def snapshot_rows(snapshot: list[dict], tickers: set[str]) -> tuple[pd.DataFrame, list[dict]]:
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows, skipped = [], []
    for d in snapshot:
        ticker = d["ticker"].upper()
        if ticker not in tickers:
            continue
        if d.get("tngoLast") is None or not d.get("timestamp"):
            skipped.append({"ticker": ticker, "reason": "no price"})
            continue
        ts = datetime.fromisoformat(d["timestamp"]).astimezone(NEW_YORK)
        if ts.time() < MARKET_CLOSE:
            skipped.append({"ticker": ticker, "reason": f"session not closed ({ts:%Y-%m-%d %H:%M} ET)"})
            continue
        rows.append({
            "ticker": ticker, "date": ts.date().isoformat(), "close": d["tngoLast"],
            "open": d.get("open"), "high": d.get("high"), "low": d.get("low"),
            "volume": d.get("volume"), "prev_close": d.get("prevClose"),
            "source": "tiingo_iex", "fetched_at": fetched_at,
        })
    return pd.DataFrame(rows), skipped


def save(new: pd.DataFrame) -> pd.DataFrame:
    if PRICES_PATH.exists():
        new = pd.concat([pd.read_parquet(PRICES_PATH), new], ignore_index=True)
    out = new.drop_duplicates(KEY, keep="last").sort_values(KEY).reset_index(drop=True)
    out.to_parquet(PRICES_PATH, index=False)
    return out


def verify(client: TiingoClient, rows: pd.DataFrame, n: int) -> None:
    sample = rows.sample(n=min(n, len(rows)), random_state=0)
    print(f"\nVerifying {len(sample)} closes against the EOD endpoint:")
    for r in sample.itertuples():
        day = datetime.fromisoformat(r.date).date()
        eod = client.eod_prices(r.ticker, day, day)
        eod_close = eod[0]["close"] if eod else None
        status = "OK" if eod_close is not None and abs(eod_close - r.close) < 0.005 else "MISMATCH"
        print(f"  {r.ticker:6s} {r.date}  snapshot {r.close:>10.2f}  eod {eod_close}  {status}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", type=int, default=0)
    args = parser.parse_args()

    universe = pd.read_csv(DATA_DIR / "universe.csv")
    tickers = set(universe["ticker"]) | BENCHMARKS
    client = TiingoClient()

    print("Fetching Tiingo IEX snapshot (all tickers, 1 request)...")
    snapshot = client.iex_snapshot()
    rows, skipped = snapshot_rows(snapshot, tickers)
    print(f"  {len(snapshot):,} tickers in snapshot; {len(rows)} universe closes; {len(skipped)} skipped")

    found = set(rows["ticker"]) | {s["ticker"] for s in skipped}
    missing = sorted(tickers - found)
    if missing:
        print(f"Not in snapshot ({len(missing)}): {' '.join(missing)}")
    if skipped:
        print("Skipped:")
        for s in skipped:
            print(f"  {s['ticker']}: {s['reason']}")

    if rows.empty:
        print("No closed-session prices to store.")
        return
    latest = rows["date"].max()
    stale = rows[rows["date"] < latest]
    print(f"Session date: {latest}")
    if len(stale):
        print(f"Older than {latest} (halted, delisted, or renamed?): "
              + " ".join(f"{t}@{d}" for t, d in zip(stale["ticker"], stale["date"])))

    all_prices = save(rows)
    print(f"Saved. prices.parquet: {len(all_prices):,} rows, {all_prices['date'].nunique()} sessions, "
          f"{all_prices['ticker'].nunique()} tickers")

    if args.verify:
        verify(client, rows[rows["date"] == latest], args.verify)


if __name__ == "__main__":
    main()
