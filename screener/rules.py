"""Per-model metric applicability, disqualifiers, and input requirements.

Declared here so the data pipeline (Milestone 3) can log coverage against exactly what the
metrics (Milestone 5) and fair value / flags (Milestone 6) will need.

Requirements are lists of alternatives; each alternative is a list of concepts. A requirement
is met when every concept in at least one alternative is available. Concepts in
ABSENT_MEANS_ZERO count as available (value 0) when a company doesn't report them.
"""

# ---- Statuses produced by Milestone 6 ----
STATUS_FLAG = "flag"                    # MoS >= 30% and not disqualified
STATUS_NO_FLAG = "no_flag"
STATUS_DISQUALIFIED = "disqualified"    # Beneish / F-Score rule tripped (where applicable)
STATUS_INSUFFICIENT = "insufficient_data"  # a fair-value / MoS input is missing; never flagged
CONFIDENCE_NORMAL = "normal"
CONFIDENCE_LOW = "low"                  # e.g. captive finance (see sectors.CAPTIVE_FINANCE)

# Not reporting these is normal and means zero (e.g. AAPL reports no goodwill line).
ABSENT_MEANS_ZERO = {
    "rd_expense", "dividends_paid", "buybacks", "stock_issuance", "goodwill",
    "intangibles_ex_goodwill", "preferred_stock", "minority_interest",
    "short_term_investments", "long_term_investments", "short_term_borrowings",
}
# Deliberately NOT zero-on-absence: long_term_debt_current (CAT reports it under a custom tag)
# and gain_on_property_sales (PLD likewise). Their absence makes fair value insufficient.

# ---- Reusable requirements ----
EBIT = [["operating_income"], ["pretax_income", "interest_expense"]]
GROSS_PROFIT = [["gross_profit"], ["revenue", "cost_of_revenue"]]
SGA = [["sga_expense"], ["selling_marketing_expense", "g_and_a_expense"]]
TOTAL_LIABILITIES = [["total_liabilities"], ["liabilities_and_equity", "stockholders_equity"]]
# Total debt: reported total, or both noncurrent and current pieces (+ short-term borrowings).
DEBT = [["long_term_debt_total", "short_term_borrowings"],
        ["long_term_debt_noncurrent", "long_term_debt_current", "short_term_borrowings"]]


def req(*concepts: str) -> list[list[str]]:
    return [list(concepts)]


# ---- Metric inputs (annual values, current and prior year unless noted) ----
METRIC_INPUTS: dict[str, dict[str, list[list[str]]]] = {
    "ev_ebit": {"ebit": EBIT, "debt": DEBT, "cash": req("cash"), "shares": req("shares_outstanding")},
    "gp_assets": {"gross_profit": GROSS_PROFIT, "total_assets": req("total_assets")},
    "sloan_accruals": {"inputs": req("net_income", "operating_cash_flow", "investing_cash_flow", "total_assets")},
    "piotroski_f": {
        "profitability": req("net_income", "operating_cash_flow", "total_assets"),
        "leverage_liquidity": req("long_term_debt_noncurrent", "current_assets", "current_liabilities",
                                  "shares_outstanding"),
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
              "shares": req("shares_outstanding")},
    "rotce": {"inputs": req("net_income", "stockholders_equity", "preferred_stock", "goodwill",
                            "intangibles_ex_goodwill")},
    "p_ffo": {"ffo": req("net_income", "depreciation_amortization", "gain_on_property_sales"),
              "shares": req("shares_outstanding")},
    "p_b": {"inputs": req("stockholders_equity", "preferred_stock", "shares_outstanding")},
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
                              "shares": req("shares_outstanding")},
    },
    "reit": {
        "valuation": "p_ffo",
        "metrics": ["p_ffo", "sloan_accruals", "piotroski_f", "beneish_m", "shareholder_yield"],
        "disqualifiers": ["beneish_m", "piotroski_f"],
        # Assumption pending confirmation: fair value = sector-median P/FFO x FFO per share.
        "fair_value_inputs": {"ffo": req("net_income", "depreciation_amortization",
                                         "gain_on_property_sales"),
                              "shares": req("shares_outstanding")},
    },
    "bank": {
        "valuation": "p_tbv",
        "metrics": ["p_tbv", "rotce", "shareholder_yield"],
        "disqualifiers": [],  # no Beneish, no Piotroski
        "fair_value_inputs": {"tbv": req("stockholders_equity", "preferred_stock", "goodwill",
                                         "intangibles_ex_goodwill"),
                              "shares": req("shares_outstanding")},
    },
    "insurer": {
        "valuation": "p_b",
        "metrics": ["p_b", "roe", "shareholder_yield"],
        "disqualifiers": [],  # no Beneish, no Piotroski
        # Assumption pending confirmation: fair value = sector-median P/B x book value per share.
        "fair_value_inputs": {"book_value": req("stockholders_equity", "preferred_stock"),
                              "shares": req("shares_outstanding")},
    },
}


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
