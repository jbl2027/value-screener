# Lovable prompt: read-only screener dashboard

Paste everything between the lines into Lovable after connecting the existing Supabase project.

---

Build a read-only dashboard for my existing Supabase project. The database already exists and is maintained by separate jobs. **Do not create, alter, drop, or migrate any tables, views, functions, triggers, storage buckets, or RLS policies, and do not run any SQL.** Do not create a `profiles` table or any other table. Only read data with the Supabase JS client using the anon (publishable) key; never use a service role key. If something seems to require a schema change, stop and tell me instead.

## Authentication
- Email + password login with Supabase Auth. Every page except /login requires a signed-in user; redirect to /login otherwise. RLS only allows the `authenticated` role to read, so nothing loads when signed out.
- No sign-up page or sign-up link (accounts are created by me). Include sign out.

## Data sources (exact names; read-only)
Use only these relations. Join on `ticker` (present in all of them).

**`latest_screen`** (view, one row per company): ticker, name, sector_model, gics_sector, gics_industry_group, status, status_reason, confidence, confidence_reasons, warnings, price, fair_value, mos, company_multiple, fair_multiple, peer_group, qualitative_gate, qualitative_detectors, qualitative_reasons, composite, composite_confidence, composite_rank, components_available, as_of.

**`valuations_latest`** (table, one row per company): cik, ticker, as_of, name, sector_model, gics_sector, gics_industry_group, status, status_reason, confidence, confidence_reasons, warnings, disqualify_reasons, missing_inputs, price, fair_value, mos, method, company_multiple, peer_multiple, fair_multiple, peer_group, peer_count, base_value, base_basis, earnings_yield, treasury_yield, qualitative_gate, qualitative_detectors, qualitative_reasons, detail (jsonb).
Keys inside `detail` to use: f_points, f_tests, f_score_scaled, m_score, m_score_imputed, beneish_top_index, status_without_dq, peer_n_sub, peer_w, peer_median_sub, peer_median_anchor, peer_anchor_level, peer_anchor_group, peer_anchor_n, peer_blended, peer_cap_applied, ebit_ttm, ebit_3y_avg, ebit_fy, ebit_fy_prior, ebit_fy_change, ebit_ttm_reported, ebit_3y_avg_reported, ebit_fy_reported, ebit_fy_prior_reported, ebit_adjustment, impairment_addback_ttm, impairment_addback_fy, impairment_addback_fy_prior, impairment_addback_3y_avg, treasury_guard_pass, event_filed, event_source, corporate_action_materiality, captive_finance, shares.
Select JSON keys with PostgREST paths, e.g. `.from('valuations_latest').select('ticker, f_score_scaled:detail->f_score_scaled, f_points:detail->f_points, f_tests:detail->f_tests, m_score:detail->m_score, m_score_imputed:detail->m_score_imputed')`.

**List-like text fields (exact types, verified against the live schema).** None of these is an array or JSON: each is a nullable plain `text` column holding a delimited list. It is `null` when there are no items, never an empty string. Split it into items for display, trim each one, and treat `null` as an empty list. Never call array methods on the raw value.

| Field | Relations | Postgres type | Separator | Example |
|---|---|---|---|---|
| qualitative_detectors | latest_screen, valuations_latest | text (nullable) | `", "` | `llm, material_weakness` |
| qualitative_reasons | latest_screen, valuations_latest | text (nullable) | `" \| "` (each detector's first item is prefixed `detector: `) | `llm: new serious company-specific risk [...] \| material_weakness: ...` |
| confidence_reasons | latest_screen, valuations_latest | text (nullable) | `"; "` | `post-corporate-action (...): TTM only until next 10-K` |
| warnings | latest_screen, valuations_latest | text (nullable) | `"; "` | `government_credit_dependent: EBIT ex Section 45X ...` |
| disqualify_reasons | valuations_latest | text (nullable) | `"; "` | `beneish M=-0.33 > -1.78; piotroski 3/9 <= 1/3` |
| missing_inputs | valuations_latest | text (nullable) | `"; "` | `ffo (net income, D&A, or gains on property sales)` |
| detail->>ebit_adjustment | valuations_latest | JSON string or null | `" \| "` | `impairment_addback: goodwill/intangible impairment charges added back to EBIT (TTM +$962M, ...)` |

Other types: `mos`, `earnings_yield`, `treasury_yield` are float8 fractions (0.51 = 51%); `price`, `fair_value`, `composite` are float8; `qualitative_gate` is boolean; `as_of` is a date; `status` and `confidence` are text.

**`scores_latest`** (table): ticker, composite, composite_confidence, composite_rank, components_available, components_missing, composite_note, valuation_score, productivity_score, earnings_quality_score, momentum_score, llm_score, growth_score, shareholder_yield_score, detail (jsonb; keys: components_active, components_pending, valuation_raw, productivity_raw, sloan_accruals, f_used, growth_metric, shareholder_yield, valuation_group, productivity_group, earnings_quality_group, momentum_group, growth_group, shareholder_yield_group).

**`llm_latest`** (view, latest Gemini 10-K review per company; may be missing for some companies): ticker, accn, filed, period, model, prompt_version, moat, moat_lost_revenue_share, management_candor, risk_change_score, cam_score, new_risks, new_risks_serious_company, cam_count, cam_new, cam_unusual, cam_topics, qualitative_gate, gate_reasons, gate_review, risk_added_share, evidence_verified, evidence_total, llm_score, scored_at, runs, run_scores (jsonb), result (jsonb).
Types: moat, management_candor, risk_change_score, cam_score and llm_score are float8. When `runs` = 2 (flag and watch companies), they are the mean of two Gemini runs and can be fractional (show 1 decimal, e.g. "7.5"). `runs` is smallint (1 or 2). `run_scores` is null or an array with one object per run: {moat, candor, risk_change, cam, llm_score, cam_count, cam_new, cam_unusual, cam_prior_available}. When runs = 2, show "average of 2 runs" with each run's llm_score in small text.
`result` structure: `moat` {score, rationale, evidence[], revenue_share_affected_by_lost_advantage}; `management_candor` {score, rationale, evidence[]}; `risk_factor_change` {assessable, rationale, new_risks[]: {risk, severity, scope, event_type, materiality, materiality_basis, materiality_rationale, evidence}}; `critical_audit_matters` {prior_year_available, evidence[], cams[]: {topic, category, new_vs_prior_year, unusual_for_industry, materiality, rationale}}; `gate` (string[]); `gate_review` (string[]); `data_quality` {sections_adequate, notes}; optional `self_consistency_run`, which is the second run with the same structure (older rows contain only {risk_factor_change, critical_audit_matters}). Rationales and evidence shown on the page come from the first run.

**`screen_history`** (table, one row per company per trading day): as_of, ticker, status, confidence, price, fair_value, mos, composite.

**`filing_flags`** (table, rule-based 10-K detectors): ticker, accn, filed, going_concern, material_weakness, evidence, checked_at.

**`runs`** (table, job log): id, job, as_of, started_at, finished_at, status, summary (jsonb).

**`pipeline_status`** (view, exactly one row, computed when read): checked_at, last_daily_run_at, last_daily_as_of, daily_age_hours, daily_weekday_age_hours, daily_stale (boolean), last_llm_run_at, last_llm_stop_reason. `daily_stale` is true on a weekday when the latest successful results load is more than 30 weekday hours old (weekend hours not counted).

## Formatting
- mos and earnings_yield/treasury_yield: percent, 0 decimals (mos can be very negative; show it, color negative red). price, fair_value: $ with 2 decimals. composite and component scores: 0–100, 0 decimals. Multiples: "12.3x". Large dollar values (EBIT): $1.2B / $340M.
- Status badges: flag (green), watch (amber), no_flag (gray), disqualified (red), insufficient_data / not_applicable / corporate_action_pending (light gray, show the label in words).
- F-Score: show `f_score_scaled` rounded (0–9) with "f_points/f_tests" in small text; blank for banks and insurers.
- M-Score: show `m_score`; if null and `m_score_imputed` exists, show it with an "imputed" tag; blank for banks and insurers. Values above −1.78 in red.
- Null values: show "—". Never invent or compute values that aren't in the data.

## Page 1: Screener (route /)
- Summary bar at the top (see below).
- Table of all rows from `latest_screen`, merged by ticker with F/M scores from `valuations_latest.detail`.
- Columns: ticker, name, sector (gics_sector), status, price, fair value, margin of safety (mos), composite, F-Score, M-Score, confidence, reason. Reason = `status_reason` when status is watch (including "qualitative_risk: …" from the gate), plus `qualitative_detectors` as small chips when qualitative_gate is true. Truncate long text with a tooltip.
- Default view: status in (flag, watch), sorted by composite descending (nulls last).
- Controls: status multi-select (all statuses available), sector (gics_sector) and sector_model filters, confidence filter, ticker/name search, margin-of-safety minimum. All columns sortable. Show "N of M companies".
- Row click opens the company page. Mobile: collapse to cards showing ticker, name, status, mos, composite.

## Page 2: Company (route /company/:ticker)
Header: ticker, name, gics_sector / gics_industry_group, status badge, price, fair value, margin of safety, composite, confidence (+ confidence_reasons), status_reason, warnings, as_of.

Sections (cards; collapse on mobile):
1. **Valuation inputs** (`valuations_latest`): method, sector_model; company_multiple vs fair_multiple; peer_group, peer_count, and from `detail`: peer_n_sub, peer_w, peer_median_sub, peer_median_anchor, peer_anchor_level, peer_anchor_group, peer_anchor_n, peer_blended, peer_cap_applied ("capped at 25x" when true); base_value and base_basis; EBIT comparison table: ebit_ttm vs ebit_3y_avg, plus ebit_fy, ebit_fy_prior, ebit_fy_change; if ebit_adjustment is present, show each item (split on `" | "`) and add an "as reported" column (ebit_ttm_reported, ebit_3y_avg_reported, ebit_fy_reported, ebit_fy_prior_reported) and an "impairment add-back" column (impairment_addback_ttm, impairment_addback_3y_avg, impairment_addback_fy, impairment_addback_fy_prior; show "—" when 0 or null) next to the adjusted values; earnings_yield vs treasury_yield and treasury_guard_pass; disqualify_reasons, missing_inputs, status_without_dq, beneish_top_index; corporate action fields (event_filed, event_source, corporate_action_materiality) when present.
2. **Component scores** (`scores_latest`): horizontal bars for valuation_score, productivity_score, earnings_quality_score, momentum_score, growth_score, shareholder_yield_score, llm_score, each with its raw value and ranking group from `detail`; composite, composite_rank, composite_confidence, components_available of detail.components_active, components_missing, composite_note. If detail.components_pending contains "llm", note "LLM score not yet included in the composite".
3. **Gemini 10-K review** (`llm_latest`; show "Not yet reviewed" if no row): filed, model, prompt_version, scored_at; scores moat, management_candor, risk_change_score, cam_score, llm_score; "evidence_verified of evidence_total quotes verified verbatim against the filing". Then result.moat and result.management_candor rationale with their evidence quotes (styled as block quotes); result.risk_factor_change rationale and a table of new_risks (risk, severity, scope, event_type, materiality, evidence); critical_audit_matters.cams table (topic, category, new_vs_prior_year, unusual_for_industry, materiality, rationale). Show result.gate items as "fires" and result.gate_review items as "reviewed, did not fire".
4. **Gate events**: qualitative_gate, qualitative_detectors, qualitative_reasons from `valuations_latest`; rows from `filing_flags` for this ticker (filed, going_concern, material_weakness, evidence).
5. **Status history** (`screen_history` for this ticker, ordered by as_of): line chart of price vs fair_value and a second small chart of mos; below it a compact table listing only the days where status changed.

## Summary bar (top of every page)
- Counts by status from `latest_screen` (flag, watch, no_flag, disqualified, insufficient_data, not_applicable, corporate_action_pending); clicking a count filters the screener.
- Last daily run: latest `runs` row with status = 'ok' and job in ('load:prices+results', 'load:results'), show finished_at in my local time, plus `as_of` from latest_screen.
- Last fundamentals update: latest `runs` row with status = 'ok' and job starting with 'load:universe', finished_at.
- Last Gemini run: latest `runs` row with job = 'llm' (finished_at and summary->>stop_reason).

## Design
Clean and minimal: white background, one accent color, system font, generous spacing, no decorative imagery. Fully responsive (usable on a phone). Fast: load the screener with at most 3 queries; fetch company details only on the company page. Loading skeletons and clear empty/error states (e.g. "Session expired, please sign in again").

---
