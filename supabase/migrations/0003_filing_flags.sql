-- Rule-based qualitative gate detectors on 10-K text (no LLM): one row per company and 10-K.
create table if not exists filing_flags (
    cik                integer not null,
    ticker             text not null,
    accn               text not null,
    filed              date,
    going_concern      boolean not null,
    material_weakness  boolean not null,
    evidence           text,
    checked_at         timestamptz not null default now(),
    primary key (cik, accn)
);
alter table filing_flags enable row level security;
drop policy if exists authenticated_read on filing_flags;
create policy authenticated_read on filing_flags for select to authenticated using (true);
