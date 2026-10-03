"""Milestone 5: compute metrics for every company as of the latest price date.

Inputs:  data/universe.csv, data/facts.parquet (build_fundamentals.py), data/prices.parquet
Outputs: data/metrics.csv        one row per company (CIK), all metrics and input bases
         data/metrics_notes.csv  one row per (company, note): why a metric is missing or caveated
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.metrics import compute  # noqa: E402
from screener.rules import MODEL_RULES  # noqa: E402
from screener.series import build_company  # noqa: E402

SPOT_CHECK = ["JPM", "PLD", "MSFT", "CAT", "AAPL", "XOM", "JNJ", "WMT", "NEE", "TRV"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", help="YYYY-MM-DD (default: latest price date)")
    args = parser.parse_args()

    universe = pd.read_csv(DATA_DIR / "universe.csv")
    facts = pd.read_parquet(DATA_DIR / "facts.parquet")
    prices = pd.read_parquet(DATA_DIR / "prices.parquet")
    as_of = args.as_of or prices["date"].max()
    prices = prices[prices["date"] <= as_of].sort_values("date").groupby("ticker").tail(1).set_index("ticker")
    print(f"As of {as_of}: {facts['cik'].nunique()} companies with facts, {len(prices)} prices")

    facts_by_cik = dict(tuple(facts.groupby("cik")))
    rows, notes = [], []
    for cik, grp in universe.groupby("cik"):
        tickers = sorted(grp["ticker"])
        ticker = tickers[0]  # multi-class: first alphabetically (classes trade near parity)
        info = grp.iloc[0]
        base = {"cik": cik, "ticker": ticker, "tickers": " ".join(tickers), "name": info["name"],
                "sector_model": info["sector_model"], "gics_sector": info["gics_sector"],
                "gics_industry_group": info["gics_industry_group"],
                "gics_sub_industry": info["gics_sub_industry"], "captive_finance": info["captive_finance"]}
        price = prices.loc[ticker] if ticker in prices.index else None
        if cik not in facts_by_cik:
            rows.append(base)
            notes.append({"cik": cik, "ticker": ticker, "note": "no facts"})
            continue
        cd = build_company(int(cik), facts_by_cik[cik], as_of)
        m, n = compute(cd, info["sector_model"],
                       float(price["close"]) if price is not None else None,
                       price["date"] if price is not None else None)
        rows.append({**base, **m})
        notes += [{"cik": cik, "ticker": ticker, "note": x} for x in n]

    df = pd.DataFrame(rows)
    df.insert(0, "as_of", as_of)
    df.to_csv(DATA_DIR / "metrics.csv", index=False)
    notes_df = pd.DataFrame(notes)
    notes_df.to_csv(DATA_DIR / "metrics_notes.csv", index=False)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    print(f"Wrote {len(df)} rows to data/metrics.csv, {len(notes_df)} notes to data/metrics_notes.csv")

    metric_cols = {
        "standard": ["ev_ebit", "ebit_3y_avg", "gp_assets", "sloan_accruals", "f_score", "m_score",
                     "m_score_imputed", "shareholder_yield", "roic", "roic_rd_adj", "growth_roic_x_rr"],
        "reit": ["p_ffo", "sloan_accruals", "f_score", "m_score", "m_score_imputed", "shareholder_yield"],
        "bank": ["p_tbv", "rotce", "shareholder_yield"],
        "insurer": ["p_b", "roe", "shareholder_yield"],
    }
    print("\n=== Coverage and distribution (p5 / median / p95) ===")
    for model, cols in metric_cols.items():
        sub = df[df["sector_model"] == model]
        print(f"\n{model} ({len(sub)})")
        for c in cols:
            s = pd.to_numeric(sub[c], errors="coerce").dropna()
            dist = f"{s.quantile(.05):9.3f} {s.median():9.3f} {s.quantile(.95):9.3f}" if len(s) else ""
            print(f"  {c:20s} {len(s):4d}/{len(sub):<4d} {dist}")

    std = df[df["sector_model"] == "standard"]
    print(f"\nPiotroski partial (<9 tests): {int((std['f_tests'] < 9).sum())} standard; "
          f"F-Score distribution: {std['f_score'].value_counts().sort_index().to_dict()}")
    print(f"Beneish M > -1.78 (strict): {int((std['m_score'] > -1.78).sum())} of {std['m_score'].notna().sum()}; "
          f"(imputed): {int((std['m_score_imputed'] > -1.78).sum())} of {std['m_score_imputed'].notna().sum()}")

    print("\n=== Spot check ===")
    spot = df[df["ticker"].isin(SPOT_CHECK)].set_index("ticker").reindex(SPOT_CHECK)
    show = ["sector_model", "price", "market_cap", "enterprise_value", "ev_ebit", "p_tbv", "rotce", "p_b",
            "roe", "p_ffo", "gp_assets", "sloan_accruals", "f_score", "m_score", "shareholder_yield", "roic"]
    out = spot[show].copy()
    for c in ["market_cap", "enterprise_value"]:
        out[c] = (out[c] / 1e9).round(0)
    print(out.round(3).to_string())
    print("(market cap and EV in $ billions)")


if __name__ == "__main__":
    main()
