"""Test the holdings tracker: RLS per user, sale validation, and the alert check.

Part 1 (rolled back): as the `authenticated` role with JWT claims for user A (a real auth user) and a
random user B, check that each user can write and read only their own holdings/sales, that alerts are
read-only except `acknowledged`, and that the triggers reject overselling and unknown tickers.
Part 2 (committed, then deleted): a fake lot for user A on a ticker trading above fair value; run
scripts/check_alerts.py; TARGET_HIT must be created once (a second run must not duplicate it); then the
test lot and its alerts are deleted.

Usage: python scripts/test_holdings.py     (exit code 1 on any failure)
"""
import json
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from screener.db import connect  # noqa: E402

results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{f'  ({detail})' if detail else ''}")


class Rollback(Exception):
    pass


def as_user(conn, user_id: str) -> None:
    conn.execute("set local role authenticated")
    conn.execute("select set_config('request.jwt.claims', %s, true)",
                 (json.dumps({"sub": user_id, "role": "authenticated"}),))


def attempt(conn, sql: str, params=()):
    """Run sql in a savepoint; return ('ok', rowcount/rows) or ('error', message)."""
    try:
        with conn.transaction():
            cur = conn.execute(sql, params)
            return "ok", (cur.fetchall() if cur.description else cur.rowcount)
    except psycopg.Error as exc:
        return "error", str(exc).splitlines()[0][:110]


def part1(conn, user_a: str, ticker: str) -> None:
    print("Part 1: RLS and validation (rolled back)")
    user_b = str(uuid.uuid4())
    try:
        with conn.transaction():
            as_user(conn, user_a)
            st, rows = attempt(conn, "insert into holdings (ticker, shares, buy_price) values (%s, 10, 100) "
                                     "returning id, user_id", (ticker,))
            check("A inserts own lot (user_id defaults to auth.uid())", st == "ok" and str(rows[0][1]) == user_a)
            lot_a = rows[0][0] if st == "ok" else None
            st, msg = attempt(conn, "insert into holdings (user_id, ticker, shares, buy_price) values (%s, %s, 1, 1)",
                              (user_b, ticker))
            check("A cannot insert a lot for another user", st == "error", msg)
            st, msg = attempt(conn, "insert into holdings (ticker, shares, buy_price) values ('NOTATICKER', 1, 1)")
            check("unknown ticker rejected", st == "error" and "unknown ticker" in msg, msg)
            st, rows = attempt(conn, "insert into sales (holding_id, shares_sold, sell_price) values (%s, 4, 120) "
                                     "returning id", (lot_a,))
            check("A records a partial sale (4 of 10)", st == "ok")
            st, msg = attempt(conn, "insert into sales (holding_id, shares_sold, sell_price) values (%s, 7, 120)",
                              (lot_a,))
            check("overselling rejected (7 more of 6 remaining)", st == "error" and "cannot sell" in msg, msg)
            st, rows = attempt(conn, "select shares_open, cost_basis from holdings_live where id = %s", (lot_a,))
            check("holdings_live shows 6 open shares", st == "ok" and rows and float(rows[0][0]) == 6,
                  f"{rows}")
            st, rows = attempt(conn, "select realized_gain, lot_closed from sales_realized where holding_id = %s",
                               (lot_a,))
            check("sales_realized: gain $80, lot not closed", st == "ok" and rows and float(rows[0][0]) == 80
                  and rows[0][1] is False, f"{rows}")
            st, rows = attempt(conn, "select open_lots, realized_gain from portfolio_summary")
            check("portfolio_summary for A", st == "ok" and rows and rows[0][0] == 1, f"{rows}")
            st, msg = attempt(conn, "insert into alerts (user_id, holding_id, ticker, alert_type, message) "
                                    "values (%s, %s, %s, 'TARGET_HIT', 'x')", (user_a, lot_a, ticker))
            check("A cannot insert alerts", st == "error", msg)

            # A pipeline alert for A's lot (inserted as postgres), then A's update rights on it.
            conn.execute("reset role")
            alert_id = conn.execute("insert into alerts (user_id, holding_id, ticker, alert_type, message) "
                                    "values (%s, %s, %s, 'APPROACHING', 'test') returning id",
                                    (user_a, lot_a, ticker)).fetchone()[0]
            as_user(conn, user_a)
            st, n = attempt(conn, "update alerts set acknowledged = true where id = %s", (alert_id,))
            check("A acknowledges own alert", st == "ok" and n == 1)
            st, msg = attempt(conn, "update alerts set message = 'edited' where id = %s", (alert_id,))
            check("A cannot change any other alert column", st == "error", msg)
            st, msg = attempt(conn, "delete from alerts where id = %s", (alert_id,))
            check("A cannot delete alerts", st == "error", msg)

            as_user(conn, user_b)
            for rel in ("holdings", "sales", "alerts", "holdings_live", "sales_realized"):
                st, rows = attempt(conn, f"select count(*) from {rel}")
                check(f"B sees none of A's rows in {rel}", st == "ok" and rows[0][0] == 0, f"{rows}")
            st, n = attempt(conn, "update holdings set notes = 'hijack' where id = %s", (lot_a,))
            check("B cannot update A's lot", st == "ok" and n == 0)
            st, n = attempt(conn, "delete from holdings where id = %s", (lot_a,))
            check("B cannot delete A's lot", st == "ok" and n == 0)
            st, msg = attempt(conn, "insert into sales (holding_id, shares_sold, sell_price) values (%s, 1, 1)",
                              (lot_a,))
            check("B cannot record a sale on A's lot", st == "error", msg)
            st, n = attempt(conn, "update alerts set acknowledged = false where id = %s", (alert_id,))
            check("B cannot acknowledge A's alert", st == "ok" and n == 0)
            raise Rollback
    except Rollback:
        pass


def part2(conn, user_a: str) -> None:
    print("\nPart 2: alert check with a fake lot (committed, then deleted)")
    open_before = conn.execute("select count(*) from holdings").fetchone()[0]
    ticker, price, fv = conn.execute(
        "select ticker, price, fair_value from valuations_latest where price >= fair_value and fair_value > 0 "
        "and status = 'no_flag' and not coalesce(qualitative_gate, false) order by price / fair_value desc "
        "limit 1").fetchone()
    lot = conn.execute(
        "insert into holdings (user_id, ticker, shares, buy_price, fair_value_at_buy, notes) "
        "values (%s, %s, 1, %s, %s, 'TEST LOT - delete') returning id",
        (user_a, ticker, round(price * 0.5, 2), fv)).fetchone()[0]
    conn.commit()
    print(f"  test lot {ticker}: price ${price:,.2f} vs fair value ${fv:,.2f}  (other lots in table: {open_before})")
    try:
        for i in (1, 2):
            out = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_alerts.py")],
                                 capture_output=True, text=True)
            print("  run", i, "->", out.stdout.strip().replace("\n", "\n         "))
            if out.returncode:
                print(out.stderr[-1500:])
            rows = conn.execute("select alert_type, price_at_alert, fair_value_at_alert, message, cleared_at "
                                "from alerts where holding_id = %s order by alert_type", (lot,)).fetchall()
            types = [r[0] for r in rows]
            check(f"after run {i}: exactly one TARGET_HIT", types.count("TARGET_HIT") == 1, ", ".join(types))
            if i == 1:
                for r in rows:
                    print(f"     {r[0]:13s} price {r[1]} fv {float(r[2]):.2f}  {r[3]}")
        check("APPROACHING fired alongside (price >= 90% of fair value)", "APPROACHING" in types)
        check("no THESIS_BREAK / DATA_ISSUE on a healthy no_flag name",
              not {"THESIS_BREAK", "DATA_ISSUE"} & set(types))
    finally:
        conn.execute("delete from holdings where id = %s", (lot,))   # alerts cascade
        conn.commit()
        left = conn.execute("select (select count(*) from holdings where notes = 'TEST LOT - delete'), "
                            "(select count(*) from alerts where holding_id = %s)", (lot,)).fetchone()
        check("test lot and its alerts deleted", left == (0, 0), f"{left}")


def main() -> None:
    with connect() as conn:
        users = conn.execute("select id from auth.users order by created_at limit 1").fetchall()
        if not users:
            sys.exit("no auth user exists; create the dashboard login first")
        user_a = str(users[0][0])
        ticker = conn.execute("select ticker from universe order by ticker limit 1").fetchone()[0]
        conn.commit()
        part1(conn, user_a, ticker)
        conn.rollback()
        part2(conn, user_a)
    print(f"\nResult: {'PASS' if all(results) else 'FAIL'} ({sum(results)}/{len(results)} checks)")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
