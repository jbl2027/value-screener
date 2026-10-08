-- Two-run averaging for flag/watch companies: component scores can be fractional (mean of two runs),
-- runs = number of Gemini runs averaged (1 or 2), run_scores = each run's components and llm_score.
-- llm_latest selects * from llm_scores, so it is dropped and recreated around the type change.

drop view if exists llm_latest;

alter table llm_scores
    alter column moat type double precision,
    alter column management_candor type double precision,
    alter column risk_change_score type double precision,
    alter column cam_score type double precision,
    add column if not exists runs smallint not null default 1,
    add column if not exists run_scores jsonb;

create view llm_latest with (security_invoker = true) as
select distinct on (cik) *
from llm_scores
order by cik, filed desc nulls last, scored_at desc;

revoke all on llm_latest from anon;
grant select on llm_latest to authenticated;
