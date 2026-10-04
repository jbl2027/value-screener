"""Manual EBIT adjustments from filing text that XBRL companyfacts doesn't expose.

Each adjustment replaces TTM and 3-yr average EBIT (USD deltas) for valuation, before peer
medians are computed. The as-reported values are kept and valued as a sensitivity. Values must
cite their source and be refreshed with each filing.
"""
import pandas as pd

EBIT_ADJUSTMENTS = {
    "FSLR": {
        "label": "ex Section 45X manufacturing credits",
        "note": "government_credit_dependent",
        # Credits recognized as a reduction to cost of sales ($ thousands):
        #   FY2023 659,745; FY2024 1,009,451; FY2025 1,606,722 (10-K filed 2026-02-24)
        #   H1 2025 678,861; H1 2026 862,513 (10-Q filed 2026-07-30)
        "ttm_adjust": -(1_606_722 + 862_513 - 678_861) * 1_000,        # -1,790.4M
        "avg3_adjust": -(659_745 + 1_009_451 + 1_606_722) / 3 * 1_000,  # -1,092.0M
        "source": "FSLR 10-K 2026-02-24 and 10-Q 2026-07-30, government incentives note",
    },
}


def apply_ebit_adjustments(metrics: pd.DataFrame) -> pd.DataFrame:
    """Return metrics with adjusted EBIT, EV/EBIT, and earnings yield; reported values kept."""
    df = metrics.copy()
    for col in ("ebit_ttm", "ebit_3y_avg", "ev_ebit", "earnings_yield"):
        df[f"{col}_reported"] = df[col]
    df["ebit_adjustment"] = None
    for ticker, adj in EBIT_ADJUSTMENTS.items():
        m = df["ticker"] == ticker
        if not m.any():
            continue
        df.loc[m, "ebit_ttm"] = df.loc[m, "ebit_ttm"] + adj["ttm_adjust"]
        df.loc[m, "ebit_3y_avg"] = df.loc[m, "ebit_3y_avg"] + adj["avg3_adjust"]
        ev, ebit = df.loc[m, "enterprise_value"], df.loc[m, "ebit_ttm"]
        df.loc[m, "ev_ebit"] = (ev / ebit).where(ebit > 0)
        df.loc[m, "earnings_yield"] = ebit / ev
        df.loc[m, "ebit_adjustment"] = f"{adj['note']}: EBIT {adj['label']} ({adj['source']})"
    return df
