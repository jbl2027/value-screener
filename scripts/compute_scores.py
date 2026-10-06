"""Milestone 7: composite score (0-100) with sector-neutral percentile ranks.

Inputs:  data/metrics.csv, data/valuations.csv, data/universe.csv, data/llm_scores.csv (optional, M8)
Output:  data/scores.csv  composite, confidence, and each component's raw value, score, and group
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.scoring import COMPONENT_METRICS, WEIGHTS, score_all  # noqa: E402
from screener.llm import PROMPT_VERSION  # noqa: E402
from screener.sensitivities import apply_ebit_adjustments  # noqa: E402

RAW = {"valuation": "valuation_raw", "productivity": "productivity_raw", "earnings_quality": "sloan_accruals",
       "momentum": "f_used", "llm": "llm_score", "growth": "growth_metric", "shareholder_yield": "shareholder_yield"}
LLM_PATH = DATA_DIR / f"llm_scores_{PROMPT_VERSION}.csv"  # full-universe run of the current prompt


def main() -> None:
    metrics = apply_ebit_adjustments(pd.read_csv(DATA_DIR / "metrics.csv"))
    valuations = pd.read_csv(DATA_DIR / "valuations.csv")[["cik", "status", "mos", "confidence", "fair_value"]]
    df = metrics.merge(valuations, on="cik", how="left")
    if LLM_PATH.exists():
        df = df.merge(pd.read_csv(LLM_PATH)[["cik", "llm_score"]], on="cik", how="left")

    scored = score_all(df)
    for comp in ("valuation", "productivity"):
        scored[f"{comp}_raw"] = None
        for model, (col, _) in COMPONENT_METRICS[comp].items():
            m = scored["sector_model"] == model
            scored.loc[m, f"{comp}_raw"] = scored.loc[m, col]

    cols = (["as_of", "cik", "ticker", "name", "sector_model", "gics_sector", "gics_industry_group", "status",
             "confidence", "mos", "composite", "composite_confidence", "composite_rank", "components_available",
             "components_active", "components_missing", "components_pending", "composite_note"]
            + list(dict.fromkeys(x for c in WEIGHTS for x in (f"{c}_score", RAW[c], f"{c}_group"))))
    out = scored[cols].sort_values("composite", ascending=False)
    out.to_csv(DATA_DIR / "scores.csv", index=False)

    pd.set_option("display.width", 250)
    s = out["composite"].dropna()
    print(f"Composite published for {len(s)} of {len(out)} companies (missing components imputed at 50)")
    print(f"Distribution: p10 {s.quantile(.1):.1f}  median {s.median():.1f}  p90 {s.quantile(.9):.1f}")
    print("Null composite:", out[out["composite"].isna()]["composite_note"].value_counts().to_dict())
    print("Composite confidence:", out["composite_confidence"].value_counts().to_dict())
    print(f"Components available (of {int(out['components_active'].iloc[0])} active):", out["components_available"].value_counts().sort_index().to_dict())

    print("\nComponent coverage (companies with a real score):")
    for c in WEIGHTS:
        print(f"  {c:18s} {out[f'{c}_score'].notna().sum():4d}")
    std = out[out["sector_model"] == "standard"]
    print(f"Operating profitability coverage (standard): {std['productivity_score'].notna().sum()} of {len(std)}")
    print("Ranking group level (standard, valuation component):",
          std["valuation_group"].str.split(":").str[0].value_counts().to_dict())

    print("\n=== Status counts ===")
    print(out["status"].value_counts().to_string())
    print("\n=== Composite by status ===")
    print(out.groupby("status")["composite"].describe()[["count", "25%", "50%", "75%"]].round(1)
          .sort_values("50%", ascending=False).to_string())

    show = ["ticker", "sector_model", "mos", "composite", "composite_confidence", "components_available"] + \
           [f"{c}_score" for c in WEIGHTS if c != "llm"]
    flags = out[out["status"] == "flag"]
    print("\n=== Top 10 flags by composite ===")
    print(flags.head(10)[show].round(1).to_string(index=False))
    print("\n=== Bottom 5 flags by composite ===")
    print(flags.tail(5)[show].round(1).to_string(index=False))
    print("\n=== APA ===")
    apa = out[out["ticker"] == "APA"]
    print(apa[show + ["components_missing"]].round(1).to_string(index=False))
    both = out.dropna(subset=["composite", "mos"])
    # Spearman = Pearson on ranks (pandas' method="spearman" needs SciPy, which isn't a dependency).
    rho = both["composite"].rank().corr(both["mos"].rank())
    print(f"\nSpearman correlation composite vs MoS: {rho:.2f}")


if __name__ == "__main__":
    main()
