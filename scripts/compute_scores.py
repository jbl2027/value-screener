"""Milestone 7: composite score (0-100) with sector-neutral percentile ranks.

Inputs:  data/metrics.csv, data/valuations.csv, data/universe.csv
Output:  data/scores.csv  composite, coverage, and each component's raw value, score, and group
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.scoring import COMPONENT_METRICS, PENDING, WEIGHTS, score_all  # noqa: E402
from screener.sensitivities import apply_ebit_adjustments  # noqa: E402

COMPONENTS = [c for c in WEIGHTS if c not in PENDING]
RAW = {"valuation": "valuation_raw", "productivity": "productivity_raw", "earnings_quality": "sloan_accruals",
       "momentum": "f_used", "growth": "growth_metric", "shareholder_yield": "shareholder_yield"}


def main() -> None:
    metrics = apply_ebit_adjustments(pd.read_csv(DATA_DIR / "metrics.csv"))
    universe = pd.read_csv(DATA_DIR / "universe.csv").drop_duplicates("cik")[["cik", "sic", "sic_division"]]
    valuations = pd.read_csv(DATA_DIR / "valuations.csv")[["cik", "status", "mos", "confidence", "fair_value"]]
    df = metrics.merge(universe, on="cik", how="left").merge(valuations, on="cik", how="left")

    scored = score_all(df)
    # Raw value behind each model-specific component, for display.
    for comp in ("valuation", "productivity"):
        scored[f"{comp}_raw"] = None
        for model, (col, _) in COMPONENT_METRICS[comp].items():
            m = scored["sector_model"] == model
            scored.loc[m, f"{comp}_raw"] = scored.loc[m, col]

    cols = (["as_of", "cik", "ticker", "name", "sector_model", "gics_sector", "sic", "status", "confidence",
             "mos", "composite", "composite_rank", "coverage", "composite_note", "components_missing",
             "pending_components"]
            + [x for c in COMPONENTS for x in (f"{c}_score", RAW[c], f"{c}_group")])
    out = scored[cols].sort_values("composite", ascending=False)
    out.to_csv(DATA_DIR / "scores.csv", index=False)

    pd.set_option("display.width", 250)
    s = out["composite"].dropna()
    print(f"Composite computed for {len(s)} of {len(out)} companies "
          f"(weights renormalized; pending: {', '.join(sorted(PENDING))})")
    print(f"Distribution: p10 {s.quantile(.1):.1f}  median {s.median():.1f}  p90 {s.quantile(.9):.1f}")
    print("Missing composite:", out[out["composite"].isna()]["composite_note"].value_counts().to_dict())

    print("\nCoverage by model (mean share of available weight):")
    print(out.groupby("sector_model")["coverage"].mean().round(2).to_string())
    print("\nComponent coverage (companies with a score):")
    for c in COMPONENTS:
        print(f"  {c:18s} {out[f'{c}_score'].notna().sum():4d}")
    std = out[out["sector_model"] == "standard"]
    print("\nRanking group level used (standard, valuation component):")
    print(std["valuation_group"].str.split(":").str[0].value_counts().to_string())

    show = ["ticker", "sector_model", "status", "mos", "composite", "coverage"] + [f"{c}_score" for c in COMPONENTS]
    print("\n=== Top 25 by composite ===")
    print(out.head(25)[show].round(1).to_string(index=False))
    print("\n=== Flags and watch, by composite ===")
    fw = out[out["status"].isin(["flag", "watch"])]
    print(fw[show].round(2).to_string(index=False))
    both = out.dropna(subset=["composite", "mos"])
    print(f"\nSpearman correlation composite vs MoS: {both['composite'].corr(both['mos'], method='spearman'):.2f}")
    print("Median composite by status:")
    print(out.groupby("status")["composite"].median().round(1).sort_values(ascending=False).to_string())


if __name__ == "__main__":
    main()
