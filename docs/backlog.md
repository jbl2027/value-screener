# Backlog

Items deferred to after the milestone noted.

## After Milestone 5
- **Fill custom-tag gaps from the SEC Financial Statement Data Sets** (free quarterly bulk
  files at https://www.sec.gov/dera/data/financial-statement-data-sets). Unlike the
  companyfacts API, they include company extension tags. Known gaps: NEE capex and interest
  expense, CAT current long-term debt, PLD gains on property sales (post-2018). Milestone 3's
  `data/mapping_report.csv` lists the rest.

## Rules decided, implemented in Milestones 5–6 (data designed in `screener/rules.py`)
1. Banks and insurers: no Sloan accruals, Piotroski, or Beneish. The Beneish disqualifier
   applies to standard and REIT models only.
2. Data confidence: if any fair-value / margin-of-safety input is missing, status is
   `insufficient_data` (never flagged), with the missing inputs logged.
3. Captive finance (CAT, DE, GM, F): flags marked low confidence.

## Known data gaps from Milestone 3 (see `data/mapping_report.csv`)
- Per-class share data only (V, STZ, ERIE, ARES, BRK-B): no total shares or EPS in
  companyfacts. BRK-B also needs the Class A : Class B = 1 : 1500 ratio for market cap.
- Debt reported only as a noncurrent piece (CDNS, NOW, DASH, PANW, CRWD, REGN, ...):
  current maturities may be zero or under a custom tag (as with CAT). Treated as missing.
- Zero long-term debt is inferred when no debt tags exist and interest is negligible;
  `fundamentals_summary.csv` `debt_basis = no_long_term_debt` lists them. Known exceptions
  go in `rules.HAS_UNTAGGED_DEBT`.
- CLX: FY2026 10-K (filed 2026-08-07) not yet in companyfacts; marked stale.
- New entities with short XBRL history: HONA (2026 spin-off), PSKY (2025 merger).

## Open assumptions to confirm
- REIT fair value = sector-median P/FFO × FFO per share.
- Insurer fair value = sector-median P/B × book value per share.
