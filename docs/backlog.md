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
- Valuation EBIT (TTM, FY, prior FY, 3-yr average) adds back goodwill and intangible impairment
  charges (2026-10-07; `screener/sensitivities.py`, charges from `metrics.Calc.impairment_*`).
  Per period: GoodwillAndIntangibleAssetImpairment if reported, else GoodwillImpairmentLoss +
  intangibles (ImpairmentOfIntangibleAssetsExcludingGoodwill, else indefinite- + finite-lived),
  so nothing is counted twice; AssetImpairmentCharges and *NetOfTax tags excluded. TTM uses the
  same FY + YTD - prior YTD periods as TTM EBIT. Applied before peer medians; recorded in
  `ebit_adjustment` (not warnings). Metrics (operating profitability, F-Score, Beneish, ROIC)
  keep reported EBIT; the major-impairment gate detector is unchanged. Effect on 2026-10-07:
  148 of 431 standard companies adjusted; SJM fair value $1.78 -> $155.96; watch +3,
  not_applicable -7. Continuing-operations goodwill impairment sits inside operating income
  (ASC 350-20-45-2). Discontinued-operations safeguard (2026-10-08): companyfacts carries no
  non-dimensional discontinued-ops impairment tags (none in the universe since 2022), so a
  period's charge is skipped when the same period shows a discontinued-operations loss >= 50%
  of it (`metrics.IMPAIRMENT_DISC_OPS_RATIO`; listed as impairment_addback_skipped). TTM
  subtracts the prior-year YTD at its raw amount only when the FY charge was added back.
  Changed 6 companies: DLTR (Family Dollar goodwill, restated into discontinued ops; watch ->
  no_flag), BAX, APP, QCOM, NWS, PPG.
- Post-corporate-action regime: event (8-K Item 2.01, or rules.MANUAL_CORPORATE_ACTIONS) after
  the latest 10-K's fiscal year end and already followed by a 10-Q -> TTM only, low confidence,
  until a 10-K covering a fiscal year that ends after the event. (Interpretation: "next 10-K"
  = first 10-K whose fiscal year ends after the event, since a 10-K filed shortly after the
  event still covers the pre-event year.)
- When a 10-Q restates the prior-year YTD (e.g. discontinued operations), TTM is rebuilt as
  current YTD + (FY - original prior YTD) x restatement ratio.
- Tiers: flag = normal confidence and 30% <= MoS <= 60%; watch = low confidence or MoS > 60%.
- Materiality: the TTM-only regime applies only if TTM acquisitions >= 10% of pre-event total
  assets, or the disposed business >= 10% of revenue (from the revenue restatement in 10-Qs, or
  for manual spin-offs the spun-off company's own revenue). CMCSA/Versant = 5%: normal treatment.
- A 10-Q/10-K clears a pending corporate action only if its period ends >= 30 days after the
  event (HON's Q2 10-Q, period ending one day after its spin 8-K, still consolidated aerospace).
- Standard fair EV/EBIT = shrinkage blend w x sub-industry median + (1 - w) x anchor median,
  w = n / (n + 8) (0 if n < 2), anchor = industry group (>= 8) -> sector (>= 8) -> all, capped
  at 25x. The cap binds for 82 of 431 standard companies.
- FSLR: primary EBIT excludes Section 45X credits (`screener/sensitivities.py`, manual from
  filing text; ~$1.79B of $1.81B TTM EBIT); as-reported is the sensitivity. Note
  `government_credit_dependent`. Refresh with each FSLR filing.
- Manual corporate actions: CMCSA (Versant), BDX (Biosciences & Diagnostics/Waters), CTVA (Vylor).
- FDX: the FedEx Freight spin 8-K (2026-06-01) is 123 days old, just past the 120-day pending
  window, and no post-event 10-Q exists yet, so it gets normal treatment.

## Milestone 8 (LLM): status
- Model: gemini-3.5-flash-lite (gemini-3.8-flash free tier = 20 requests/day and repeatedly
  503-overloaded). Exact Flash-Lite daily limit not exposed by the API; check AI Studio.
- Rubric v2.1 tested on 20 companies (data/llm_*_test_v2.1.*). Risk-change scores 1-9, median
  5.5, 1 of 20 at the floor. Gate fires for 8 of 20 (40%): the model rates most serious risks
  ">25% of operating income" (e.g. MSFT AI capex, ZTS convertible notes). Pending: gate fix and
  rubric approval, then the LLM GitHub Actions workflow with a priority queue.
- Backfill complete 2026-10-07: 499 of 500 (HONA has no 10-K yet). Re-scoring 10 random
  non-flag/watch companies: llm_score mean absolute difference 2.6 (max 10.5); risk change is the
  noisiest component (0.8), CAM the most stable (0.1).
- 2026-10-08: LLM component live in the composite at `scoring.LLM_WEIGHT` = 10 (others scaled
  to 90 in their report proportions). Flag/watch scores are the mean of two runs (run 2 at
  temperature 0.7; `llm_scores.runs`, `run_scores`); the gate fires if either run fires. A
  company that becomes flag/watch with a single-run score gets run 2 in the next queue run,
  reusing the stored run 1 (one request). Gate findings from an earlier partial second run on
  the same 10-K are carried over as "[earlier run]" (IT's was restored by hand after the first
  upgrade pass dropped it). Effect on 2026-10-07 flags: no status change, no flag moved more
  than 3 places among flags (mean 0.8), composite change mean +0.3, max 3.5 (APO).
- Gate (approved): LLM part = new serious/existential company-specific risk of 8 event types,
  materiality >= 10% or not estimable, verified quote with matching event language, two runs for
  flag/watch (fires if either fires); new unusual CAMs >= 10%. Rule-based: going concern and
  material weakness (10-K text, formal auditor/9A conclusions only), restatement (8-K Item 4.02 or
  10-K/A with restated XBRL financial data; 10-K/A text matching fired on clawback clauses), and
  goodwill/intangible impairment >= 5% of assets. First full run: gate fired for 19 companies;
  flags moved to watch: GEN, EIX, CDW, UHS (all LLM).

- Gemini requests have a wall-clock limit (`llm.REQUEST_TIMEOUT`, 300 s; requests' own timeout
  is per socket read, and one trickling response stalled a run ~50 min). A timeout counts like a
  5xx: retried MAX_5XX_RETRIES times, then ServiceUnavailable stops the run.

## Milestone 10: performance tracking (2026-10-08)
- SPY close captured in the daily bulk IEX request (`update_prices.BENCHMARKS`).
- `flag_episodes` rebuilt from screen_history each day (`scripts/update_performance.py`), so the
  backfill is the same code; entry/exit at the screen close on the status-change date; tier =
  top/bottom half of that day's flags by composite. Backfill: 35 episodes (24 open, 11 closed)
  over 2026-10-02 .. 10-07.
- Total returns from Tiingo EOD adjClose (dividends) for SPY + episode tickers, re-fetched over the
  full range daily (~1 request per open episode, capped at 40/run; 500 unique symbols/month);
  `flag_performance.return_basis` falls back to price_only when adjusted data is missing.
- History starts 2026-10-02 and has gaps on days the daily job didn't run (2026-10-05), so an
  entry/exit can land one session late; returns are paper (no costs, close-to-close).

## Milestone 9: Supabase notes
- Database ~195 MB of the 500 MB free tier after the initial load (facts ~970k rows dominate).
  Daily history is kept slim (screen_history); full detail only in *_latest tables.
- Price history starts 2026-10-02; sessions missed before the daily job starts are not backfilled.

## Composite score (Milestone 7): decisions applied
- Ranking groups: GICS industry group -> sector -> all standard (min 8), matching valuation peers.
- Missing components imputed at 50; `components_available` (of 7); < 5 -> low confidence.
- Productivity = operating profitability (EBIT + R&D) / assets for all standard companies
  (430 of 431 covered; FSLR's numerator excludes 45X credits).
- Composite null for insufficient_data rows (component scores kept).
- LLM component at LLM_WEIGHT (10%) since 2026-10-08; a missing LLM score is imputed at 50.

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

