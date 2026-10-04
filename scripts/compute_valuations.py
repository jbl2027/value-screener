"""Milestone 6: fair value, margin of safety, flags, and disqualifications.

Inputs:  data/metrics.csv (compute_metrics.py), data/fundamentals_summary.csv,
         EDGAR submissions (8-K Item 2.01 check), FRED DGS10
Outputs: data/valuations.csv         one row per company: status, fair value, MoS, peers, reasons
         data/valuation_fits.csv     regression fits (bank, insurer) and peer-group medians
         data/corporate_actions.csv  Item 2.01 check per company
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener import rules as R  # noqa: E402
from screener.config import DATA_DIR  # noqa: E402
from screener.corporate_actions import check  # noqa: E402
from screener.fred import latest  # noqa: E402
from screener.sec_client import SECClient  # noqa: E402
from screener.valuation import value_all  # noqa: E402

OUTPUT_COLUMNS = [
    "as_of", "cik", "ticker", "name", "sector_model", "gics_sector", "gics_industry_group",
    "status", "status_reason", "confidence", "confidence_reasons", "warnings", "disqualify_reasons",
    "missing_inputs", "price", "fair_value", "mos", "method", "peer_level", "peer_group",
    "peer_count", "peer_multiple", "fair_multiple", "company_multiple", "base_value", "shares",
    "earnings_yield", "treasury_yield", "treasury_guard_pass", "m_score", "m_score_imputed",
    "acquisitions_to_assets", "f_points", "f_tests", "f_score_scaled", "captive_finance",
    "corporate_action_pending", "item_2_01_filed", "cleared_by",
]


def main() -> None:
    metrics = pd.read_csv(DATA_DIR / "metrics.csv")
    summary = pd.read_csv(DATA_DIR / "fundamentals_summary.csv")
    as_of = metrics["as_of"].iloc[0]

    print(f"As of {as_of}. Checking 8-K Item 2.01 filings (last {R.CORPORATE_ACTION_LOOKBACK_DAYS} days)...")
    sec = SECClient()
    with ThreadPoolExecutor(max_workers=4) as pool:
        corp = list(pool.map(lambda c: check(int(c), sec, as_of, R.CORPORATE_ACTION_LOOKBACK_DAYS),
                             metrics["cik"]))
    corp = pd.DataFrame(corp)
    corp.to_csv(DATA_DIR / "corporate_actions.csv", index=False)

    t_date, t_yield = latest(R.TREASURY_SERIES, as_of)
    print(f"10-yr Treasury ({R.TREASURY_SERIES}) {t_yield:.2f}% on {t_date} "
          f"-> standard model needs EV/EBIT < {100 / t_yield:.1f}x to flag")

    result, fits = value_all(metrics, summary, corp, t_yield)
    result[OUTPUT_COLUMNS].sort_values(["status", "mos"], ascending=[True, False]).to_csv(
        DATA_DIR / "valuations.csv", index=False)

    peer_rows = (result[result["sector_model"].isin(["standard", "reit"])]
                 .groupby(["sector_model", "peer_level", "peer_group"])
                 .agg(peer_count=("peer_count", "first"), peer_multiple=("peer_multiple", "first"),
                      companies=("ticker", "count")).reset_index())
    fit_rows = pd.DataFrame(fits).assign(peer_level="regression")
    pd.concat([fit_rows, peer_rows], ignore_index=True).assign(as_of=as_of).to_csv(
        DATA_DIR / "valuation_fits.csv", index=False)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 70)
    print("\n=== Status by model ===")
    print(pd.crosstab(result["sector_model"], result["status"], margins=True).to_string())

    print("\n=== Regression fits ===")
    for f in fits:
        if f.get("r2") is None:
            print(f"  {f['model']}: insufficient data (n={f['n']})")
        else:
            print(f"  {f['model']}: {f['y']} = {f['intercept']:.3f} + {f['slope']:.3f} x {f['x']}   "
                  f"R2={f['r2']:.3f}  n={f['n']}")

    print("\n=== Peer groups used ===")
    print(peer_rows.round(2).to_string(index=False))

    def show(status, cols, title):
        sub = result[result["status"] == status]
        print(f"\n=== {title} ({len(sub)}) ===")
        if len(sub):
            print(sub.sort_values("mos", ascending=False)[cols].round(3).to_string(index=False))

    show(R.STATUS_FLAG, ["ticker", "sector_model", "price", "fair_value", "mos", "company_multiple",
                         "peer_multiple", "fair_multiple", "earnings_yield", "confidence",
                         "confidence_reasons", "warnings"], "FLAGS")
    show(R.STATUS_DISQUALIFIED, ["ticker", "sector_model", "mos", "disqualify_reasons"], "Disqualified")
    show(R.STATUS_NOT_APPLICABLE, ["ticker", "sector_model", "status_reason"], "Not applicable")
    show(R.STATUS_CORPORATE_ACTION, ["ticker", "item_2_01_filed", "status_reason"], "Corporate action pending")
    show(R.STATUS_EXCLUDED, ["ticker", "status_reason"], "Excluded")
    show(R.STATUS_INSUFFICIENT, ["ticker", "sector_model", "missing_inputs"], "Insufficient data")

    guard = result[(result["status"] == R.STATUS_NO_FLAG) & (result["mos"] >= R.MOS_FLAG_THRESHOLD)]
    print(f"\nBlocked by the Treasury guard (MoS >= 30% but EBIT/EV <= {t_yield:.2f}%): "
          + (" ".join(guard["ticker"]) or "none"))
    review = result[result["warnings"].str.contains("beneish review", na=False)]
    print("Beneish M&A review warnings: " + (" ".join(review["ticker"]) or "none"))


if __name__ == "__main__":
    main()
