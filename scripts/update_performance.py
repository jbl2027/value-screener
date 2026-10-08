"""Milestone 10: paper tracking of flags against SPY (records only; nothing is traded).

1. Flag episodes, rebuilt from screen_history (so the first run is also the backfill): an episode
   starts on the first screen date a company is flagged and ends on the first later screen date
   with any other status (exit_status), or when it is missing from a screen (not_in_screen).
   Entry and exit prices are that day's screen close. entry_composite_tier ranks the entry
   composite among all flags on the entry date (top half / bottom half).
2. Adjusted closes (dividends and splits, Tiingo EOD adjClose) for SPY and every ticker with an
   open episode, or a closed one still missing data, from its first entry date to today: one
   request per ticker (free tier: 50 requests/hour, 500 unique symbols/month; capped per run).
   Re-fetching the whole range each day keeps adjusted values consistent after new dividends.
3. SPY closes for screen dates missing from prices (backfill before SPY joined the bulk capture).

Usage: python scripts/update_performance.py [--no-fetch]
"""
import argparse
import math
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.db import connect  # noqa: E402
from screener.tiingo import TiingoClient  # noqa: E402

BENCHMARK = "SPY"
MAX_EOD_REQUESTS = 40   # per run; below the free tier's 50/hour
EPISODE_COLS = ["cik", "ticker", "entry_date", "entry_price", "entry_fair_value", "entry_mos", "entry_composite",
                "entry_composite_tier", "entry_flag_count", "exit_date", "exit_price", "exit_status", "exit_reason"]


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def build_episodes(hist: pd.DataFrame, latest_reasons: dict[int, str], latest_as_of) -> list[dict]:
    """Episodes from the full screen history (one row per as_of and cik)."""
    dates = sorted(hist["as_of"].unique())
    by_day = {d: g.set_index("cik") for d, g in hist.groupby("as_of")}
    # Composite tier among that day's flags.
    tiers, counts = {}, {}
    for d, g in by_day.items():
        flags = g[g["status"] == "flag"]
        counts[d] = len(flags)
        ranked = flags["composite"].dropna().rank(ascending=False, method="first")
        half = math.ceil(len(ranked) / 2)
        for cik, rk in ranked.items():
            tiers[(d, cik)] = "top_half" if rk <= half else "bottom_half"

    episodes = []
    for cik in hist["cik"].unique():
        open_ep = None
        for d in dates:
            day = by_day[d]
            row = day.loc[cik] if cik in day.index else None
            status = row["status"] if row is not None else "not_in_screen"
            if status == "flag" and open_ep is None:
                open_ep = {"cik": int(cik), "ticker": row["ticker"], "entry_date": d,
                           "entry_price": _num(row["price"]), "entry_fair_value": _num(row["fair_value"]),
                           "entry_mos": _num(row["mos"]), "entry_composite": _num(row["composite"]),
                           "entry_composite_tier": tiers.get((d, cik)), "entry_flag_count": counts[d],
                           "exit_date": None, "exit_price": None, "exit_status": None, "exit_reason": None}
            elif status != "flag" and open_ep is not None:
                open_ep.update(exit_date=d, exit_price=_num(row["price"]) if row is not None else None,
                               exit_status=status,
                               exit_reason=latest_reasons.get(int(cik)) if d == latest_as_of else None)
                episodes.append(open_ep)
                open_ep = None
        if open_ep is not None:
            episodes.append(open_ep)
    return episodes


def save_episodes(conn, episodes: list[dict]) -> None:
    cols = ", ".join(EPISODE_COLS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in EPISODE_COLS[2:] if c != "exit_reason")
    with conn.cursor() as cur:
        cur.executemany(
            f"insert into flag_episodes ({cols}) values ({', '.join(['%s'] * len(EPISODE_COLS))}) "
            f"on conflict (cik, entry_date) do update set {updates}, "
            f"exit_reason = coalesce(excluded.exit_reason, case when excluded.exit_date is not distinct from "
            f"flag_episodes.exit_date then flag_episodes.exit_reason end), updated_at = now()",
            [[e[c] for c in EPISODE_COLS] for e in episodes])


def fetch_adjusted(conn, client: TiingoClient, today: date) -> dict:
    """EOD adjusted closes for SPY + tickers whose episodes need data, from the earliest entry date."""
    need = conn.execute(
        "select e.ticker, min(e.entry_date) from flag_episodes e "
        "where e.exit_date is null "
        "   or not exists (select 1 from eod_adjusted a where a.ticker = e.ticker and a.date = e.entry_date) "
        "   or not exists (select 1 from eod_adjusted a where a.ticker = e.ticker and a.date = e.exit_date) "
        "group by e.ticker order by min(e.entry_date)").fetchall()
    if not need:
        return {"requests": 0}
    first = min(d for _, d in need)
    jobs = [(BENCHMARK, first)] + [(t, d) for t, d in need if t != BENCHMARK]
    done, failed, skipped = 0, [], [t for t, _ in jobs[MAX_EOD_REQUESTS:]]
    for ticker, start in jobs[:MAX_EOD_REQUESTS]:
        try:
            rows = client.eod_prices(ticker, start, today)
        except Exception as exc:  # rate limit or unknown symbol: keep going, report
            failed.append(f"{ticker}: {exc}"[:120])
            if "rate limit" in str(exc):
                skipped += [t for t, _ in jobs[jobs.index((ticker, start)) + 1:MAX_EOD_REQUESTS]]
                break
            continue
        with conn.cursor() as cur:
            cur.executemany(
                "insert into eod_adjusted (ticker, date, close, adj_close, div_cash, split_factor) "
                "values (%s, %s, %s, %s, %s, %s) on conflict (ticker, date) do update set close = excluded.close, "
                "adj_close = excluded.adj_close, div_cash = excluded.div_cash, split_factor = excluded.split_factor, "
                "fetched_at = now()",
                [(ticker, r["date"][:10], r.get("close"), r.get("adjClose"), r.get("divCash"), r.get("splitFactor"))
                 for r in rows])
        done += 1
    # SPY close in prices for screen dates captured before SPY joined the bulk snapshot.
    conn.execute(
        "insert into prices (ticker, date, close, source, fetched_at) "
        "select a.ticker, a.date, a.close, 'tiingo_eod', a.fetched_at from eod_adjusted a "
        "where a.ticker = %s and a.date in (select distinct as_of from screen_history) "
        "on conflict (ticker, date) do nothing", (BENCHMARK,))
    return {"requests": done, "failed": failed, "skipped_rate_cap": skipped}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch", action="store_true", help="rebuild episodes only (no Tiingo requests)")
    args = parser.parse_args()
    with connect() as conn:
        hist = pd.DataFrame(conn.execute(
            "select as_of, cik, ticker, status, price, fair_value, mos, composite from screen_history").fetchall(),
            columns=["as_of", "cik", "ticker", "status", "price", "fair_value", "mos", "composite"])
        latest_as_of = hist["as_of"].max()
        reasons = dict(conn.execute("select cik, status_reason from valuations_latest where as_of = %s",
                                    (latest_as_of,)).fetchall())
        episodes = build_episodes(hist, reasons, latest_as_of)
        save_episodes(conn, episodes)
        conn.commit()
        n_open = sum(e["exit_date"] is None for e in episodes)
        print(f"Screen dates {hist['as_of'].nunique()} ({hist['as_of'].min()} .. {latest_as_of}); "
              f"episodes {len(episodes)} ({n_open} open, {len(episodes) - n_open} closed)")
        fetch = {"requests": 0} if args.no_fetch else fetch_adjusted(conn, TiingoClient(), date.today())
        conn.commit()
        print(f"Adjusted EOD: {fetch}")
        summary = conn.execute("select group_state, group_tier, episodes, avg_excess_return, median_excess_return, "
                               "hit_rate, total_return_episodes, price_only_episodes from flag_performance_summary "
                               "order by 1, 2").fetchall()
        for s in summary:
            print("  ", s)
        conn.execute("insert into runs (job, as_of, finished_at, status, summary) values "
                     "('performance', %s, now(), 'ok', jsonb_build_object('episodes', %s, 'open', %s, "
                     "'eod_requests', %s))", (latest_as_of, len(episodes), n_open, fetch["requests"]))
        conn.commit()


if __name__ == "__main__":
    main()
