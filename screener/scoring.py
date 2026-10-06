"""Milestone 7: composite score (0-100), the research report's Forensic Value Score weights.

Component weights (report section 2.2):
  valuation 25   relative valuation (earnings yield EBIT/EV; banks TBV/P; insurers B/P; REITs FFO/P)
  productivity 15  operating profitability (EBIT + R&D) / assets (Ball et al. 2015) for every
                 standard company; banks ROTCE, insurers ROE
  earnings_quality 10  Sloan accruals (lower is better; worst decile in group scores 0)
  momentum 10    Piotroski F mapped: 8-9 -> 100, 7 -> 85, 6 -> 65, 5 -> 50, 4 -> 35, <= 3 -> 0
                 (full score, or scaled score when >= 7 tests ran; else missing)
  llm 20         Gemini 10-K forensic score (Milestone 8)
  growth 10      ROIC x reinvestment rate (R&D-adjusted where available)
  shareholder_yield 10

Percentile ranks are sector-neutral: standard companies rank within their GICS industry group,
falling back to GICS sector, then all standard companies, when fewer than MIN_GROUP companies
have the metric (matching the valuation peers). Banks, insurers, and REITs rank within their
own model. A missing component is imputed at the neutral score of 50; `components_available`
counts the real ones (of `components_active`), and fewer than MIN_COMPONENTS makes the composite
low confidence. Components in PENDING (currently the LLM score) are excluded and the remaining
weights renormalized until they are in production. A composite also requires a real valuation score, and is null for rows whose
valuation status is insufficient_data (component scores are kept for debugging). Banks and
insurers have no earnings-quality or momentum component (Rule 1), so those are imputed.
"""
import numpy as np
import pandas as pd

WEIGHTS = {"valuation": 25, "productivity": 15, "earnings_quality": 10, "momentum": 10,
           "llm": 20, "growth": 10, "shareholder_yield": 10}
NEUTRAL = 50.0              # imputed score for a missing component
# Components not yet in production are excluded and the remaining weights renormalized (rather
# than imputed at 50). The LLM component joins once its rubric is approved and scored at scale.
PENDING = {"llm"}
MIN_GROUP = 8               # minimum companies with the metric for a ranking group
MIN_COMPONENTS = 5          # fewer real components -> composite low confidence
SLOAN_WORST_DECILE = 10.0   # percentile at or below which accruals score 0
PIOTROSKI_MAP = [(8, 100), (7, 85), (6, 65), (5, 50), (4, 35)]  # else 0
STANDARD_LEVELS = ("gics_industry_group", "gics_sector", "sector_model")

# component -> model -> (metric column, higher_is_better)
COMPONENT_METRICS = {
    "valuation": {"standard": ("earnings_yield", True), "bank": ("tbv_yield", True),
                  "insurer": ("book_yield", True), "reit": ("ffo_yield", True)},
    "productivity": {"standard": ("operating_profitability", True), "bank": ("rotce", True),
                     "insurer": ("roe", True)},
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
    if "llm_score" not in df:
        df["llm_score"] = np.nan
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
            for level in STANDARD_LEVELS:
                peers = sub[sub[level] == row[level]]
                if len(peers) >= MIN_GROUP or level == STANDARD_LEVELS[-1]:
                    break
            out.loc[idx, f"{component}_score"] = _percentile(peers[col], higher).loc[idx]
            out.loc[idx, f"{component}_group"] = f"{level}:{row[level]}"
    out[f"{component}_score"] = pd.to_numeric(out[f"{component}_score"])
    if component == "earnings_quality":
        out.loc[out[f"{component}_score"] <= SLOAN_WORST_DECILE, f"{component}_score"] = 0.0
    return out


def momentum_score(df: pd.DataFrame) -> pd.Series:
    def mapped(f):
        if pd.isna(f):
            return np.nan
        for floor, score in PIOTROSKI_MAP:
            if round(f) >= floor:
                return float(score)
        return 0.0
    return df["f_used"].map(mapped).where(df["sector_model"].isin(["standard", "reit"]))


def score_all(df: pd.DataFrame) -> pd.DataFrame:
    df = prepare(df)
    for component in COMPONENT_METRICS:
        df = df.join(rank_component(df, component))
    df["momentum_score"] = momentum_score(df)
    df["momentum_group"] = np.where(df["momentum_score"].notna(), "absolute map", None)
    df["llm_group"] = np.where(df["llm_score"].notna(), "absolute (rubric)", None)

    active = {k: w for k, w in WEIGHTS.items() if k not in PENDING}
    scores = pd.DataFrame({k: df[f"{k}_score"] for k in active})
    df["components_available"] = scores.notna().sum(axis=1)
    df["components_active"] = len(active)
    df["components_missing"] = scores.apply(lambda r: " ".join(k for k in active if pd.isna(r[k])), axis=1)
    df["components_pending"] = " ".join(sorted(PENDING))
    total = sum(active.values())
    composite = sum(scores[k].fillna(NEUTRAL) * w for k, w in active.items()) / total

    has_valuation = scores["valuation"].notna()
    insufficient = df.get("status", pd.Series(index=df.index, dtype=object)) == "insufficient_data"
    df["composite"] = composite.where(has_valuation & ~insufficient)
    df["composite_note"] = np.select([insufficient, ~has_valuation], ["valuation status insufficient_data",
                                                                     "no valuation score"], "")
    df["composite_confidence"] = np.where(df["composite"].isna(), None,
                                          np.where(df["components_available"] >= MIN_COMPONENTS, "normal", "low"))
    df["composite_rank"] = df["composite"].rank(ascending=False, method="min")
    return df
