# Lovable prompt: holdings tracker and alerts (add to the existing dashboard)

Paste everything between the lines into Lovable. It extends the dashboard built from `docs/lovable_prompt.md`.

---

Add a holdings tracker to my existing dashboard. Keep everything already built.

## Rules (same as before)
- **Do not create, alter, drop, or migrate any tables, views, functions, triggers, storage buckets, or RLS policies, and do not run any SQL.** Everything below already exists in the database. If something seems to require a schema change, stop and tell me instead.
- Use the Supabase JS client with the publishable (anon) key and the signed-in user's session only. Never use a service role key.
- This is a tracking tool only: no brokerage connections, no order placement, no trade buttons that talk to any external service, no emails or push notifications. "Mark as Bought" and "Record Sale" only save records of trades I made elsewhere.
- Use the exact relation and column names below. Never send `id`, `user_id`, or `created_at` on insert: the database fills them in (`user_id` defaults to the signed-in user, and RLS rejects any other value).
- Show "—" for null values. Don't invent values. Sums for display totals come from `portfolio_summary`; don't compute them yourself.

## Database objects (exact names and Postgres types)
Numbers: PostgREST returns `numeric`, `float8`, `int4` and `int8` as JSON numbers. Wrap them in `Number()` before doing arithmetic or formatting. `date` values are `YYYY-MM-DD` strings; `timestamptz` values are ISO strings (show them in my local time). Fields named `*_pct`, `mos*` and `progress_to_fair_value` are fractions (0.25 = 25%).

**`holdings`** (table; one row per buy lot; read/insert/update/delete own rows):
id uuid (auto), user_id uuid (auto = signed-in user), ticker text NOT NULL, buy_date date NOT NULL (default today), shares numeric NOT NULL (> 0), buy_price numeric NOT NULL (> 0), fair_value_at_buy numeric, mos_at_buy float8, composite_at_buy float8, status_at_buy text, target_price numeric (nullable, > 0), notes text, created_at timestamptz (auto).
The database rejects tickers not in the screener universe, and lowering `shares` below what has already been sold.

**`sales`** (table; read/insert/update/delete own rows):
id uuid (auto), user_id uuid (auto), holding_id uuid NOT NULL (→ holdings.id), sell_date date NOT NULL (default today), shares_sold numeric NOT NULL (> 0), sell_price numeric NOT NULL (> 0), notes text, created_at timestamptz (auto).
The database rejects selling more than the lot's remaining shares, a sell_date before the buy_date, and sales on another user's lot. Show the error message it returns. A lot is closed when its total shares sold equals its shares.

**`alerts`** (table; written by the daily pipeline; you may SELECT and UPDATE only the `acknowledged` column):
id uuid, user_id uuid, holding_id uuid, ticker text, alert_type text (one of TARGET_HIT, APPROACHING, MANUAL_TARGET, THESIS_BREAK, QUALITATIVE, DATA_ISSUE), triggered_at timestamptz, price_at_alert numeric, fair_value_at_alert numeric, message text NOT NULL, acknowledged boolean NOT NULL, cleared_at timestamptz (null while the condition still holds; set when it clears).
To acknowledge, run `update({ acknowledged: true }).eq('id', alertId)` and send no other columns.

**`holdings_live`** (view; open lots only, i.e. shares_open > 0):
id uuid, user_id uuid, ticker text, name text, buy_date date, shares numeric, shares_sold numeric, shares_open numeric, buy_price numeric, target_price numeric, notes text, fair_value_at_buy numeric, mos_at_buy float8, composite_at_buy float8, status_at_buy text, as_of date, price float8, fair_value float8, mos float8, status text, status_reason text, composite float8, qualitative_gate boolean, cost_basis numeric, market_value float8, unrealized_gain float8, unrealized_pct float8, progress_to_fair_value float8, days_held int4, created_at timestamptz.
`progress_to_fair_value` is 0 at the buy price and 1 at the current fair value. It can be below 0 or above 1, and it is null when fair value is missing or at or below the buy price.

**`sales_realized`** (view; one row per sale):
id uuid, user_id uuid, holding_id uuid, ticker text, name text, buy_date date, buy_price numeric, sell_date date, shares_sold numeric, sell_price numeric, notes text, cost_basis numeric, proceeds numeric, realized_gain numeric, realized_pct numeric, days_held int4, lot_closed boolean.

**`portfolio_summary`** (view; exactly one row for the signed-in user):
user_id uuid, open_lots int8, cost_basis numeric, market_value float8, unrealized_gain float8, unrealized_pct float8, realized_gain numeric, unacknowledged_alerts int8. cost_basis, market_value and unrealized values are null when there are no open lots.

**`pipeline_status`** (view; exactly one row):
checked_at timestamptz, last_daily_run_at timestamptz, last_daily_as_of date, daily_age_hours numeric, daily_weekday_age_hours numeric, daily_stale boolean, last_llm_run_at timestamptz, last_llm_stop_reason text.

**`latest_screen`** (existing view, used for "Mark as Bought"): ticker text, price float8, fair_value float8, mos float8, composite float8, status text, as_of date.

## 1. "Mark as Bought" (company page)
- Add a "Mark as Bought" button to the company page header. It opens a form with:
  - shares: number > 0, required; fractional shares allowed.
  - buy price: number > 0, required; defaults to `latest_screen.price` for this ticker.
  - buy date: defaults to today in my local time (`YYYY-MM-DD`, not UTC); no future dates.
  - target price: optional, > 0.
  - notes: optional.
- On save, insert into `holdings`: ticker, shares, buy_price, buy_date, target_price, notes, plus a snapshot of this ticker's `latest_screen` row at that moment: fair_value_at_buy = fair_value, mos_at_buy = mos, composite_at_buy = composite, status_at_buy = status. Snapshot values may be null; send null in that case.
- Show a short confirmation with a link to the Portfolio page. If the insert fails, show the database's error message.
- If I already hold this ticker, show "You hold N shares (M lots)" with a link to the Portfolio page. Get this from `holdings_live`.

## 2. Portfolio page (route /portfolio; add it to the navigation)
Top to bottom:
1. **Alerts**: unacknowledged rows from `alerts` (`acknowledged = false`), newest `triggered_at` first. Each shows the alert_type badge, ticker (link to the company page), message, triggered_at, price_at_alert, fair_value_at_alert, and "condition still active" when cleared_at is null or "cleared" otherwise, plus an Acknowledge button. Badge colors: TARGET_HIT green; MANUAL_TARGET green; APPROACHING amber; THESIS_BREAK red; QUALITATIVE red; DATA_ISSUE gray. Add a "Show acknowledged" toggle that lists older alerts. Hide the section when there is nothing to show.
2. **Totals** from `portfolio_summary`: open_lots, cost_basis, market_value, unrealized_gain ($ and unrealized_pct), realized_gain.
3. **Open lots** from `holdings_live`, sorted by ticker. Columns: ticker (+ name), shares_open (show "of shares" if shares_sold > 0), buy_price, price, fair_value, unrealized_gain, unrealized_pct, progress toward fair value, status badge (same colors as the screener), days_held. Also show target_price if set, and the as_of date.
   - Progress bar: fill = progress_to_fair_value clamped to 0–100%, with the actual value as a label (it can read "−12%" or "135%"). Show "—" with no bar when it is null.
   - Gains: green when positive, red when negative.
   - Row actions: "Record Sale", "Edit" (target price and notes), and "Delete lot" (for entry mistakes). Delete needs an inline confirmation step in the page, because browser dialogs don't work in this app. It removes the lot and its sales and alerts.
   - Expanding a row shows the at-buy snapshot: buy_date, fair_value_at_buy, mos_at_buy, composite_at_buy, status_at_buy, and notes.
4. **Closed positions** from `sales_realized`, newest sell_date first. Columns: ticker, buy_date, sell_date, shares_sold, buy_price, sell_price, proceeds, realized_gain, realized_pct, days_held, and a "lot closed" or "partial" tag from lot_closed. Show realized_gain from `portfolio_summary` as the section total.

Mobile: lots and sales collapse to cards (ticker, shares, unrealized or realized $ and %, progress bar, status).

## 3. Record Sale
- A form from an open lot: shares to sell (> 0, at most shares_open, prefilled with shares_open), sell price (required, > 0, prefilled with the current price), sell date (defaults to today local, not before buy_date), and notes.
- Insert into `sales`: holding_id, shares_sold, sell_price, sell_date, notes. Partial sales are allowed. After saving, refetch `holdings_live`, `sales_realized` and `portfolio_summary`. A fully sold lot drops out of `holdings_live` and appears under Closed positions.

## 4. Edit an open lot
Edit target_price (clearing it sets null) and notes, then save with `update({ target_price, notes }).eq('id', lotId)`. Don't offer editing of anything else.

## 5. Summary bar additions (every page)
- An alert badge showing `portfolio_summary.unacknowledged_alerts` when it is above 0, linking to /portfolio.
- A **stale data banner** across the top of every page when `pipeline_status.daily_stale` is true: "Screener data may be stale: the last daily update finished {daily_age_hours} hours ago ({last_daily_run_at} local time, data as of {last_daily_as_of})." Use amber, and don't let me dismiss it permanently (it reappears on reload).

## 6. Defensive handling (apply everywhere, including existing pages)
- Write one helper, `toList(value, separator)`, and use it for every list-like field before rendering. It returns `value` unchanged if it is already an array; `[]` for null, undefined or an empty string; and for a string, `value.split(separator).map(s => s.trim()).filter(Boolean)`. Anything else becomes `[String(value)]`. Separators: qualitative_detectors `", "`; qualitative_reasons `" | "`; confidence_reasons, warnings, disqualify_reasons, missing_inputs `"; "`. These are all plain text columns, not arrays. JSON arrays inside `detail` or `result` (e.g. `result.gate`, `result.moat.evidence`) also go through `toList`.
- Never call `.map`, `.join` or `.length` on a raw database value without normalizing it first.
- Wrap each page section (each card, table, form, the alerts list, the banner) in its own React error boundary. A failed section shows "This section couldn't load" with a Retry button, and the rest of the page keeps working.
- Handle a missing row (e.g. `portfolio_summary` or `pipeline_status` returning nothing) as "no data", not an error. Treat an expired session as "Session expired, please sign in again".

---
