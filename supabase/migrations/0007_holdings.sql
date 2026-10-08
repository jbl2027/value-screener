-- Holdings tracker and sell alerts (tracking only: no brokerage link, no orders, no notifications).
--   holdings  one row per buy lot, entered from the dashboard
--   sales     partial or full sells of a lot; a lot is closed when shares sold = shares bought
--   alerts    written by the daily pipeline (scripts/check_alerts.py, postgres role) only
-- RLS: each signed-in user reads and writes only their own holdings and sales; on alerts they can read
-- their own rows and change only `acknowledged`. All other tables stay read-only (0005).

create table holdings (
    id                uuid primary key default gen_random_uuid(),
    user_id           uuid not null default auth.uid(),
    ticker            text not null,           -- checked against universe on insert (no FK: the weekly
                                               -- universe load deletes tickers that leave the index)
    buy_date          date not null default current_date,
    shares            numeric not null check (shares > 0),
    buy_price         numeric not null check (buy_price > 0),
    fair_value_at_buy numeric,
    mos_at_buy        double precision,
    composite_at_buy  double precision,
    status_at_buy     text,
    target_price      numeric check (target_price is null or target_price > 0),
    notes             text,
    created_at        timestamptz not null default now()
);
create index holdings_user_idx on holdings (user_id);

create or replace function check_holding_ticker() returns trigger language plpgsql set search_path = public as $$
begin
    new.ticker := upper(trim(new.ticker));
    if not exists (select 1 from universe where ticker = new.ticker) then
        raise exception 'unknown ticker %: not in the screener universe', new.ticker;
    end if;
    return new;
end $$;
create trigger holdings_ticker_check before insert or update of ticker on holdings
    for each row execute function check_holding_ticker();

create table sales (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null default auth.uid(),
    holding_id  uuid not null references holdings (id) on delete cascade,
    sell_date   date not null default current_date,
    shares_sold numeric not null check (shares_sold > 0),
    sell_price  numeric not null check (sell_price > 0),
    notes       text,
    created_at  timestamptz not null default now()
);
create index sales_holding_idx on sales (holding_id);

-- A sale must belong to the lot's owner, be on/after the buy date, and never oversell the lot.
create or replace function check_sale() returns trigger language plpgsql set search_path = public as $$
declare lot holdings%rowtype; already numeric;
begin
    select * into lot from holdings where id = new.holding_id for update;
    if not found or lot.user_id <> new.user_id then
        raise exception 'sale user does not own holding %', new.holding_id;
    end if;
    if new.sell_date < lot.buy_date then
        raise exception 'sell_date % is before buy_date %', new.sell_date, lot.buy_date;
    end if;
    select coalesce(sum(shares_sold), 0) into already from sales
    where holding_id = new.holding_id and id <> new.id;
    if already + new.shares_sold > lot.shares then
        raise exception 'cannot sell % shares: only % of % remain in this lot',
            new.shares_sold, lot.shares - already, lot.shares;
    end if;
    return new;
end $$;
create trigger sales_check before insert or update on sales for each row execute function check_sale();

-- Lowering a lot's shares below what has already been sold is rejected too.
create or replace function check_holding_shares() returns trigger language plpgsql set search_path = public as $$
begin
    if new.shares < (select coalesce(sum(shares_sold), 0) from sales where holding_id = new.id) then
        raise exception 'shares (%) cannot be less than shares already sold', new.shares;
    end if;
    return new;
end $$;
create trigger holdings_shares_check before update of shares on holdings
    for each row execute function check_holding_shares();

-- An alert stays "active" (cleared_at null) while its condition holds; it fires again only after the
-- condition has cleared (cleared_at set by the pipeline) and then recurs.
create table alerts (
    id                  uuid primary key default gen_random_uuid(),
    user_id             uuid not null,
    holding_id          uuid not null references holdings (id) on delete cascade,
    ticker              text not null,
    alert_type          text not null check (alert_type in ('TARGET_HIT', 'APPROACHING', 'MANUAL_TARGET',
                                                            'THESIS_BREAK', 'QUALITATIVE', 'DATA_ISSUE')),
    triggered_at        timestamptz not null default now(),
    price_at_alert      numeric,
    fair_value_at_alert numeric,
    message             text not null,
    acknowledged        boolean not null default false,
    cleared_at          timestamptz
);
create index alerts_user_idx on alerts (user_id, acknowledged);
create unique index alerts_one_active on alerts (holding_id, alert_type) where cleared_at is null;

alter table holdings enable row level security;
alter table sales enable row level security;
alter table alerts enable row level security;

create policy holdings_own on holdings for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy sales_own on sales for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy alerts_select_own on alerts for select to authenticated using (user_id = auth.uid());
create policy alerts_ack_own on alerts for update to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());

revoke all on holdings, sales, alerts from anon;
grant select, insert, update, delete on holdings, sales to authenticated;
revoke all on alerts from authenticated;
grant select on alerts to authenticated;
grant update (acknowledged) on alerts to authenticated;

-- Open lots with live screen data. Progress = how far the price has moved from buy price toward
-- the current fair value (0% at buy, 100% at fair value); null when fair value <= buy price.
create or replace view holdings_live with (security_invoker = true) as
with sold as (
    select holding_id, sum(shares_sold) as shares_sold from sales group by holding_id
)
select
    h.id, h.user_id, h.ticker, s.name, h.buy_date, h.shares,
    coalesce(sold.shares_sold, 0)                         as shares_sold,
    h.shares - coalesce(sold.shares_sold, 0)              as shares_open,
    h.buy_price, h.target_price, h.notes,
    h.fair_value_at_buy, h.mos_at_buy, h.composite_at_buy, h.status_at_buy,
    s.as_of, s.price, s.fair_value, s.mos, s.status, s.status_reason, s.composite,
    s.qualitative_gate,
    (h.shares - coalesce(sold.shares_sold, 0)) * h.buy_price                 as cost_basis,
    (h.shares - coalesce(sold.shares_sold, 0)) * s.price                     as market_value,
    (h.shares - coalesce(sold.shares_sold, 0)) * (s.price - h.buy_price)     as unrealized_gain,
    (s.price - h.buy_price) / h.buy_price                                    as unrealized_pct,
    case when s.fair_value > h.buy_price
         then (s.price - h.buy_price) / (s.fair_value - h.buy_price) end      as progress_to_fair_value,
    current_date - h.buy_date                                                as days_held,
    h.created_at
from holdings h
left join sold on sold.holding_id = h.id
left join latest_screen s on s.ticker = h.ticker
where h.shares - coalesce(sold.shares_sold, 0) > 0;

-- Every sale with its realized gain, for the Closed Positions section.
create or replace view sales_realized with (security_invoker = true) as
with sold as (
    select holding_id, sum(shares_sold) as shares_sold from sales group by holding_id
)
select
    x.id, x.user_id, x.holding_id, h.ticker, u.name, h.buy_date, h.buy_price,
    x.sell_date, x.shares_sold, x.sell_price, x.notes,
    x.shares_sold * h.buy_price                       as cost_basis,
    x.shares_sold * x.sell_price                      as proceeds,
    x.shares_sold * (x.sell_price - h.buy_price)      as realized_gain,
    (x.sell_price - h.buy_price) / h.buy_price        as realized_pct,
    x.sell_date - h.buy_date                          as days_held,
    sold.shares_sold >= h.shares                      as lot_closed
from sales x
join holdings h on h.id = x.holding_id
join sold on sold.holding_id = x.holding_id
left join universe u on u.ticker = h.ticker;

-- One row per user (RLS limits it to the caller): portfolio totals and unacknowledged alert count.
create or replace view portfolio_summary with (security_invoker = true) as
select
    auth.uid()                                                           as user_id,
    (select count(*) from holdings_live)                                 as open_lots,
    (select sum(cost_basis) from holdings_live)                          as cost_basis,
    (select sum(market_value) from holdings_live)                        as market_value,
    (select sum(unrealized_gain) from holdings_live)                     as unrealized_gain,
    (select sum(unrealized_gain) / nullif(sum(cost_basis), 0) from holdings_live) as unrealized_pct,
    (select coalesce(sum(realized_gain), 0) from sales_realized)         as realized_gain,
    (select count(*) from alerts where not acknowledged)                 as unacknowledged_alerts;

revoke all on holdings_live, sales_realized, portfolio_summary from anon;
grant select on holdings_live, sales_realized, portfolio_summary to authenticated;
