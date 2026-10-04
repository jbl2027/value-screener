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
   - Standard: peer-median EV/EBIT x 3-yr average EBIT, minus net debt, per share. Peers =
     GICS industry group (derived in `screener/gics.py`) if >= 8 valid EV/EBITs, else GICS
     sector, else all standard companies. Exclude EBIT <= 0 or EV/EBIT > 100; winsorize
     5/95 within group. Negative 3-yr average EBIT -> `not_applicable`. Log peer group/count.
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

## Valuation (Milestone 6): decisions applied
- AXP, RJF, SYF routed to the bank model.
- Fair value base = min(TTM, 3-yr average) for EBIT (standard) and FFO (REIT).
- Post-corporate-action regime: event (8-K Item 2.01, or rules.MANUAL_CORPORATE_ACTIONS) after
  the latest 10-K's fiscal year end and already followed by a 10-Q -> TTM only, low confidence,
  until a 10-K covering a fiscal year that ends after the event. (Interpretation: "next 10-K"
  = first 10-K whose fiscal year ends after the event, since a 10-K filed shortly after the
  event still covers the pre-event year.)
- When a 10-Q restates the prior-year YTD (e.g. discontinued operations), TTM is rebuilt as
  current YTD + (FY - original prior YTD) x restatement ratio.
- Tiers: flag = normal confidence and 30% <= MoS <= 60%; watch = low confidence or MoS > 60%.

## Valuation: open items
- Spin-offs can be announced under 8-K Item 8.01 rather than 2.01 (Comcast/Versant), so the
  automatic scan misses them; CMCSA is in MANUAL_CORPORATE_ACTIONS. Possible fix: scan the text
  of Item 8.01 8-Ks for completed separations/distributions.
- Insurer P/B ~ ROE regression is weak (R2 0.24, n=17), so insurer valuations are low
  confidence (watch at most).

## Metrics (Milestone 5): decisions applied in Milestone 6
- Piotroski: 134 standard companies have < 9 testable criteria (144 lack COGS, so no gross
  margin test). `f_score` is set only with all 9; `f_points`/`f_tests` hold partial results.
- Beneish: strict `m_score` needs all 8 indices (234 of 434); `m_score_imputed` sets up to 2
  missing indices (not TATA) to 1 (390 of 434). LOW trips the rule (DSRI 11x) because of 2025
  B2B acquisitions, a known Beneish M&A false positive.
- Bank shareholder yield excludes debt paydown (bank debt is operating funding).
- SYF (consumer finance, deposit-funded) sits on the standard model; EV/EBIT 2.9x is not
  meaningful. Consider routing to the bank model (as with GS/MS/SCHW).
- CTVA: price implies ~$8B market cap against pre-separation financials; Corteva filed a
  cluster of 8-Ks on 2026-09-30/10-01, consistent with a just-completed separation. Corporate
  actions (spin-offs) aren't detected yet.

## Prices (Milestone 4)
- Tiingo free tier: 50 requests/hour, 1,000/day, 500 unique symbols/month, 1 GB/month,
  internal use only (no sharing or displaying the data to others).
- Daily closes come from the bulk IEX snapshot (1 request/day, ~11 MB). Undocumented whether
  it counts toward the 500 unique symbols; it worked on the free key.
- Snapshot closes are unadjusted. Milestone 10 performance tracking needs split/dividend-
  adjusted returns: fetch EOD (adjClose) per flagged ticker only, to stay under 500 symbols.
- No historical backfill yet: a per-ticker EOD backfill of all 503 tickers would take ~11
  hours at 50 requests/hour and exceed the 500-symbol cap.

