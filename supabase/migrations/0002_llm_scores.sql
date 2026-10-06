-- Milestone 8: Gemini 10-K scores. One row per company, 10-K (accession), model, and prompt
-- version, so a new 10-K or a new prompt version gets a fresh score and history is kept.
create table if not exists llm_scores (
    cik                        integer not null,
    ticker                     text not null,
    accn                       text not null,
    filed                      date,
    period                     date,
    model                      text not null,
    prompt_version             text not null,
    as_of                      date,
    moat                       smallint,
    moat_lost_revenue_share    double precision,
    management_candor          smallint,
    risk_change_score          smallint,
    cam_score                  smallint,
    new_risks                  smallint,
    new_risks_serious_company  smallint,
    cam_count                  smallint,
    cam_new                    smallint,
    cam_unusual                smallint,
    cam_topics                 text,
    qualitative_gate           boolean,
    gate_reasons               text,
    gate_review                text,
    risk_added_share           double precision,
    evidence_verified          smallint,
    evidence_total             smallint,
    llm_score                  double precision,
    prompt_tokens              integer,
    output_tokens              integer,
    result                     jsonb,                -- full model output: rationales, evidence
    scored_at                  timestamptz not null default now(),
    primary key (cik, accn, model, prompt_version)
);

alter table llm_scores enable row level security;
drop policy if exists authenticated_read on llm_scores;
create policy authenticated_read on llm_scores for select to authenticated using (true);

-- Latest score per company (most recent 10-K, then most recent scoring).
create or replace view llm_latest with (security_invoker = true) as
select distinct on (cik) *
from llm_scores
order by cik, filed desc nulls last, scored_at desc;

grant select on llm_latest to authenticated;
revoke all on llm_latest from anon;
