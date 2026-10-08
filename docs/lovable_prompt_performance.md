# Lovable prompt: Performance page (add to the existing dashboard)

Paste everything between the lines into Lovable. It extends the dashboard built from `docs/lovable_prompt.md` and `docs/lovable_prompt_holdings.md`.

---

Add a read-only **Performance** page to my existing dashboard. Keep everything already built.

## Rules (same as before)
- **Do not create, alter, drop, or migrate any tables, views, functions, triggers, storage buckets, or RLS policies, and do not run any SQL.** The data already exists. If something seems to require a schema change, stop and tell me instead.
- Use the Supabase JS client with the publishable (anon) key and the signed-in user's session only. Never use a service role key. This page only reads data: no inserts, updates, or deletes.
- This is paper tracking of the screener's flags against SPY for research. No trading features, brokerage links, or notifications.
- Use exact names. Show "—" for null values. Don't invent or recompute returns; every number comes from the views below. Put each section in its own error boundary, and pass any list-like value through the existing `toList` helper.

## Data (read-only; exact names and Postgres types)
PostgREST returns float8, numeric, int4 and int8 as JSON numbers; wrap them in `Number()`. Returns and rates are fractions (0.034 = 3.4%).

**`flag_performance_summary`** (view, up to 12 rows): group_state text (all / open / closed), group_tier text (all / top_half / bottom_half / unranked), episodes int8, avg_excess_return float8, median_excess_return float8, hit_rate numeric, avg_return float8, avg_spy_return float8, avg_days_held numeric, total_return_episodes int8, price_only_episodes int8.

**`flag_performance`** (view, one row per flag episode): cik int4, ticker text, entry_date date, entry_price float8, entry_fair_value float8, entry_mos float8, entry_composite float8, entry_composite_tier text, entry_flag_count int4, exit_date date, exit_price float8, exit_status text, exit_reason text, is_open boolean, end_date date, end_price float8, days_held int4, price_return float8, spy_price_return float8, total_return float8, spy_total_return float8, return_basis text (`total_return` or `price_only`), stock_return float8, spy_return float8, excess_return float8, beat_spy boolean.

## Performance page (route /performance; add it to the navigation)
1. **Headline cards** from the `flag_performance_summary` row with group_state = 'all' and group_tier = 'all': episodes, average excess return vs SPY, median excess return, hit rate (% of episodes beating SPY), average return vs average SPY return, and average days held.
2. **Breakdown table** from `flag_performance_summary`. Rows: all, open, closed. Column groups: all / top half / bottom half of flags by composite at entry (use group_tier). Each cell shows episodes, average excess, median excess and hit rate. Color excess returns green when positive and red when negative.
3. **Episodes table** from `flag_performance`, newest entry_date first. Columns: ticker (link to the company page), entry_date, entry_price, entry_mos, entry_composite plus a tier badge (top half / bottom half), status (an "Open" badge, or "Closed" with exit_date and exit_status), days_held, stock_return, spy_return, excess_return (green/red), and a small "price-only" tag when return_basis = 'price_only'. Show exit_reason in a tooltip. Filters: open/closed/all and tier. Sortable columns.
4. **Basis note** under the headline: "Returns include dividends where adjusted prices are available ({total_return_episodes} episodes); the rest are price-only ({price_only_episodes})." Use the all/all summary row.
5. **Caveats** in small gray text at the bottom: "Paper tracking: entry and exit at the screen's closing price on the day the status changed; no costs or taxes. Episodes held less than one day are excluded from the summary. Not investment advice."

Mobile: cards stack, and the episodes table collapses to cards (ticker, open/closed, days held, excess return).

## Also
- On the company page, add a small "Flag history" list from `flag_performance` filtered by ticker: entry_date → exit_date (or "open"), stock_return, excess_return.
- Empty states: when there are no episodes, show "No flag episodes yet. Tracking starts when a company is flagged." Handle missing rows as "no data", not as an error.

---
