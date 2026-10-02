"""Canonical concept -> ordered XBRL tag fallbacks.

For each reporting period, the first tag in the list that has a value wins. Companies switch
tags over time (e.g. SalesRevenueNet -> RevenueFromContractWithCustomer... after ASC 606 in
2018), so resolution is per period, not per company.

Each entry: (unit, period_type, [(taxonomy, tag), ...])
  period_type: "duration" (income/cash-flow) or "instant" (balance sheet, share counts)
"""

G = "us-gaap"
DEI = "dei"


def _g(*tags: str) -> list[tuple[str, str]]:
    return [(G, t) for t in tags]


TAG_MAP: dict[str, tuple[str, str, list[tuple[str, str]]]] = {
    # ---- Income statement ----
    "revenue": ("USD", "duration", _g(
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "RevenuesNetOfInterestExpense",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenuesAndOtherIncome",
    )),
    "cost_of_revenue": ("USD", "duration", _g(
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
        "CostOfServices",
    )),
    "gross_profit": ("USD", "duration", _g("GrossProfit")),
    "operating_income": ("USD", "duration", _g("OperatingIncomeLoss")),
    "pretax_income": ("USD", "duration", _g(
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    )),
    "income_tax": ("USD", "duration", _g("IncomeTaxExpenseBenefit")),
    "interest_expense": ("USD", "duration", _g(
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestExpenseDebt",
        "InterestAndDebtExpense",
    )),
    "net_income": ("USD", "duration", _g(
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "ProfitLoss",
    )),
    # Excluding acquired IPR&D first: acquired IPR&D is an acquisition, not organic R&D spend,
    # and some filers (e.g. JNJ) use the base tag only for a minor line item.
    "rd_expense": ("USD", "duration", _g(
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
        "ResearchAndDevelopmentExpense",
    )),
    "sga_expense": ("USD", "duration", _g("SellingGeneralAndAdministrativeExpense")),
    # Components for filers that don't report a combined SG&A line (e.g. MSFT).
    "selling_marketing_expense": ("USD", "duration", _g("SellingAndMarketingExpense")),
    "g_and_a_expense": ("USD", "duration", _g("GeneralAndAdministrativeExpense")),
    "depreciation_amortization": ("USD", "duration", _g(
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "Depreciation",
    )),
    "gain_on_property_sales": ("USD", "duration", _g(
        "GainsLossesOnSalesOfInvestmentRealEstate",
        "GainLossOnSaleOfRealEstateInvestmentProperty",
        "GainLossOnDispositionOfRealEstateInvestmentProperty",
        "GainLossOnSaleOfProperties",
        "GainLossOnDispositionOfProperty",
        "GainLossOnSaleOfPropertyPlantEquipment",
        "GainLossOnDispositionOfAssets",
    )),
    # ---- Cash flow ----
    "operating_cash_flow": ("USD", "duration", _g(
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    )),
    "investing_cash_flow": ("USD", "duration", _g(
        "NetCashProvidedByUsedInInvestingActivities",
        "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
    )),
    "capex": ("USD", "duration", _g(
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
    )),
    "dividends_paid": ("USD", "duration", _g(
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
        "PaymentsOfOrdinaryDividends",
    )),
    "buybacks": ("USD", "duration", _g(
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfEquity",
    )),
    "stock_issuance": ("USD", "duration", _g(
        "ProceedsFromIssuanceOfCommonStock",
        "ProceedsFromIssuanceOrSaleOfEquity",
        "ProceedsFromStockOptionsExercised",
    )),
    # ---- Balance sheet ----
    "total_assets": ("USD", "instant", _g("Assets")),
    "current_assets": ("USD", "instant", _g("AssetsCurrent")),
    "current_liabilities": ("USD", "instant", _g("LiabilitiesCurrent")),
    "total_liabilities": ("USD", "instant", _g("Liabilities")),
    # For filers without a total-liabilities line (e.g. WMT): liabilities = this - total equity.
    "liabilities_and_equity": ("USD", "instant", _g("LiabilitiesAndStockholdersEquity")),
    "cash": ("USD", "instant", _g(
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndDueFromBanks",
        "Cash",
    )),
    "short_term_investments": ("USD", "instant", _g(
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    )),
    "receivables": ("USD", "instant", _g(
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
    )),
    "ppe_net": ("USD", "instant", _g(
        "PropertyPlantAndEquipmentNet",
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
    )),
    "real_estate_net": ("USD", "instant", _g("RealEstateInvestmentPropertyNet")),
    "long_term_investments": ("USD", "instant", _g(
        "LongTermInvestments",
        "MarketableSecuritiesNoncurrent",
    )),
    "goodwill": ("USD", "instant", _g("Goodwill")),
    "intangibles_ex_goodwill": ("USD", "instant", _g(
        "IntangibleAssetsNetExcludingGoodwill",
        "FiniteLivedIntangibleAssetsNet",
    )),
    "long_term_debt_noncurrent": ("USD", "instant", _g(
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
    )),
    "long_term_debt_current": ("USD", "instant", _g(
        "LongTermDebtCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
        "DebtCurrent",
    )),
    "long_term_debt_total": ("USD", "instant", _g(
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
        "DebtInstrumentCarryingAmount",
    )),
    "short_term_borrowings": ("USD", "instant", _g(
        "ShortTermBorrowings",
        "CommercialPaper",
    )),
    "stockholders_equity": ("USD", "instant", _g(
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    )),
    "preferred_stock": ("USD", "instant", _g(
        "PreferredStockValue",
        "PreferredStockValueOutstanding",
    )),
    "minority_interest": ("USD", "instant", _g("MinorityInterest")),
    # ---- Shares ----
    "shares_outstanding": ("shares", "instant", [
        (DEI, "EntityCommonStockSharesOutstanding"),
        (G, "CommonStockSharesOutstanding"),
    ]),
    "diluted_shares": ("shares", "duration", _g(
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
        "WeightedAverageNumberOfSharesOutstandingBasic",  # last resort, e.g. XOM reports basic only
    )),
}
