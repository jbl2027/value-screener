"""Sell / review alerts for open holdings lots (tracking only: alerts are rows in Supabase shown on the
dashboard; nothing is sent and no orders are placed).

For every open lot, compares the latest screen (valuations_latest) with the lot:
  TARGET_HIT     price >= current fair value
  APPROACHING    price >= 90% of current fair value
  MANUAL_TARGET  price >= the lot's target_price (if set)
  THESIS_BREAK   status disqualified; F-Score at the disqualify threshold; strict M-Score > -1.78;
                 or current fair value more than 20% below fair_value_at_buy
  QUALITATIVE    the qualitative gate fires for the ticker
  DATA_ISSUE     status insufficient_data or corporate_action_pending, or the ticker has no screen row
Each alert fires once while its condition holds (an active alert has cleared_at null) and re-arms only
after the condition clears. A condition that can't be evaluated (e.g. no fair value) leaves the alert
state unchanged. Alerts on lots that have been fully sold are cleared.

Usage: python scripts/check_alerts.py [--dry-run]
"""
import json
import sys
from pathlib import Path

from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener import rules as R  # noqa: E402
from screener.db import connect  # noqa: E402

APPROACHING_RATIO = 0.90
FAIR_VALUE_DROP = 0.20
DATA_ISSUE_STATUSES = (R.STATUS_INSUFFICIENT, R.STATUS_CORPORATE_ACTION)

LOTS_SQL = """
select h.id, h.user_id, h.ticker, h.buy_price, h.fair_value_at_buy, h.target_price,
       h.shares - coalesce((select sum(shares_sold) from sales s where s.holding_id = h.id), 0) as shares_open,
       v.as_of, v.status, v.status_reason, v.price, v.fair_value, v.mos, v.disqualify_reasons,
       v.qualitative_gate, v.qualitative_reasons,
       (v.detail ->> 'f_points')::float as f_points, (v.detail ->> 'f_tests')::float as f_tests,
       (v.detail ->> 'm_score')::float as m_score
from holdings h
left join valuations_latest v on v.ticker = h.ticker
"""


def money(x) -> str:
    return "—" if x is None else f"${float(x):,.2f}"


def evaluate(lot: dict) -> dict[str, tuple[bool | None, str]]:
    """alert_type -> (condition: True / False / None = can't evaluate, message)."""
    price, fv = lot["price"], lot["fair_value"]
    price = None if price is None else float(price)
    fv = None if fv is None else float(fv)
    has_row = lot["status"] is not None
    out = {}

    if price is not None and fv is not None and fv > 0:
        out["TARGET_HIT"] = (price >= fv, f"Price {money(price)} reached fair value {money(fv)} "
                                          f"({price / fv - 1:+.0%} vs fair value)")
        out["APPROACHING"] = (price >= APPROACHING_RATIO * fv,
                              f"Price {money(price)} is {price / fv:.0%} of fair value {money(fv)}")
    else:
        out["TARGET_HIT"] = out["APPROACHING"] = (None, "")

    target = lot["target_price"]
    if target is None:
        out["MANUAL_TARGET"] = (False, "")
    elif price is None:
        out["MANUAL_TARGET"] = (None, "")
    else:
        out["MANUAL_TARGET"] = (price >= float(target),
                                f"Price {money(price)} reached your target price {money(target)}")

    if not has_row:
        out["THESIS_BREAK"] = out["QUALITATIVE"] = (None, "")
    else:
        breaks = []
        if lot["status"] == R.STATUS_DISQUALIFIED:
            breaks.append(f"disqualified ({lot['disqualify_reasons'] or lot['status_reason']})")
        pts, tests = lot["f_points"], lot["f_tests"]
        if pts is not None and tests and tests >= R.PIOTROSKI_MIN_TESTS and pts / tests <= R.PIOTROSKI_DQ_RATIO:
            breaks.append(f"F-Score {pts:.0f}/{tests:.0f} at or below the disqualify threshold")
        if lot["m_score"] is not None and lot["m_score"] > R.BENEISH_THRESHOLD:
            breaks.append(f"M-Score {lot['m_score']:.2f} > {R.BENEISH_THRESHOLD}")
        fv_buy = lot["fair_value_at_buy"]
        if fv is not None and fv_buy is not None and float(fv_buy) > 0 and fv < (1 - FAIR_VALUE_DROP) * float(fv_buy):
            breaks.append(f"fair value {money(fv)} is {fv / float(fv_buy) - 1:.0%} vs {money(fv_buy)} at buy")
        out["THESIS_BREAK"] = (bool(breaks), "Thesis break: " + "; ".join(breaks))
        out["QUALITATIVE"] = (bool(lot["qualitative_gate"]),
                              f"Qualitative gate: {lot['qualitative_reasons'] or 'detector fired'}")

    if not has_row:
        out["DATA_ISSUE"] = (True, f"No screen data for {lot['ticker']} (left the universe or not yet valued)")
    else:
        out["DATA_ISSUE"] = (lot["status"] in DATA_ISSUE_STATUSES,
                             f"Status {lot['status']}: {lot['status_reason'] or ''}".rstrip(": "))
    return out


def run(conn, dry_run: bool = False) -> dict:
    cur = conn.execute(LOTS_SQL)
    cols = [d.name for d in cur.description]
    lots = [dict(zip(cols, r)) for r in cur.fetchall()]
    active = {(h, t) for h, t in conn.execute(
        "select holding_id, alert_type from alerts where cleared_at is null").fetchall()}
    fired, cleared, open_lots = [], 0, 0
    for lot in lots:
        if lot["shares_open"] <= 0:          # fully sold: retire any active alerts
            if not dry_run:
                cleared += conn.execute("update alerts set cleared_at = now() where holding_id = %s "
                                        "and cleared_at is null", (lot["id"],)).rowcount
            continue
        open_lots += 1
        for alert_type, (cond, message) in evaluate(lot).items():
            is_active = (lot["id"], alert_type) in active
            if cond and not is_active:
                fired.append(f"{lot['ticker']} {alert_type}: {message}")
                if not dry_run:
                    conn.execute(
                        "insert into alerts (user_id, holding_id, ticker, alert_type, price_at_alert, "
                        "fair_value_at_alert, message) values (%s, %s, %s, %s, %s, %s, %s)",
                        (lot["user_id"], lot["id"], lot["ticker"], alert_type, lot["price"], lot["fair_value"],
                         message[:1000]))
            elif cond is False and is_active:
                cleared += 1
                if not dry_run:
                    conn.execute("update alerts set cleared_at = now() where holding_id = %s and alert_type = %s "
                                 "and cleared_at is null", (lot["id"], alert_type))
    return {"lots": len(lots), "open_lots": open_lots, "fired": len(fired), "cleared": cleared,
            "fired_detail": fired[:50]}


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    with connect() as conn:
        with conn.transaction():
            summary = run(conn, dry_run)
            if not dry_run:
                conn.execute("insert into runs (job, as_of, finished_at, status, summary) "
                             "values ('alerts', current_date, now(), 'ok', %s)",
                             (Jsonb({k: v for k, v in summary.items() if k != "fired_detail"},
                                    dumps=json.dumps),))
    print(f"{'[dry run] ' if dry_run else ''}lots {summary['lots']} (open {summary['open_lots']}): "
          f"fired {summary['fired']}, cleared {summary['cleared']}")
    for line in summary["fired_detail"]:
        print("  ", line)


if __name__ == "__main__":
    main()
