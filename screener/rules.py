"""Per-model metric applicability, disqualifiers, and input requirements.

Declared here so the data pipeline (Milestone 3) can log coverage against exactly what the
metrics (Milestone 5) and fair value / flags (Milestone 6) will need.

Requirements are lists of alternatives; each alternative is a list of concepts. A requirement
is met when every concept in at least one alternative is available. Concepts in
ABSENT_MEANS_ZERO count as available (value 0) when a company doesn't report them.
"""
from screener.debt import DebtResult, resolve_debt

# ---- Statuses produced by Milestone 6 ----
STATUS_FLAG = "flag"                    # normal confidence, 30% <= MoS <= 60%, not disqualified
STATUS_WATCH = "watch"                  # would flag but low confidence, or MoS > 60%
STATUS_NO_FLAG = "no_flag"
STATUS_DISQUALIFIED = "disqualified"    # Beneish / F-Score rule tripped (where applicable)
STATUS_INSUFFICIENT = "insufficient_data"  # a fair-value / MoS input is missing; never flagged
STATUS_NOT_APPLICABLE = "not_applicable"   # method doesn't apply (e.g. negative 3-yr avg EBIT)
STATUS_CORPORATE_ACTION = "corporate_action_pending"  # 8-K Item 2.01 not yet followed by a 10-Q
STATUS_EXCLUDED = "excluded"            # manual exclusion (MANUAL_EXCLUSIONS)
CONFIDENCE_NORMAL = "normal"
CONFIDENCE_LOW = "low"                  # e.g. captive finance (see sectors.CAPTIVE_FINANCE)

# ---- Flag and disqualification thresholds (Milestone 6) ----
MOS_FLAG_THRESHOLD = 0.30
BENEISH_THRESHOLD = -1.78               # strict M-Score above this disqualifies; imputed only warns
BENEISH_MA_EXCEPTION = 0.10             # acquisitions > 10% of beginning total assets: review, not DQ
PIOTROSKI_MIN_TESTS = 7                 # fewer testable criteria: no DQ, F-Score low confidence
PIOTROSKI_DQ_RATIO = 1 / 3              # disqualify if points / tests run <= 1/3
CORPORATE_ACTION_LOOKBACK_DAYS = 120    # 8-K Item 2.01 within this window and no 10-Q since
TREASURY_SERIES = "DGS10"               # standard model: flag only if EBIT/EV > 10-yr Treasury

MOS_EXTREME = 0.60                      # MoS above this goes to "watch" (extreme_mos_review)

# Corporate actions the 8-K Item 2.01 scan misses: ticker -> (8-K filing date, description,
# spun-off company's CIK or None). The spun-off company's revenue measures materiality.
MANUAL_CORPORATE_ACTIONS = {
    "CMCSA": ("2026-01-05", "Versant spin-off completed 2026-01-01 (8-K Item 8.01)", 2067876),
    # Biosciences & Diagnostic Solutions spun off and combined with Waters (no standalone
    # filer); size comes from the revenue restatement in BDX's 10-Qs.
    "BDX": ("2026-02-09", "Biosciences & Diagnostics spin-off/combination with Waters (8-K Item 8.01)", None),
    "CTVA": ("2026-10-01", "Vylor seed business separation completed (8-K Item 8.01)", 2128626),
}
# Post-corporate-action regime applies only to material transactions.
CORPORATE_ACTION_MIN_ACQ_TO_ASSETS = 0.10   # acquisitions (TTM cash) / pre-event total assets
CORPORATE_ACTION_MIN_DISPOSED_REVENUE = 0.10  # disposed business / revenue

MANUAL_EXCLUSIONS: dict[str, str] = {}

# Not reporting these is normal and means zero (e.g. AAPL reports no goodwill line).
ABSENT_MEANS_ZERO = {
    "rd_expense", "dividends_paid", "buybacks", "stock_issuance", "goodwill", "acquisitions",
    "impairment_goodwill_intangibles",
    "intangibles_ex_goodwill", "preferred_stock", "minority_interest",
    "short_term_investments", "long_term_investments", "short_term_borrowings",
}
# Deliberately NOT zero-on-absence: gain_on_property_sales (PLD reports it under a custom tag).
# Its absence makes REIT fair value insufficient. Debt has its own resolver (screener/debt.py).

# ---- Reusable requirements ----
# EBIT, in order of preference. Pretax income alone is a conservative last resort (it
# understates EBIT by the interest expense), used e.g. by homebuilders that capitalize
# interest into cost of sales and report no interest expense line (DHI).
EBIT = [["operating_income"], ["pretax_income", "interest_expense"], ["pretax_income"]]
EBIT_CONSERVATIVE_BASIS = "pretax_income"
GROSS_PROFIT = [["gross_profit"], ["revenue", "cost_of_revenue"]]
SGA = [["sga_expense"], ["selling_marketing_expense", "g_and_a_expense"]]
TOTAL_LIABILITIES = [["total_liabilities"], ["liabilities_and_equity", "stockholders_equity"]]
# Total debt comes from screener/debt.py; "total_debt" is available when it resolves.
DEBT = [["total_debt"]]
LONG_TERM_DEBT = [["long_term_debt_noncurrent"], ["long_term_debt_total"], ["total_debt"]]
# Multi-class filers' cover-page share counts are dimensional and absent from companyfacts
# (META, V, CMCSA, ...). Fall back to weighted diluted shares, then to shares implied by
# net income / diluted EPS (for V this yields Class A-equivalent shares, matching V's price).
# BRK-B needs a class ratio (EPS is per Class A share) - handled in M5.
SHARES = [["shares_outstanding"], ["diluted_shares"], ["net_income", "eps_diluted"]]


def req(*concepts: str) -> list[list[str]]:
    return [list(concepts)]


# ---- Metric inputs (annual values, current and prior year unless noted) ----
METRIC_INPUTS: dict[str, dict[str, list[list[str]]]] = {
    "ev_ebit": {"ebit": EBIT, "debt": DEBT, "cash": req("cash"), "shares": SHARES},
    "gp_assets": {"gross_profit": GROSS_PROFIT, "total_assets": req("total_assets")},
    "sloan_accruals": {"inputs": req("net_income", "operating_cash_flow", "investing_cash_flow", "total_assets")},
    "piotroski_f": {
        "profitability": req("net_income", "operating_cash_flow", "total_assets"),
        "leverage": LONG_TERM_DEBT,
        "liquidity": req("current_assets", "current_liabilities"),
        "dilution": SHARES,
        "gross_margin": GROSS_PROFIT,
        "asset_turnover": req("revenue", "total_assets"),
    },
    "beneish_m": {
        "core": req("receivables", "revenue", "current_assets", "ppe_net", "total_assets",
                    "depreciation_amortization", "net_income", "operating_cash_flow",
                    "current_liabilities"),
        "gross_profit": GROSS_PROFIT,
        "sga": SGA,
        "debt": DEBT,
    },
    "shareholder_yield": {"flows": req("dividends_paid", "buybacks", "stock_issuance"), "debt": DEBT},
    "roic_reinvestment": {"ebit": EBIT, "tax": req("income_tax", "pretax_income"),
                          "capital": req("stockholders_equity", "cash"), "debt": DEBT,
                          "reinvestment": req("capex", "depreciation_amortization")},
    "rd_capitalized": {"rd": req("rd_expense")},  # 5 prior years of R&D, straight-line
    "p_tbv": {"tbv": req("stockholders_equity", "preferred_stock", "goodwill", "intangibles_ex_goodwill"),
              "shares": SHARES},
    "rotce": {"inputs": req("net_income", "stockholders_equity", "preferred_stock", "goodwill",
                            "intangibles_ex_goodwill")},
    "p_ffo": {"ffo": req("net_income", "depreciation_amortization", "gain_on_property_sales"),
              "shares": SHARES},
    "p_b": {"inputs": req("stockholders_equity", "preferred_stock"), "shares": SHARES},
    "roe": {"inputs": req("net_income", "stockholders_equity")},
}

# Rule 1: banks and insurers skip cash-flow-based quality metrics (their operating cash flow
# isn't meaningful, e.g. JPM OCF = -$148B). Piotroski is skipped entirely for them because
# its cash-flow tests are integral to the score.
MODEL_RULES: dict[str, dict] = {
    "standard": {
        "valuation": "ev_ebit",
        "metrics": ["ev_ebit", "gp_assets", "sloan_accruals", "piotroski_f", "beneish_m",
                    "shareholder_yield", "roic_reinvestment", "rd_capitalized"],
        "disqualifiers": ["beneish_m", "piotroski_f"],
        "fair_value_inputs": {"ebit_3y": EBIT, "debt": DEBT, "cash": req("cash"),
                              "minority_interest": req("minority_interest"),
                              "preferred_stock": req("preferred_stock"),
                              "shares": SHARES},
    },
    "reit": {
        "valuation": "p_ffo",
        "metrics": ["p_ffo", "sloan_accruals", "piotroski_f", "beneish_m", "shareholder_yield"],
        "disqualifiers": ["beneish_m", "piotroski_f"],
        # Fair value = peer-median P/FFO x FFO per share. Peers = same GICS sub-industry when
        # at least `min_peers` REITs in it have a computable P/FFO; otherwise all REITs.
        "fair_value_method": {"type": "peer_median_multiple", "multiple": "p_ffo",
                              "peer_group": "gics_sub_industry", "min_peers": 5,
                              "fallback_group": "sector_model"},
        "fair_value_inputs": {"ffo": req("net_income", "depreciation_amortization",
                                         "gain_on_property_sales"),
                              "shares": SHARES},
    },
    "bank": {
        "valuation": "p_tbv",
        "metrics": ["p_tbv", "rotce", "shareholder_yield"],
        "disqualifiers": [],  # no Beneish, no Piotroski
        # Fair P/TBV = cross-sectional OLS of P/TBV on ROTCE across all banks, evaluated at the
        # bank's ROTCE. Fair value = fair P/TBV x TBV per share. R-squared logged every run.
        "fair_value_method": {"type": "regression", "y": "p_tbv", "x": "rotce",
                              "group": "sector_model"},
        "fair_value_inputs": {"tbv": req("stockholders_equity", "preferred_stock", "goodwill",
                                         "intangibles_ex_goodwill"),
                              "rotce": req("net_income"),
                              "shares": SHARES},
    },
    "insurer": {
        "valuation": "p_b",
        "metrics": ["p_b", "roe", "shareholder_yield"],
        "disqualifiers": [],  # no Beneish, no Piotroski
        # Fair P/B = cross-sectional OLS of P/B on ROE across all insurers, evaluated at the
        # insurer's ROE. Fair value = fair P/B x book value per share. R-squared logged every run.
        "fair_value_method": {"type": "regression", "y": "p_b", "x": "roe",
                              "group": "sector_model"},
        "fair_value_inputs": {"book_value": req("stockholders_equity", "preferred_stock"),
                              "roe": req("net_income"),
                              "shares": SHARES},
    },
}
MODEL_RULES["standard"]["fair_value_method"] = {
    # Fair value = fair EV/EBIT x min(TTM, 3-yr average EBIT), minus net debt, per share.
    # Fair EV/EBIT = shrinkage blend, capped:
    #   w = n / (n + shrinkage_k) with n = valid peers in the GICS sub-industry (w = 0 if n < 2)
    #   fair = w x sub-industry median + (1 - w) x anchor median, capped at max_fair_multiple
    #   anchor = industry group if >= anchor_min valid peers, else sector, else all standard
    # Valid = EBIT > 0 and EV/EBIT <= max_multiple (higher means near-zero EBIT relative to EV).
    # Multiples are winsorized at the 5th/95th percentile within each group before the median.
    # Logged per company: n, w, both medians, anchor level, and whether the cap applied.
    "type": "shrinkage_peer_multiple", "multiple": "ev_ebit", "base": "min(ebit_ttm, ebit_3y_avg)",
    "sub_level": "gics_sub_industry", "min_sub_peers": 2, "shrinkage_k": 8,
    "anchor_levels": ["gics_industry_group", "gics_sector", "sector_model"], "anchor_min": 8,
    "max_fair_multiple": 25.0, "max_multiple": 100, "winsorize": (0.05, 0.95),
}


def derived_concepts(cik: int, values: dict[str, float]) -> tuple[set[str], DebtResult]:
    """Pseudo-concepts inferred from a snapshot of {concept: value}, plus the debt resolution."""
    debt = resolve_debt(cik, values)
    return ({"total_debt"} if debt.ok else set()), debt


def met_by(requirement: list[list[str]], available: set[str]) -> str | None:
    """The first satisfied alternative, joined with '+', or None."""
    have = available | ABSENT_MEANS_ZERO
    for alt in requirement:
        if all(c in have for c in alt):
            return "+".join(alt)
    return None


def is_met(requirement: list[list[str]], available: set[str]) -> bool:
    have = available | ABSENT_MEANS_ZERO
    return any(all(c in have for c in alt) for alt in requirement)


def missing_for(requirement: list[list[str]], available: set[str]) -> list[str]:
    """Concepts missing from the closest alternative (fewest missing), or [] if met.

    When alternatives tie, they are joined: ["long_term_debt_total | long_term_debt_current"].
    """
    have = available | ABSENT_MEANS_ZERO
    gaps = [[c for c in alt if c not in have] for alt in requirement]
    fewest = min(len(g) for g in gaps)
    tied = [g for g in gaps if len(g) == fewest]
    if fewest == 0 or len(tied) == 1:
        return tied[0]
    return [" | ".join("+".join(g) for g in tied)]
