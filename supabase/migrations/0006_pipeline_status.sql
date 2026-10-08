-- One-row pipeline health summary for the dashboard, computed at read time so a missed scheduled
-- run shows up even though nothing was written.
-- daily_stale: it is a weekday in New York and the latest successful results load (daily or weekly)
-- finished more than 30 weekday hours ago. Weekend hours are not counted, so Friday's run is not
-- stale on Monday morning; if Friday's run (and its backup) is missed, the dashboard goes stale at
-- ~00:30 ET Monday, 30 weekday hours after Thursday's run.

create or replace view pipeline_status with (security_invoker = true) as
with daily as (
    select max(finished_at) as finished_at from runs
    where status = 'ok' and job like 'load:%' and job like '%results%'
),
daily_as_of as (
    select as_of from runs
    where status = 'ok' and job like 'load:%' and job like '%results%'
    order by finished_at desc nulls last limit 1
),
llm as (
    select finished_at, summary ->> 'stop_reason' as stop_reason from runs
    where status = 'ok' and job = 'llm'
    order by finished_at desc nulls last limit 1
),
ny as (
    select (select finished_at from daily) at time zone 'America/New_York' as last_local,
           now() at time zone 'America/New_York' as now_local
),
age as (
    select (select coalesce(sum(extract(epoch from least(ny.now_local, d + interval '1 day')
                                                 - greatest(ny.last_local, d))), 0) / 3600.0
            from generate_series(date_trunc('day', ny.last_local), ny.now_local, interval '1 day') as d
            where extract(isodow from d) < 6) as weekday_hours
    from ny
)
select
    now()                                                   as checked_at,
    (select finished_at from daily)                         as last_daily_run_at,
    (select as_of from daily_as_of)                         as last_daily_as_of,
    round((extract(epoch from now() - (select finished_at from daily)) / 3600.0)::numeric, 1)
                                                            as daily_age_hours,
    round(age.weekday_hours::numeric, 1)                    as daily_weekday_age_hours,
    extract(isodow from ny.now_local) < 6
        and ((select finished_at from daily) is null or age.weekday_hours > 30)
                                                            as daily_stale,
    (select finished_at from llm)                           as last_llm_run_at,
    (select stop_reason from llm)                           as last_llm_stop_reason
from ny, age;

revoke all on pipeline_status from anon;
grant select on pipeline_status to authenticated;
