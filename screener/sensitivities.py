"""EBIT adjustments for valuation, applied before peer medians are computed.

1. Impairment add-back (all companies): goodwill and intangible impairment charges are non-cash,
   non-recurring write-downs of past acquisition prices; left in, they depress TTM, fiscal-year,
   and especially 3-yr average EBIT for years (e.g. SJM after Hostess). The charges come from
   metrics (impairment_addback_*, same periods as each EBIT figure). The major-impairment gate
   detector is separate and unchanged. Safeguard: a period's charge is not added back when the
   same period shows a discontinued-operations loss of at least half the charge
   (impairment_addback_excluded; listed in ebit_adjustment as impairment_addback_skipped).
2. Manual adjustments (EBIT_ADJUSTMENTS) from filing text that XBRL companyfacts doesn't expose.
   Each replaces TTM and 3-yr average EBIT (USD deltas); the as-reported values are valued as a
   sensitivity. Values must cite their source and be refreshed with each filing.

As-reported EBIT is kept in *_reported columns; ebit_adjustment describes what was changed.
"""
import pandas as pd

ADD_BACK_IMPAIRMENTS = True

EBIT_ADJUSTMENTS = {
    "FSLR": {
        "label": "ex Section 45X manufacturing credits",
        "note": "government_credit_dependent",
        # Credits recognized as a reduction to cost of sales ($ thousands):
        #   FY2023 659,745; FY2024 1,009,451; FY2025 1,606,722 (10-K filed 2026-02-24)
        #   H1 2025 678,861; H1 2026 862,513 (10-Q filed 2026-07-30)
        "ttm_adjust": -(1_606_722 + 862_513 - 678_861) * 1_000,        # -1,790.4M
        "avg3_adjust": -(659_745 + 1_009_451 + 1_606_722) / 3 * 1_000,  # -1,092.0M
        "fy_adjust": -1_606_722 * 1_000,                                 # FY2025, -1,606.7M
        "source": "FSLR 10-K 2026-02-24 and 10-Q 2026-07-30, government incentives note",
    },
}

# EBIT column -> impairment add-back column (same period)
IMPAIRMENT_ADDBACK = {
    "ebit_ttm": "impairment_addback_ttm",
    "ebit_fy": "impairment_addback_fy",
    "ebit_fy_prior": "impairment_addback_fy_prior",
    "ebit_3y_avg": "impairment_addback_3y_avg",
}


def _money(x: float) -> str:
    return f"${x / 1e9:,.2f}B" if abs(x) >= 1e9 else f"${x / 1e6:,.0f}M"


def apply_ebit_adjustments(metrics: pd.DataFrame) -> pd.DataFrame:
    """Return metrics with adjusted EBIT, EV/EBIT, and earnings yield; reported values kept.
    ebit_adjustment: text of every adjustment (shown on the company page);
    ebit_adjustment_manual: the manual one only (also listed under warnings)."""
    df = metrics.copy()
    for col in ("ebit_ttm", "ebit_3y_avg", "ebit_fy", "ebit_fy_prior", "ev_ebit", "earnings_yield"):
        df[f"{col}_reported"] = df[col] if col in df else None
    notes = pd.Series([[] for _ in range(len(df))], index=df.index)
    adjusted = pd.Series(False, index=df.index)

    if ADD_BACK_IMPAIRMENTS and all(c in df for c in IMPAIRMENT_ADDBACK.values()):
        for ebit_col, add_col in IMPAIRMENT_ADDBACK.items():
            add = df[add_col].fillna(0).clip(lower=0)
            df[ebit_col] = df[ebit_col] + add.where(df[ebit_col].notna(), 0)
        has = df[list(IMPAIRMENT_ADDBACK.values())].fillna(0).gt(0).any(axis=1)
        for i in df.index[has]:
            parts = [f"{label} +{_money(df.at[i, col])}" for label, col in
                     (("TTM", "impairment_addback_ttm"), ("FY", "impairment_addback_fy"),
                      ("prior FY", "impairment_addback_fy_prior"), ("3-yr avg", "impairment_addback_3y_avg"))
                     if pd.notna(df.at[i, col]) and df.at[i, col] > 0]
            notes[i].append("impairment_addback: goodwill/intangible impairment charges added back to EBIT ("
                            + ", ".join(parts) + "; XBRL GoodwillImpairmentLoss / ImpairmentOfIntangibleAssets*)")
        adjusted |= has
        if "impairment_addback_excluded" in df:
            for i in df.index[df["impairment_addback_excluded"].notna()]:
                notes[i].append(f"impairment_addback_skipped: {df.at[i, 'impairment_addback_excluded']}")

    df["ebit_adjustment_manual"] = None
    for ticker, adj in EBIT_ADJUSTMENTS.items():
        m = df["ticker"] == ticker
        if not m.any():
            continue
        df.loc[m, "ebit_ttm"] = df.loc[m, "ebit_ttm"] + adj["ttm_adjust"]
        df.loc[m, "ebit_3y_avg"] = df.loc[m, "ebit_3y_avg"] + adj["avg3_adjust"]
        if "operating_profitability" in df and "fy_adjust" in adj:
            df.loc[m, "operating_profitability"] = (df.loc[m, "operating_profitability"]
                                                    + adj["fy_adjust"] / df.loc[m, "total_assets_fy"])
        text = f"{adj['note']}: EBIT {adj['label']} ({adj['source']})"
        df.loc[m, "ebit_adjustment_manual"] = text
        for i in df.index[m]:
            notes[i].append(text)
        adjusted |= m

    ev, ebit = df["enterprise_value"], df["ebit_ttm"]
    df.loc[adjusted, "ev_ebit"] = (ev / ebit).where(ebit > 0)[adjusted]
    df.loc[adjusted, "earnings_yield"] = (ebit / ev)[adjusted]
    if "ebit_fy_change" in df:
        e0, e1 = df["ebit_fy"], df["ebit_fy_prior"]
        df.loc[adjusted, "ebit_fy_change"] = ((e0 - e1) / e1.abs()).where(e1.notna() & (e1 != 0))[adjusted]
    df["ebit_adjustment"] = notes.map(lambda n: " | ".join(n) or None)
    return df
