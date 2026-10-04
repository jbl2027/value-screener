"""Milestone 7: composite score (0-100), the research report's Forensic Value Score weights.

Component weights (report section 2.2):
  valuation 25   relative valuation (earnings yield EBIT/EV; banks TBV/P; insurers B/P; REITs FFO/P)
  productivity 15  GP/A (banks ROTCE, insurers ROE)
  earnings_quality 10  Sloan accruals (lower is better; worst decile in group scores 0)
  momentum 10    Piotroski F mapped: 8-9 -> 100, 7 -> 85, 6 -> 65, 5 -> 50, 4 -> 35, <= 3 -> 0
                 (full score, or scaled score when >= 7 tests ran; else missing)
  llm 20         Gemini 10-K forensic score (Milestone 8; excluded until then)
  growth 10      ROIC x reinvestment rate (R&D-adjusted where available)
  shareholder_yield 10

Percentile ranks are sector-neutral: standard companies rank within their 2-digit SIC major
group, falling back to SIC division, then all standard companies, when fewer than MIN_GROUP
companies have the metric. Banks, insurers, and REITs rank within their own model.
Composite = weighted mean of available component scores (weights renormalized), reported with
`coverage` = available weight / total weight excluding pending components. Banks and insurers
skip earnings quality and momentum (Rule 1).
"""
import numpy as np
import pandas as pd

WEIGHTS = {"valuation": 25, "productivity": 15, "earnings_quality": 10, "momentum": 10,
           "llm": 20, "growth": 10, "shareholder_yield": 10}
PENDING = {"llm"}           # components not yet built (Milestone 8)
MIN_GROUP = 8               # minimum companies with the metric for a ranking group
MIN_COVERAGE = 0.5          # composite needs >= 50% of available weight, incl. valuation
SLOAN_WORST_DECILE = 10.0   # percentile at or below which accruals score 0
PIOTROSKI_MAP = [(8, 100), (7, 85), (6, 65), (5, 50), (4, 35)]  # else 0

# component -> model -> (metric column, higher_is_better)
COMPONENT_METRICS = {
    "valuation": {"standard": ("earnings_yield", True), "bank": ("tbv_yield", True),
                  "insurer": ("book_yield", True), "reit": ("ffo_yield", True)},
    "productivity": {"standard": ("gp_assets", True), "bank": ("rotce", True), "insurer": ("roe", True)},
    "earnings_quality": {"standard": ("sloan_accruals", False), "reit": ("sloan_accruals", False)},
    "growth": {"standard": ("growth_metric", True)},
    "shareholder_yield": {m: ("shareholder_yield", True) for m in ("standard", "bank", "insurer", "reit")},
}


def _inverse(s: pd.Series) -> pd.Series:
    return (1 / s).where(s > 0)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["tbv_yield"] = _inverse(df["p_tbv"])
    df["book_yield"] = _inverse(df["p_b"])
    df["ffo_yield"] = _inverse(df["p_ffo"])
    df["growth_metric"] = df["growth_rd_adj"].fillna(df["growth_roic_x_rr"])
    df["f_used"] = df["f_score"].fillna(df["f_score_scaled"])
    df["sic_major"] = (df["sic"] // 100).astype("Int64")
    return df


def _percentile(values: pd.Series, higher_is_better: bool) -> pd.Series:
    ranks = values.rank(method="average", ascending=higher_is_better)
    return (ranks - 0.5) / values.notna().sum() * 100


def rank_component(df: pd.DataFrame, component: str) -> pd.DataFrame:
    """Score 0-100 and the ranking group used, per company, for one component."""
    out = pd.DataFrame(index=df.index, columns=[f"{component}_score", f"{component}_group"], dtype=object)
    for model, (col, higher) in COMPONENT_METRICS[component].items():
        sub = df[(df["sector_model"] == model) & df[col].notna()]
        if model != "standard":
            out.loc[sub.index, f"{component}_score"] = _percentile(sub[col], higher)
            out.loc[sub.index, f"{component}_group"] = f"model:{model}"
            continue
        for idx, row in sub.iterrows():
            for level in ("sic_major", "sic_division", "sector_model"):
                peers = sub[sub[level] == row[level]]
                if len(peers) >= MIN_GROUP or level == "sector_model":
                    break
            pct = _percentile(peers[col], higher).loc[idx]
            out.loc[idx, f"{component}_score"] = pct
            out.loc[idx, f"{component}_group"] = f"{level}:{row[level]}"
    out[f"{component}_score"] = pd.to_numeric(out[f"{component}_score"])
    if component == "earnings_quality":
        worst = out[f"{component}_score"] <= SLOAN_WORST_DECILE
        out.loc[worst, f"{component}_score"] = 0.0
    return out


def momentum_score(df: pd.DataFrame) -> pd.Series:
    def mapped(f):
        if pd.isna(f):
            return np.nan
        for floor, score in PIOTROSKI_MAP:
            if round(f) >= floor:
                return float(score)
        return 0.0
    s = df["f_used"].map(mapped)
    return s.where(df["sector_model"].isin(["standard", "reit"]))


def score_all(df: pd.DataFrame) -> pd.DataFrame:
    df = prepare(df)
    for component in COMPONENT_METRICS:
        df = df.join(rank_component(df, component))
    df["momentum_score"] = momentum_score(df)
    df["momentum_group"] = np.where(df["momentum_score"].notna(), "absolute map", None)

    active = {k: w for k, w in WEIGHTS.items() if k not in PENDING}
    total = sum(active.values())
    scores = pd.DataFrame({k: df[f"{k}_score"] for k in active})
    weights = pd.DataFrame({k: np.where(scores[k].notna(), w, 0.0) for k, w in active.items()}, index=df.index)
    df["coverage"] = weights.sum(axis=1) / total
    composite = (scores.fillna(0) * weights).sum(axis=1) / weights.sum(axis=1).replace(0, np.nan)
    ok = (df["coverage"] >= MIN_COVERAGE) & scores["valuation"].notna()
    df["composite"] = composite.where(ok)
    df["composite_note"] = np.where(ok, "", np.where(scores["valuation"].isna(), "no valuation score",
                                                     "coverage below 50%"))
    df["components_missing"] = scores.apply(lambda r: " ".join(k for k in active if pd.isna(r[k])), axis=1)
    df["pending_components"] = " ".join(sorted(PENDING))
    df["composite_rank"] = df["composite"].rank(ascending=False, method="min")
    return df
