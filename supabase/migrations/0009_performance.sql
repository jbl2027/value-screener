-- Milestone 10: paper tracking of flags vs SPY (records only; no orders).
--   flag_episodes    one row per stretch of consecutive flag status (rebuilt from screen_history)
--   eod_adjusted     Tiingo EOD closes with dividend/split-adjusted closes for SPY and episode tickers
--   flag_performance           per episode: return since entry (to exit if closed) vs SPY
--   flag_performance_summary   average / median excess return and hit rate by open/closed and tier
-- Writers are the pipeline (postgres role); authenticated users read only.

create table if not exists flag_episodes (
    cik                   integer not null,
    ticker                text not null,
    entry_date            date not null,     -- first screen date with status flag (close of that day)
    entry_price           double precision,
    entry_fair_value      double precision,
    entry_mos             double precision,
    entry_composite       double precision,
    entry_composite_tier  text,              -- top_half / bottom_half of that day's flags by composite
    entry_flag_count      integer,           -- number of flags on the entry date
    exit_date             date,              -- first screen date with another status (null = open)
    exit_price            double precision,
    exit_status           text,              -- the new status (or not_in_screen)
    exit_reason           text,              -- status_reason of the new status, when known
    updated_at            timestamptz not null default now(),
    primary key (cik, entry_date)
);
create index if not exists flag_episodes_ticker_idx on flag_episodes (ticker);

create table if not exists eod_adjusted (
    ticker        text not null,
    date          date not null,
    close         double precision,
    adj_close     double precision,          -- dividend- and split-adjusted (Tiingo adjClose)
    div_cash      double precision,
    split_factor  double precision,
    fetched_at    timestamptz not null default now(),
    primary key (ticker, date)
);

alter table flag_episodes enable row level security;
alter table eod_adjusted enable row level security;
drop policy if exists authenticated_read on flag_episodes;
drop policy if exists authenticated_read on eod_adjusted;
create policy authenticated_read on flag_episodes for select to authenticated using (true);
create policy authenticated_read on eod_adjusted for select to authenticated using (true);
revoke all on flag_episodes, eod_adjusted from anon;
revoke insert, update, delete, truncate, references, trigger on flag_episodes, eod_adjusted from authenticated;
grant select on flag_episodes, eod_adjusted to authenticated;

-- Per episode. End = exit date for closed episodes, else the ticker's latest stored close.
-- Total return uses adjusted closes (dividends reinvested) when both the stock and SPY have them
-- on the entry and end dates; otherwise both sides are price-only (return_basis says which).
-- SPY prices are the latest close on or before each date.
create or replace view flag_performance with (security_invoker = true) as
with ep as (
    select e.*,
           e.exit_date is null as is_open,
           coalesce(e.exit_date, (select max(p.date) from prices p where p.ticker = e.ticker)) as end_date
    from flag_episodes e
),
px as (
    select ep.*,
           coalesce(ep.exit_price, (select p.close from prices p where p.ticker = ep.ticker
                                    and p.date <= ep.end_date order by p.date desc limit 1)) as end_price,
           (select p.close from prices p where p.ticker = 'SPY' and p.date <= ep.entry_date
            order by p.date desc limit 1) as spy_entry_price,
           (select p.close from prices p where p.ticker = 'SPY' and p.date <= ep.end_date
            order by p.date desc limit 1) as spy_end_price,
           (select a.adj_close from eod_adjusted a where a.ticker = ep.ticker and a.date = ep.entry_date) as adj_entry,
           (select a.adj_close from eod_adjusted a where a.ticker = ep.ticker and a.date = ep.end_date) as adj_end,
           (select a.adj_close from eod_adjusted a where a.ticker = 'SPY' and a.date <= ep.entry_date
            order by a.date desc limit 1) as spy_adj_entry,
           (select a.adj_close from eod_adjusted a where a.ticker = 'SPY' and a.date <= ep.end_date
            order by a.date desc limit 1) as spy_adj_end
    from ep
),
r as (
    select px.*,
           end_price / nullif(entry_price, 0) - 1                   as price_return,
           spy_end_price / nullif(spy_entry_price, 0) - 1           as spy_price_return,
           adj_end / nullif(adj_entry, 0) - 1                       as total_return,
           spy_adj_end / nullif(spy_adj_entry, 0) - 1               as spy_total_return
    from px
)
select
    cik, ticker, entry_date, entry_price, entry_fair_value, entry_mos, entry_composite, entry_composite_tier,
    entry_flag_count, exit_date, exit_price, exit_status, exit_reason, is_open, end_date, end_price,
    end_date - entry_date                                            as days_held,
    price_return, spy_price_return, total_return, spy_total_return,
    case when total_return is not null and spy_total_return is not null
         then 'total_return' else 'price_only' end                  as return_basis,
    case when total_return is not null and spy_total_return is not null
         then total_return else price_return end                     as stock_return,
    case when total_return is not null and spy_total_return is not null
         then spy_total_return else spy_price_return end             as spy_return,
    case when total_return is not null and spy_total_return is not null
         then total_return - spy_total_return else price_return - spy_price_return end as excess_return,
    case when total_return is not null and spy_total_return is not null
         then total_return > spy_total_return else price_return > spy_price_return end as beat_spy
from r;

-- Summary over episodes held at least one day with a return. group_state: all / open / closed;
-- group_tier: all / top_half / bottom_half / unranked.
create or replace view flag_performance_summary with (security_invoker = true) as
select
    case when grouping(state) = 1 then 'all' else state end           as group_state,
    case when grouping(tier) = 1 then 'all' else tier end             as group_tier,
    count(*)                                                          as episodes,
    avg(excess_return)                                                as avg_excess_return,
    percentile_cont(0.5) within group (order by excess_return)        as median_excess_return,
    avg(case when beat_spy then 1.0 else 0.0 end)                     as hit_rate,
    avg(stock_return)                                                 as avg_return,
    avg(spy_return)                                                   as avg_spy_return,
    avg(days_held)                                                    as avg_days_held,
    count(*) filter (where return_basis = 'total_return')             as total_return_episodes,
    count(*) filter (where return_basis = 'price_only')               as price_only_episodes
from (
    select case when is_open then 'open' else 'closed' end as state,
           coalesce(entry_composite_tier, 'unranked') as tier, *
    from flag_performance
    where days_held > 0 and excess_return is not null
) x
group by grouping sets ((), (state), (tier), (state, tier));

revoke all on flag_performance, flag_performance_summary from anon;
grant select on flag_performance, flag_performance_summary to authenticated;
