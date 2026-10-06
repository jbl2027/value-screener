-- Value screener schema (Milestone 9).
-- Writers: Python jobs connect as the postgres role through the pooler (bypasses RLS).
-- Readers: the Lovable frontend as authenticated users (Tiingo free tier = internal use only,
-- so nothing is readable by the anon role).

-- ---------- reference ----------
create table if not exists universe (
    ticker               text primary key,
    cik                  integer not null,
    name                 text,
    sec_name             text,
    sic                  integer,
    sic_description      text,
    sic_division         text,
    sector_model         text not null,          -- standard | bank | insurer | reit
    sector_model_source  text,                   -- sic | override
    captive_finance      boolean not null default false,
    gics_sector          text,
    gics_industry_group  text,
    gics_sub_industry    text,
    fiscal_year_end      text,
    updated_at           timestamptz not null default now()
);
create index if not exists universe_cik on universe (cik);

-- ---------- point-in-time fundamentals ----------
-- One row per (concept, period, distinct reported value) from the first filing that reported it.
-- A later filing with a different value for the same period is a restatement: the earlier row
-- gets superseded_date = that filing's date. As-of query for date D:
--   filed <= D and (superseded_date is null or superseded_date > D)
create table if not exists facts (
    cik              integer not null,
    source_cik       integer,                    -- differs from cik for predecessor entities
    concept          text not null,
    tag              text not null,              -- XBRL tag that supplied the value
    value            double precision not null,
    period_start     date,                       -- null for balance-sheet (instant) values
    period_end       date not null,
    duration_days    smallint,
    fy               smallint,                   -- fiscal year/period of the FILING, not the fact
    fp               text,
    form             text not null,
    filed            date not null,
    accn             text not null,
    superseded_date  date
);
create index if not exists facts_lookup on facts (cik, concept, period_end, filed);

create table if not exists fundamentals_summary (
    cik                 integer primary key,
    tickers             text,
    sector_model        text,
    has_10k             boolean,
    latest_10k_filed    date,
    fy_end              date,
    latest_10k_accn     text,
    stale_10k           boolean,
    fair_value_ready    boolean,
    fair_value_missing  text,
    detail              jsonb,
    updated_at          timestamptz not null default now()
);

-- ---------- prices ----------
create table if not exists prices (
    ticker      text not null,
    date        date not null,
    close       double precision not null,
    open        double precision,
    high        double precision,
    low         double precision,
    volume      bigint,
    prev_close  double precision,
    source      text not null,
    fetched_at  timestamptz,
    primary key (ticker, date)
);

-- ---------- latest results (replaced each run) ----------
create table if not exists metrics_latest (
    cik           integer primary key,
    ticker        text not null,
    as_of         date not null,
    sector_model  text not null,
    detail        jsonb not null                 -- every metric and input basis
);

create table if not exists valuations_latest (
    cik                  integer primary key,
    ticker               text not null,
    as_of                date not null,
    name                 text,
    sector_model         text not null,
    gics_sector          text,
    gics_industry_group  text,
    status               text not null,          -- flag | watch | no_flag | disqualified | ...
    status_reason        text,
    confidence           text,
    confidence_reasons   text,
    warnings             text,
    disqualify_reasons   text,
    missing_inputs       text,
    price                double precision,
    fair_value           double precision,
    mos                  double precision,
    method               text,
    company_multiple     double precision,
    peer_multiple        double precision,
    fair_multiple        double precision,
    peer_group           text,
    peer_count           integer,
    base_value           double precision,
    base_basis           text,
    earnings_yield       double precision,
    treasury_yield       double precision,
    qualitative_gate     boolean,
    qualitative_reasons  text,
    detail               jsonb not null
);

create table if not exists scores_latest (
    cik                   integer primary key,
    ticker                text not null,
    as_of                 date not null,
    composite             double precision,      -- null when not publishable
    composite_confidence  text,
    composite_rank        integer,
    components_available  smallint,
    components_missing    text,
    composite_note        text,
    valuation_score       double precision,
    productivity_score    double precision,
    earnings_quality_score double precision,
    momentum_score        double precision,
    llm_score             double precision,
    growth_score          double precision,
    shareholder_yield_score double precision,
    detail                jsonb not null
);

-- ---------- slim daily history ----------
create table if not exists screen_history (
    as_of         date not null,
    cik           integer not null,
    ticker        text not null,
    status        text not null,
    confidence    text,
    price         double precision,
    fair_value    double precision,
    mos           double precision,
    composite     double precision,
    primary key (as_of, cik)
);

-- ---------- job log ----------
create table if not exists runs (
    id           bigserial primary key,
    job          text not null,
    as_of        date,
    started_at   timestamptz not null default now(),
    finished_at  timestamptz,
    status       text not null default 'running',  -- running | ok | failed
    summary      jsonb
);

-- ---------- frontend view ----------
create or replace view latest_screen with (security_invoker = true) as
select v.ticker, v.name, v.sector_model, v.gics_sector, v.gics_industry_group,
       v.status, v.status_reason, v.confidence, v.confidence_reasons, v.warnings,
       v.price, v.fair_value, v.mos, v.company_multiple, v.fair_multiple, v.peer_group,
       v.qualitative_gate, v.qualitative_reasons,
       s.composite, s.composite_confidence, s.composite_rank, s.components_available,
       v.as_of
from valuations_latest v
left join scores_latest s using (cik);

-- ---------- row level security: authenticated users read; nobody else ----------
do $$
declare t text;
begin
    foreach t in array array['universe', 'facts', 'fundamentals_summary', 'prices', 'metrics_latest',
                             'valuations_latest', 'scores_latest', 'screen_history', 'runs']
    loop
        execute format('alter table %I enable row level security', t);
        execute format('drop policy if exists authenticated_read on %I', t);
        execute format('create policy authenticated_read on %I for select to authenticated using (true)', t);
    end loop;
end $$;

grant select on latest_screen to authenticated;
revoke all on latest_screen from anon;
