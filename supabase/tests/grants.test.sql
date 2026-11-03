-- Which functions may each role run? Pins the exact list, so a function that is callable by clients
-- by accident (for example through default privileges) fails a test instead of shipping.
-- Run with scripts/run_pgtap.py. Read-only: it only inspects privileges.

begin;
create extension if not exists pgtap with schema extensions;
select no_plan();

-- our own functions: not part of an extension
create temp table pub_fn as
select p.oid, p.proname
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e');
create temp table priv_fn as
select p.oid, p.proname
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'private';

select is((select count(*)::int from pub_fn where has_function_privilege('anon', oid, 'EXECUTE')), 0,
          'anon can run none of the public functions');

select is((select array_agg(proname::text order by proname::text) from pub_fn where has_function_privilege('authenticated', oid, 'EXECUTE')),
          array['accept_call', 'ack_alert', 'ack_password_changed', 'decline_call', 'end_call', 'finish_wrap_up',
                'heartbeat', 'lookup_customer', 'set_status', 'start_run', 'stop_run'],
          'signed-in users can run exactly the intended RPCs');

select ok(not has_function_privilege('authenticated', 'public.system_end_call(uuid, text)', 'EXECUTE'), 'a client cannot run system_end_call');
select ok(not has_function_privilege('authenticated', 'public.rate_limit_hit(text, integer, interval)', 'EXECUTE'), 'a client cannot run the rate limiter');
select ok(has_function_privilege('service_role', 'public.system_end_call(uuid, text)', 'EXECUTE'), 'the server (secret key) can run system_end_call');
select ok(has_function_privilege('service_role', 'public.rate_limit_hit(text, integer, interval)', 'EXECUTE'), 'the server can run the rate limiter');

-- private helpers: clients may run only the four that the access rules need
select is((select array_agg(proname::text order by proname::text) from priv_fn where has_function_privilege('authenticated', oid, 'EXECUTE')),
          array['face_mfa_ok', 'is_active_agent', 'leads_team', 'my_team'],
          'signed-in users can run only the access-rule helpers in the private schema');
select is((select count(*)::int from priv_fn where has_function_privilege('anon', oid, 'EXECUTE')), 0, 'anon can run no private function');

-- the dispatcher, sweeper and simulation tick are for the database scheduler only
select ok(not has_function_privilege('authenticated', 'private.sim_tick()', 'EXECUTE'), 'a client cannot run the simulation tick');
select ok(not has_function_privilege('authenticated', 'private.sweep_calls()', 'EXECUTE'), 'a client cannot run the sweeper');
select ok(not has_function_privilege('authenticated', 'private.dispatch_calls(uuid)', 'EXECUTE'), 'a client cannot run dispatch directly');

-- the scheduled jobs exist and run as expected
select is((select count(*)::int from cron.job where jobname in ('bpo-sweep', 'bpo-sim-tick')), 2, 'both scheduled jobs exist');
select is((select schedule from cron.job where jobname = 'bpo-sweep'), '5 seconds', 'the sweeper runs every 5 seconds');
select is((select schedule from cron.job where jobname = 'bpo-sim-tick'), '10 seconds', 'the simulation tick runs every 10 seconds');

select * from finish();
rollback;
