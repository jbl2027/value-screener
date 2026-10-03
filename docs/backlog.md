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
4. Debt is resolved by `screener/debt.py`: aggregate totals first; noncurrent-only treats
   current maturities as zero; instrument parts are summed only when no total exists (one tag
   per overlap group). Those fallbacks are low confidence and rejected as insufficient if the
   implied interest rate (interest / debt) exceeds 8%. The check cannot run when interest
   expense is untagged (DHI, DASH, PANW) and only catches gross omissions (CAT passes at 5.1%
   though its current maturities are likely missing).
5. Fair value methods (configured in `rules.MODEL_RULES[...]["fair_value_method"]`):
   - Standard: sector-median EV/EBIT x 3-yr average EBIT, minus net debt, per share.
   - REIT: peer-median P/FFO x FFO per share; peers = GICS sub-industry if at least 5 REITs
     in it have a computable P/FFO, else all REITs. (Only Multi-Family and Retail have 5
     members today, and Retail has 3 missing gains, so most REITs use the all-REIT median.)
   - Bank: OLS of P/TBV on ROTCE across banks; fair P/TBV at the bank's ROTCE x TBV/share.
   - Insurer: OLS of P/B on ROE across insurers; fair P/B at the insurer's ROE x BV/share.
   - Log regression R-squared every run.

## Known data gaps from Milestone 3 (see `data/mapping_report.csv`)
- Per-class share data only (V, STZ, ERIE, ARES, BRK-B): no total shares or EPS in
  companyfacts. BRK-B also needs the Class A : Class B = 1 : 1500 ratio for market cap.
- Zero long-term debt is inferred when no debt tags exist and interest is negligible;
  `fundamentals_summary.csv` `debt_basis = no_long_term_debt` lists them. Known exceptions
  go in `debt.HAS_UNTAGGED_DEBT`.
- 12 standard companies have no debt tags and non-negligible interest (debt under custom
  tags): see `debt_problem = no_debt_tags_found`.
- CLX: FY2026 10-K (filed 2026-08-07) not yet in companyfacts; marked stale.
- New entities with short XBRL history: HONA (2026 spin-off), PSKY (2025 merger).

## Open questions
- Standard-model peer group for "sector-median EV/EBIT": currently configured as SIC
  division (Manufacturing alone has ~200 companies). Confirm before Milestone 6, or use
  GICS sector / 2-digit SIC.
