"""GICS hierarchy (2023 structure): sub-industry -> industry group -> sector.

Only sub-industry names are available from the constituent list, so industry group is derived
here. Lists cover every sub-industry in the current S&P 500 plus common neighbours; an unknown
sub-industry raises so the map gets updated rather than silently misgrouped.
"""

INDUSTRY_GROUPS: dict[str, tuple[str, list[str]]] = {
    # industry group: (sector, [sub-industries])
    "Energy": ("Energy", [
        "Oil & Gas Drilling", "Oil & Gas Equipment & Services", "Integrated Oil & Gas",
        "Oil & Gas Exploration & Production", "Oil & Gas Refining & Marketing",
        "Oil & Gas Storage & Transportation", "Coal & Consumable Fuels"]),
    "Materials": ("Materials", [
        "Commodity Chemicals", "Diversified Chemicals", "Fertilizers & Agricultural Chemicals",
        "Industrial Gases", "Specialty Chemicals", "Construction Materials",
        "Metal, Glass & Plastic Containers", "Paper & Plastic Packaging Products & Materials",
        "Aluminum", "Diversified Metals & Mining", "Copper", "Gold", "Precious Metals & Minerals",
        "Silver", "Steel", "Forest Products", "Paper Products"]),
    "Capital Goods": ("Industrials", [
        "Aerospace & Defense", "Building Products", "Construction & Engineering",
        "Electrical Components & Equipment", "Heavy Electrical Equipment",
        "Industrial Conglomerates", "Construction Machinery & Heavy Transportation Equipment",
        "Agricultural & Farm Machinery", "Industrial Machinery & Supplies & Components",
        "Trading Companies & Distributors"]),
    "Commercial & Professional Services": ("Industrials", [
        "Commercial Printing", "Environmental & Facilities Services", "Office Services & Supplies",
        "Diversified Support Services", "Security & Alarm Services",
        "Human Resource & Employment Services", "Research & Consulting Services",
        "Data Processing & Outsourced Services"]),
    "Transportation": ("Industrials", [
        "Air Freight & Logistics", "Passenger Airlines", "Marine Transportation",
        "Rail Transportation", "Cargo Ground Transportation", "Passenger Ground Transportation",
        "Airport Services", "Highways & Railtracks", "Marine Ports & Services"]),
    "Automobiles & Components": ("Consumer Discretionary", [
        "Automotive Parts & Equipment", "Tires & Rubber", "Automobile Manufacturers",
        "Motorcycle Manufacturers"]),
    "Consumer Durables & Apparel": ("Consumer Discretionary", [
        "Consumer Electronics", "Home Furnishings", "Homebuilding", "Household Appliances",
        "Housewares & Specialties", "Leisure Products", "Apparel, Accessories & Luxury Goods",
        "Footwear", "Textiles"]),
    "Consumer Services": ("Consumer Discretionary", [
        "Casinos & Gaming", "Hotels, Resorts & Cruise Lines", "Leisure Facilities", "Restaurants",
        "Education Services", "Specialized Consumer Services"]),
    "Consumer Discretionary Distribution & Retail": ("Consumer Discretionary", [
        "Distributors", "Broadline Retail", "Apparel Retail", "Computer & Electronics Retail",
        "Home Improvement Retail", "Other Specialty Retail", "Automotive Retail",
        "Homefurnishing Retail"]),
    "Consumer Staples Distribution & Retail": ("Consumer Staples", [
        "Drug Retail", "Food Distributors", "Food Retail", "Consumer Staples Merchandise Retail"]),
    "Food, Beverage & Tobacco": ("Consumer Staples", [
        "Brewers", "Distillers & Vintners", "Soft Drinks & Non-alcoholic Beverages",
        "Agricultural Products & Services", "Packaged Foods & Meats", "Tobacco"]),
    "Household & Personal Products": ("Consumer Staples", [
        "Household Products", "Personal Care Products"]),
    "Health Care Equipment & Services": ("Health Care", [
        "Health Care Equipment", "Health Care Supplies", "Health Care Distributors",
        "Health Care Services", "Health Care Facilities", "Managed Health Care",
        "Health Care Technology"]),
    "Pharmaceuticals, Biotechnology & Life Sciences": ("Health Care", [
        "Biotechnology", "Pharmaceuticals", "Life Sciences Tools & Services"]),
    "Banks": ("Financials", ["Diversified Banks", "Regional Banks"]),
    "Financial Services": ("Financials", [
        "Diversified Financial Services", "Multi-Sector Holdings", "Specialized Finance",
        "Commercial & Residential Mortgage Finance", "Transaction & Payment Processing Services",
        "Consumer Finance", "Asset Management & Custody Banks", "Investment Banking & Brokerage",
        "Diversified Capital Markets", "Financial Exchanges & Data", "Mortgage REITs"]),
    "Insurance": ("Financials", [
        "Insurance Brokers", "Life & Health Insurance", "Multi-line Insurance",
        "Property & Casualty Insurance", "Reinsurance"]),
    "Software & Services": ("Information Technology", [
        "IT Consulting & Other Services", "Internet Services & Infrastructure",
        "Application Software", "Systems Software"]),
    "Technology Hardware & Equipment": ("Information Technology", [
        "Communications Equipment", "Technology Hardware, Storage & Peripherals",
        "Electronic Equipment & Instruments", "Electronic Components",
        "Electronic Manufacturing Services", "Technology Distributors"]),
    "Semiconductors & Semiconductor Equipment": ("Information Technology", [
        "Semiconductor Materials & Equipment", "Semiconductors"]),
    "Telecommunication Services": ("Communication Services", [
        "Alternative Carriers", "Integrated Telecommunication Services",
        "Wireless Telecommunication Services"]),
    "Media & Entertainment": ("Communication Services", [
        "Advertising", "Broadcasting", "Cable & Satellite", "Publishing",
        "Movies & Entertainment", "Interactive Home Entertainment", "Interactive Media & Services"]),
    "Utilities": ("Utilities", [
        "Electric Utilities", "Gas Utilities", "Multi-Utilities", "Water Utilities",
        "Independent Power Producers & Energy Traders", "Renewable Electricity"]),
    "Equity Real Estate Investment Trusts (REITs)": ("Real Estate", [
        "Diversified REITs", "Industrial REITs", "Hotel & Resort REITs", "Office REITs",
        "Health Care REITs", "Multi-Family Residential REITs", "Single-Family Residential REITs",
        "Retail REITs", "Other Specialized REITs", "Self-Storage REITs", "Telecom Tower REITs",
        "Timber REITs", "Data Center REITs"]),
    "Real Estate Management & Development": ("Real Estate", [
        "Diversified Real Estate Activities", "Real Estate Operating Companies",
        "Real Estate Development", "Real Estate Services"]),
}

SUB_INDUSTRY_TO_GROUP = {sub: group for group, (_, subs) in INDUSTRY_GROUPS.items() for sub in subs}


def industry_group(sub_industry: str, sector: str) -> str:
    group = SUB_INDUSTRY_TO_GROUP.get(sub_industry)
    if group is None:
        raise KeyError(f"GICS sub-industry not in screener/gics.py: {sub_industry!r}")
    expected_sector = INDUSTRY_GROUPS[group][0]
    if expected_sector != sector:
        raise ValueError(f"{sub_industry!r} maps to {group!r} ({expected_sector}), "
                         f"but the constituent list says sector {sector!r}")
    return group
