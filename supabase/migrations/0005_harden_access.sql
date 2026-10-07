-- Read-only hardening for the frontend.
-- Supabase's default grants give anon and authenticated full privileges (incl. TRUNCATE, which
-- RLS does not govern) on every table. Writers are the Python jobs (postgres role), so:
--   anon           no privileges at all (RLS already returns nothing; this removes the grants too)
--   authenticated  SELECT only, on the dashboard's tables and views
--   every view     security_invoker = true, so views apply the caller's RLS, not the owner's

do $$
declare v record;
begin
    for v in select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'public' and c.relkind = 'v'
    loop
        execute format('alter view public.%I set (security_invoker = true)', v.relname);
    end loop;
end $$;

revoke all on all tables in schema public from anon;
revoke all on all sequences in schema public from anon;
revoke all on all functions in schema public from anon;

revoke insert, update, delete, truncate, references, trigger on all tables in schema public from authenticated;
revoke all on all sequences in schema public from authenticated;
revoke all on schema_migrations from authenticated;
grant select on universe, facts, fundamentals_summary, prices, metrics_latest, valuations_latest, scores_latest,
                screen_history, runs, llm_scores, filing_flags, latest_screen, llm_latest to authenticated;

-- Future tables/views created by the postgres role (migrations) get no anon or write grants.
alter default privileges for role postgres in schema public revoke all on tables from anon;
alter default privileges for role postgres in schema public revoke all on sequences from anon;
alter default privileges for role postgres in schema public revoke all on functions from anon;
alter default privileges for role postgres in schema public
    revoke insert, update, delete, truncate, references, trigger on tables from authenticated;
