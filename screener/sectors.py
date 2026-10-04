"""SIC-based sector classification and valuation-model routing.

sector_model decides which valuation/quality metrics apply:
  bank     -> P/TBV, ROTCE
  reit     -> P/FFO (approximate)
  insurer  -> P/B, ROE
  standard -> EV/EBIT and the rest of the standard factor set
"""

BANK_SICS = {6021, 6022, 6029, 6035, 6036}
REIT_SICS = {6798}
# Balance-sheet insurance carriers (life, A&H, P&C, surety, title, NEC). Two exclusions stay
# on the standard model because they are valued on earnings, not book:
#   6324 managed care (UNH, ELV, CI, CNC, HUM) and 6411 insurance brokers (AON, AJG, ...).
INSURER_SICS = {6311, 6321, 6331, 6351, 6361, 6399}

# Manual overrides where SIC alone routes a company to the wrong model.
SECTOR_MODEL_OVERRIDES = {
    "INVH": "reit",   # single-family rental REIT filed under SIC 6510
    "GS": "bank",     # bank holding company filed under SIC 6211
    "MS": "bank",     # bank holding company filed under SIC 6211
    "SCHW": "bank",   # bank holding company filed under SIC 6211
    "SYF": "bank",    # deposit-funded consumer lender (deposits 79% of liabilities)
    "AXP": "bank",    # bank holding company (deposits 57% of liabilities)
    "RJF": "bank",    # Raymond James Bank (deposits 78% of liabilities)
}

# Industrials/autos with captive finance arms. Finance-arm debt inflates EV, so EV/EBIT-based
# fair values and flags for these are marked lower confidence.
CAPTIVE_FINANCE = {"CAT", "DE", "GM", "F"}

SIC_DIVISIONS = [
    (100, 999, "Agriculture, Forestry & Fishing"),
    (1000, 1499, "Mining"),
    (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"),
    (4000, 4999, "Transportation, Communications & Utilities"),
    (5000, 5199, "Wholesale Trade"),
    (5200, 5999, "Retail Trade"),
    (6000, 6999, "Finance, Insurance & Real Estate"),
    (7000, 8999, "Services"),
    (9100, 9999, "Public Administration"),
]


def sic_division(sic: int | None) -> str | None:
    if sic is None:
        return None
    for lo, hi, name in SIC_DIVISIONS:
        if lo <= sic <= hi:
            return name
    return None


def sector_model(ticker: str, sic: int | None) -> tuple[str, str]:
    """Return (sector_model, source) where source is 'override' or 'sic'."""
    if ticker in SECTOR_MODEL_OVERRIDES:
        return SECTOR_MODEL_OVERRIDES[ticker], "override"
    if sic in BANK_SICS:
        return "bank", "sic"
    if sic in REIT_SICS:
        return "reit", "sic"
    if sic in INSURER_SICS:
        return "insurer", "sic"
    return "standard", "sic"
