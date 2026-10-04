"""Manual EBIT sensitivities from filing text that XBRL companyfacts doesn't expose.

Each entry adjusts TTM and 3-yr average EBIT (USD) and is reported alongside the main
valuation without changing status. Values must cite their source and be refreshed each filing.
"""

EBIT_SENSITIVITIES = {
    "FSLR": {
        "label": "ex Section 45X manufacturing credits",
        # Credits recognized as a reduction to cost of sales ($ thousands):
        #   FY2023 659,745; FY2024 1,009,451; FY2025 1,606,722 (10-K filed 2026-02-24)
        #   H1 2025 678,861; H1 2026 862,513 (10-Q filed 2026-07-30)
        "ttm_adjust": -(1_606_722 + 862_513 - 678_861) * 1_000,        # -1,790.4M
        "avg3_adjust": -(659_745 + 1_009_451 + 1_606_722) / 3 * 1_000,  # -1,092.0M
        "source": "FSLR 10-K 2026-02-24 and 10-Q 2026-07-30, government incentives note",
    },
}
