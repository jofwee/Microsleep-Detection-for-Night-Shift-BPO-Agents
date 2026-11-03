-- Tests for the simulated call center: agent state machine, dispatch, sweeps, customer lookup,
-- access rules and simulation runs. Run with scripts/run_pgtap.py (no Docker) or `supabase test db`.
-- One transaction that is rolled back. NOTE: now() is constant inside a transaction, so every
-- "time passes" step below moves a timestamp by hand.
--
-- Cast: team A (leader LA, agents A1 A2 A3), team B (leader LB, agent B1).
-- Each agent holds a face verification for their own login session.

begin;
create extension if not exists pgtap with schema extensions;
select no_plan();

-- ---------------------------------------------------------------------------- helpers
create function pg_temp.login(p_user uuid, p_session uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',
    json_build_object('sub', p_user, 'role', 'authenticated', 'session_id', p_session)::text, true);
  execute 'set local role authenticated';
end $$;

create function pg_temp.queue_call(p_id uuid, p_team uuid, p_age interval default '0 seconds') returns void language sql as $$
  insert into public.calls (id, team_id, scenario_id, customer_id, language, mood, queued_at)
  select p_id, p_team, 'internet_down', (select id from public.customers order by account_no limit 1), 'en', 'calm', now() - p_age;
$$;

create function pg_temp.set_agent(
  p_agent uuid, p_team uuid, p_status text, p_monitor text default 'LIVE', p_identity boolean default true,
  p_fatigue text default 'ALERT', p_hb interval default '1 second', p_since interval default '1 minute'
) returns void language sql as $$
  insert into public.agent_status (agent_id, team_id, status, monitor_state, identity_ok, fatigue_state, last_heartbeat_at, status_since)
  values (p_agent, p_team, p_status::public.agent_state, p_monitor, p_identity, p_fatigue, now() - p_hb, now() - p_since)
  on conflict (agent_id) do update set
    status = excluded.status, monitor_state = excluded.monitor_state, identity_ok = excluded.identity_ok,
    fatigue_state = excluded.fatigue_state, last_heartbeat_at = excluded.last_heartbeat_at,
    status_since = excluded.status_since, current_call_id = null, missed_offers = 0, hold_pending = null;
$$;

-- ---------------------------------------------------------------------------- fixtures
insert into auth.users (id, email) values
  ('a0000000-0000-0000-0000-000000000001', 'la@example.test'),
  ('a0000000-0000-0000-0000-000000000011', 'a1@example.test'),
  ('a0000000-0000-0000-0000-000000000012', 'a2@example.test'),
  ('a0000000-0000-0000-0000-000000000013', 'a3@example.test'),
  ('b0000000-0000-0000-0000-000000000001', 'lb@example.test'),
  ('b0000000-0000-0000-0000-000000000011', 'b1@example.test');
insert into public.teams (id, name) values
  ('aa000000-0000-0000-0000-000000000001', 'pgtap Team A'), ('bb000000-0000-0000-0000-000000000001', 'pgtap Team B');
insert into public.profiles (id, role, team_id, full_name, must_change_password) values
  ('a0000000-0000-0000-0000-000000000001', 'team_leader', 'aa000000-0000-0000-0000-000000000001', 'Leader A', false),
  ('a0000000-0000-0000-0000-000000000011', 'agent', 'aa000000-0000-0000-0000-000000000001', 'Agent A1', false),
  ('a0000000-0000-0000-0000-000000000012', 'agent', 'aa000000-0000-0000-0000-000000000001', 'Agent A2', false),
  ('a0000000-0000-0000-0000-000000000013', 'agent', 'aa000000-0000-0000-0000-000000000001', 'Agent A3', false),
  ('b0000000-0000-0000-0000-000000000001', 'team_leader', 'bb000000-0000-0000-0000-000000000001', 'Leader B', false),
  ('b0000000-0000-0000-0000-000000000011', 'agent', 'bb000000-0000-0000-0000-000000000001', 'Agent B1', false);
update public.teams set leader_id = 'a0000000-0000-0000-0000-000000000001' where id = 'aa000000-0000-0000-0000-000000000001';
update public.teams set leader_id = 'b0000000-0000-0000-0000-000000000001' where id = 'bb000000-0000-0000-0000-000000000001';
insert into public.workstations (id, team_id, token_hash) values
  ('ac000000-0000-0000-0000-000000000001', 'aa000000-0000-0000-0000-000000000001', '\x01'),
  ('bc000000-0000-0000-0000-000000000001', 'bb000000-0000-0000-0000-000000000001', '\x02');
insert into public.face_verifications (id, agent_id, team_id, auth_session_id, workstation_id, score, liveness_ok, expires_at) values
  ('d1000000-0000-0000-0000-000000000011', 'a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000011', 'ac000000-0000-0000-0000-000000000001', 0.8, true, now() + interval '12 hours'),
  ('d1000000-0000-0000-0000-000000000012', 'a0000000-0000-0000-0000-000000000012', 'aa000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000012', 'ac000000-0000-0000-0000-000000000001', 0.8, true, now() + interval '12 hours'),
  ('d1000000-0000-0000-0000-000000000013', 'a0000000-0000-0000-0000-000000000013', 'aa000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000013', 'ac000000-0000-0000-0000-000000000001', 0.8, true, now() + interval '12 hours'),
  ('d2000000-0000-0000-0000-000000000011', 'b0000000-0000-0000-0000-000000000011', 'bb000000-0000-0000-0000-000000000001', 'c2000000-0000-0000-0000-000000000011', 'bc000000-0000-0000-0000-000000000001', 0.8, true, now() + interval '12 hours');
insert into public.scenarios (id, category, title, brief, customer, facts, difficulty, languages, moods) values
  ('billing_dispute', 'BILLING', 'Billing', 'b', 'c', '{"k":"v"}', 2, '{en,taglish}', '{calm,frustrated}'),
  ('internet_down', 'TECH_SUPPORT', 'Internet', 'b', 'c', '{"k":"v"}', 2, '{en,tl,taglish}', '{calm,frustrated,confused}')
  on conflict (id) do nothing;
insert into public.customers (account_no, full_name, plan, balance) values
  ('AC-001', 'Customer One', 'Plan 1', 100), ('AC-002', 'Customer Two', 'Plan 2', 200), ('AC-003', 'Customer Three', 'Plan 3', 300);

-- ============================================================================ 1. the agent state machine (RPCs)
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select lives_ok($$select public.set_status('NOT_READY', 'signed in')$$, 'A1 signs in (NOT_READY)');
select throws_ok($$select public.set_status('AVAILABLE')$$, '55000', null, 'AVAILABLE is refused while the monitor is not live');
select is((select public.heartbeat('LIVE', true, 'ALERT') ->> 'status'), 'NOT_READY', 'a heartbeat reports the current status');
select lives_ok($$select public.set_status('AVAILABLE')$$, 'with the monitor live A1 can go AVAILABLE');
select throws_ok($$select public.set_status('ON_CALL')$$, '55000', null, 'a client cannot set ON_CALL');
select throws_ok($$select public.set_status('RINGING')$$, '55000', null, 'a client cannot set RINGING');
select throws_ok($$select public.heartbeat('LIVE', true, 'WEIRD')$$, '22023', null, 'an invalid heartbeat is refused');
reset role;
select is((select status::text from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000011'), 'AVAILABLE', 'A1 is AVAILABLE');
select is((select count(*)::int from public.agent_status_log where agent_id = 'a0000000-0000-0000-0000-000000000011'), 2, 'the status log has both changes');
select ok((select to_ts is not null from public.agent_status_log where agent_id = 'a0000000-0000-0000-0000-000000000011' and status = 'NOT_READY'), 'the previous status was closed in the log');

-- the same agent in a login session WITHOUT a face check
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c9000000-0000-0000-0000-000000000099');
select throws_ok($$select public.set_status('NOT_READY')$$, '42501', null, 'no face check for this login: set_status refused');
select throws_ok($$select public.heartbeat('LIVE', true, 'ALERT')$$, '42501', null, 'no face check for this login: heartbeat refused');
reset role;

select pg_temp.login('a0000000-0000-0000-0000-000000000012', 'c1000000-0000-0000-0000-000000000012');
select throws_ok($$select public.set_status('AVAILABLE')$$, '55000', null, 'from OFFLINE an agent must sign in (NOT_READY) first');
reset role;
select pg_temp.login('a0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000001');
select throws_ok($$select public.set_status('NOT_READY')$$, '42501', null, 'a team leader has no agent status');
reset role;

-- ============================================================================ 2. who is eligible for a call
-- A1 is the only agent, in every state in turn, with one waiting call (C1).
select pg_temp.queue_call('ca000000-0000-0000-0000-000000000001', 'aa000000-0000-0000-0000-000000000001');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'NOT_READY');
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'NOT_READY gets no call');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'SYSTEM_HOLD');
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'SYSTEM_HOLD gets no call');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'OFFLINE');
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'OFFLINE gets no call');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE', p_monitor => 'OFF');
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'AVAILABLE with the camera monitor off gets no call');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE', p_identity => false);
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'identity not confirmed: no call');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE', p_fatigue => 'MICROSLEEP');
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'a flagged microsleep: no call');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE', p_hb => '30 seconds');
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'a stale heartbeat (30 s): no call');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE');
update public.face_verifications set revoked_at = now() where agent_id = 'a0000000-0000-0000-0000-000000000011';
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'a revoked face check: no call');
update public.face_verifications set revoked_at = null, expires_at = now() - interval '1 second' where agent_id = 'a0000000-0000-0000-0000-000000000011';
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'an expired face check: no call');
update public.face_verifications set expires_at = now() + interval '12 hours' where agent_id = 'a0000000-0000-0000-0000-000000000011';
select pg_temp.set_agent('b0000000-0000-0000-0000-000000000011', 'bb000000-0000-0000-0000-000000000001', 'AVAILABLE');
update public.agent_status set status = 'NOT_READY' where agent_id = 'a0000000-0000-0000-0000-000000000011';
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'an eligible agent of ANOTHER team gets no call of team A');
select is((select status::text from public.calls where id = 'ca000000-0000-0000-0000-000000000001'), 'QUEUED', 'the call is still waiting after all of that');

update public.agent_status set status = 'AVAILABLE' where agent_id = 'a0000000-0000-0000-0000-000000000011';
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 1, 'a fully eligible agent gets the call');
select is((select status::text || '/' || agent_id::text from public.calls where id = 'ca000000-0000-0000-0000-000000000001'), 'OFFERED/a0000000-0000-0000-0000-000000000011', 'the call is OFFERED to A1');
select is((select status::text || '/' || current_call_id::text from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000011'), 'RINGING/ca000000-0000-0000-0000-000000000001', 'A1 is RINGING with that call');
select is((select count(*)::int from public.call_offers where call_id = 'ca000000-0000-0000-0000-000000000001'), 1, 'the offer was logged');
select ok((select offer_expires_at > offered_at from public.calls where id = 'ca000000-0000-0000-0000-000000000001'), 'the offer has an expiry time');

-- ============================================================================ 3. longest idle first, one call per agent
update public.calls set status = 'ABANDONED' where id = 'ca000000-0000-0000-0000-000000000001';
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE', p_since => '10 minutes');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000012', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE', p_since => '5 minutes');
select pg_temp.queue_call('ca000000-0000-0000-0000-000000000002', 'aa000000-0000-0000-0000-000000000001', '30 seconds');
select pg_temp.queue_call('ca000000-0000-0000-0000-000000000003', 'aa000000-0000-0000-0000-000000000001', '20 seconds');
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 2, 'two agents, two calls: both are offered');
select is((select agent_id::text from public.calls where id = 'ca000000-0000-0000-0000-000000000002'), 'a0000000-0000-0000-0000-000000000011', 'the older call goes to the agent idle longest');
select is((select agent_id::text from public.calls where id = 'ca000000-0000-0000-0000-000000000003'), 'a0000000-0000-0000-0000-000000000012', 'the next call goes to the next agent');
select pg_temp.queue_call('ca000000-0000-0000-0000-000000000004', 'aa000000-0000-0000-0000-000000000001', '10 seconds');
select is(private.dispatch_calls('aa000000-0000-0000-0000-000000000001'), 0, 'nobody is free: the third call keeps waiting');

-- ============================================================================ 4. accept, decline, timeout
select pg_temp.login('a0000000-0000-0000-0000-000000000012', 'c1000000-0000-0000-0000-000000000012');
select throws_ok($$select public.accept_call('ca000000-0000-0000-0000-000000000002')$$, '55000', null, 'A2 cannot accept a call offered to A1');
reset role;
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select lives_ok($$select public.accept_call('ca000000-0000-0000-0000-000000000002')$$, 'A1 accepts their call');
select throws_ok($$select public.accept_call('ca000000-0000-0000-0000-000000000002')$$, '55000', null, 'a call cannot be accepted twice');
reset role;
select is((select status::text from public.calls where id = 'ca000000-0000-0000-0000-000000000002'), 'ACTIVE', 'the call is ACTIVE');
select is((select status::text from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000011'), 'ON_CALL', 'A1 is ON_CALL');
select is((select outcome from public.call_offers where call_id = 'ca000000-0000-0000-0000-000000000002'), 'accepted', 'the offer outcome is accepted');

-- A2 declines C3: it goes back to the queue, is never offered to A2 again, and A2 is offered the next call (C4)
select pg_temp.login('a0000000-0000-0000-0000-000000000012', 'c1000000-0000-0000-0000-000000000012');
select lives_ok($$select public.decline_call('ca000000-0000-0000-0000-000000000003')$$, 'A2 declines their call');
reset role;
select is((select status::text from public.calls where id = 'ca000000-0000-0000-0000-000000000003'), 'QUEUED', 'the declined call is back in the queue');
select is((select outcome from public.call_offers where call_id = 'ca000000-0000-0000-0000-000000000003'), 'declined', 'the offer outcome is declined');
select is((select agent_id::text from public.calls where id = 'ca000000-0000-0000-0000-000000000004'), 'a0000000-0000-0000-0000-000000000012', 'A2 is offered the next call: a call nobody can take does not block the queue');

-- the offer on C4 times out; the sweep returns it and counts the miss
update public.calls set offer_expires_at = now() - interval '1 second' where id = 'ca000000-0000-0000-0000-000000000004';
select private.sweep_calls();
select is((select status::text from public.calls where id = 'ca000000-0000-0000-0000-000000000004'), 'QUEUED', 'an unanswered offer returns to the queue');
select is((select outcome from public.call_offers where call_id = 'ca000000-0000-0000-0000-000000000004'), 'timeout', 'the outcome is timeout');
select is((select status::text || '/' || missed_offers from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000012'), 'AVAILABLE/1', 'one missed offer: still AVAILABLE');
-- a second miss puts the agent on hold
select pg_temp.queue_call('ca000000-0000-0000-0000-000000000005', 'aa000000-0000-0000-0000-000000000001');
select private.dispatch_calls('aa000000-0000-0000-0000-000000000001');
select is((select status::text from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000012'), 'RINGING', 'A2 is offered a fresh call');
update public.calls set offer_expires_at = now() - interval '1 second' where id = 'ca000000-0000-0000-0000-000000000005';
select private.sweep_calls();
select is((select status::text || '/' || coalesce(reason, '') from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000012'), 'SYSTEM_HOLD/missed offers', 'two missed offers in a row: SYSTEM_HOLD');

-- accepting an expired offer is refused
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000013', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE');
select private.dispatch_calls('aa000000-0000-0000-0000-000000000001');
select is((select status::text from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000013'), 'RINGING', 'A3 is offered a call');
update public.calls set offer_expires_at = now() - interval '1 second' where agent_id = 'a0000000-0000-0000-0000-000000000013' and status = 'OFFERED';
select pg_temp.login('a0000000-0000-0000-0000-000000000013', 'c1000000-0000-0000-0000-000000000013');
select throws_ok($$select public.accept_call((select id from public.calls where agent_id = 'a0000000-0000-0000-0000-000000000013' and status = 'OFFERED'))$$, '55000', null, 'accepting an expired offer is refused');
reset role;
select private.sweep_calls();

-- ============================================================================ 5. impatient callers
update public.calls set status = 'ABANDONED' where status in ('QUEUED', 'OFFERED') and team_id = 'aa000000-0000-0000-0000-000000000001';
select pg_temp.queue_call('ca000000-0000-0000-0000-000000000006', 'aa000000-0000-0000-0000-000000000001', '200 seconds');
select pg_temp.queue_call('ca000000-0000-0000-0000-000000000007', 'aa000000-0000-0000-0000-000000000001', '5 seconds');
select private.sweep_calls();
select is((select status::text || '/' || end_reason from public.calls where id = 'ca000000-0000-0000-0000-000000000006'), 'ABANDONED/abandoned', 'a caller who waited past their patience is ABANDONED');
select is((select status::text from public.calls where id = 'ca000000-0000-0000-0000-000000000007'), 'QUEUED', 'a caller within their patience keeps waiting');

-- ============================================================================ 6. ending a call and wrap-up
update public.calls set status = 'ABANDONED' where id = 'ca000000-0000-0000-0000-000000000007';
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select throws_ok($$select public.finish_wrap_up('ca000000-0000-0000-0000-000000000002', 'resolved')$$, '55000', null, 'wrap-up is refused while the call is still active');
select lives_ok($$select public.end_call('ca000000-0000-0000-0000-000000000002')$$, 'A1 ends their call');
reset role;
select is((select status::text || '/' || end_reason from public.calls where id = 'ca000000-0000-0000-0000-000000000002'), 'WRAP_UP/agent_ended', 'the call is in wrap-up');
select is((select status::text from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000011'), 'WRAP_UP', 'A1 is in WRAP_UP');
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select throws_ok($$select public.set_status('NOT_READY')$$, '55000', null, 'status cannot be changed during wrap-up');
select throws_ok($$select public.finish_wrap_up('ca000000-0000-0000-0000-000000000002', 'maybe')$$, '22023', null, 'an invalid disposition is refused');
reset role;
select pg_temp.login('a0000000-0000-0000-0000-000000000012', 'c1000000-0000-0000-0000-000000000012');
select throws_ok($$select public.finish_wrap_up('ca000000-0000-0000-0000-000000000002', 'resolved')$$, '55000', null, 'another agent cannot finish my wrap-up');
reset role;
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select lives_ok($$select public.finish_wrap_up('ca000000-0000-0000-0000-000000000002', 'resolved', 'refund filed')$$, 'A1 finishes wrap-up');
reset role;
select is((select status::text || '/' || disposition || '/' || notes from public.calls where id = 'ca000000-0000-0000-0000-000000000002'), 'COMPLETED/resolved/refund filed', 'the call is COMPLETED with its disposition and notes');
select is((select status::text from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000011'), 'AVAILABLE', 'A1 is AVAILABLE again');

-- the caller hangs up (only the Edge Function, with the secret key, can say so)
select pg_temp.queue_call('ca000000-0000-0000-0000-000000000008', 'aa000000-0000-0000-0000-000000000001');
select private.dispatch_calls('aa000000-0000-0000-0000-000000000001');
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select public.accept_call('ca000000-0000-0000-0000-000000000008');
select throws_ok($$select public.system_end_call('ca000000-0000-0000-0000-000000000008', 'caller_hangup')$$, '42501', null, 'clients cannot end a call on the caller''s behalf');
reset role;
set local role service_role;
select throws_ok($$select public.system_end_call('ca000000-0000-0000-0000-000000000008', 'because')$$, '22023', null, 'an invalid caller reason is refused');
select lives_ok($$select public.system_end_call('ca000000-0000-0000-0000-000000000008', 'caller_hangup')$$, 'the caller hangs up');
select lives_ok($$select public.system_end_call('ca000000-0000-0000-0000-000000000008', 'caller_hangup')$$, 'ending an already ended call changes nothing');
reset role;
select is((select status::text || '/' || end_reason from public.calls where id = 'ca000000-0000-0000-0000-000000000008'), 'WRAP_UP/caller_hangup', 'the call is in wrap-up with the caller''s reason');
select is((select status::text from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000011'), 'WRAP_UP', 'the agent goes to wrap-up');

-- a problem seen DURING a call does not drop the caller; the hold comes after wrap-up
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select is((select public.heartbeat('LIVE', false, 'ALERT') ->> 'status'), 'WRAP_UP', 'an identity problem during wrap-up does not interrupt it');
select lives_ok($$select public.finish_wrap_up('ca000000-0000-0000-0000-000000000008', 'unresolved')$$, 'wrap-up finishes');
reset role;
select is((select status::text || '/' || reason from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000011'), 'SYSTEM_HOLD/identity not confirmed', 'after wrap-up the agent is held (identity not confirmed)');
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select throws_ok($$select public.set_status('AVAILABLE')$$, '55000', null, 'a held agent cannot return while identity is unconfirmed');
select public.heartbeat('LIVE', true, 'ALERT');
select lives_ok($$select public.set_status('AVAILABLE')$$, 'once identity is confirmed again the agent can return');
reset role;

-- a problem while AVAILABLE or RINGING takes effect at once
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select is((select public.heartbeat('LIVE', true, 'MICROSLEEP') ->> 'status'), 'SYSTEM_HOLD', 'a microsleep while AVAILABLE: SYSTEM_HOLD at once');
reset role;
select pg_temp.queue_call('ca000000-0000-0000-0000-000000000009', 'aa000000-0000-0000-0000-000000000001');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000013', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE');
select private.dispatch_calls('aa000000-0000-0000-0000-000000000001');
select is((select status::text from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000013'), 'RINGING', 'A3 is ringing');
select pg_temp.login('a0000000-0000-0000-0000-000000000013', 'c1000000-0000-0000-0000-000000000013');
select is((select public.heartbeat('OFF', true, 'ALERT') ->> 'status'), 'SYSTEM_HOLD', 'the camera going off while ringing: SYSTEM_HOLD');
reset role;
select is((select status::text from public.calls where id = 'ca000000-0000-0000-0000-000000000009'), 'QUEUED', 'the ringing call was released back to the queue');

-- ============================================================================ 7. silent agents and lost face checks
update public.calls set status = 'ABANDONED' where status in ('QUEUED', 'OFFERED') and team_id = 'aa000000-0000-0000-0000-000000000001';
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE', p_hb => '2 minutes');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000012', 'aa000000-0000-0000-0000-000000000001', 'NOT_READY');
update public.face_verifications set revoked_at = now() where agent_id = 'a0000000-0000-0000-0000-000000000012';
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000013', 'aa000000-0000-0000-0000-000000000001', 'ON_CALL');
update public.calls set status = 'ACTIVE', agent_id = 'a0000000-0000-0000-0000-000000000013' where id = 'ca000000-0000-0000-0000-000000000009';
update public.agent_status set current_call_id = 'ca000000-0000-0000-0000-000000000009', last_heartbeat_at = now() - interval '2 minutes' where agent_id = 'a0000000-0000-0000-0000-000000000013';
select private.sweep_calls();
select is((select status::text || '/' || reason from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000011'), 'OFFLINE/heartbeat lost', 'an agent whose app went silent is OFFLINE');
select is((select status::text || '/' || end_reason from public.calls where id = 'ca000000-0000-0000-0000-000000000009'), 'COMPLETED/agent_lost', 'a call whose agent vanished is closed as agent_lost');
select is((select status::text || '/' || reason from public.agent_status where agent_id = 'a0000000-0000-0000-0000-000000000012'), 'OFFLINE/face check no longer valid', 'an agent whose face check was revoked goes OFFLINE');
update public.face_verifications set revoked_at = null where agent_id = 'a0000000-0000-0000-0000-000000000012';

-- ============================================================================ 8. customer lookup
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'ON_CALL');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000012', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE');
select pg_temp.login('a0000000-0000-0000-0000-000000000012', 'c1000000-0000-0000-0000-000000000012');
select throws_ok($$select public.lookup_customer('AC-001')$$, '55000', null, 'customer lookup is refused outside a call');
reset role;
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select is((select public.lookup_customer(' ac-002 ') ->> 'full_name'), 'Customer Two', 'lookup works during a call, ignoring case and spaces');
select ok((select public.lookup_customer('AC-002') ? 'id') is not true, 'the internal id is not returned');
select ok((select public.lookup_customer('AC-999')) is null, 'an unknown account returns nothing');
reset role;
select pg_temp.login('a0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000001');
select throws_ok($$select public.lookup_customer('AC-001')$$, '42501', null, 'a team leader cannot use the customer lookup');
reset role;

-- ============================================================================ 9. who can see what
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'AVAILABLE');
select pg_temp.set_agent('a0000000-0000-0000-0000-000000000013', 'aa000000-0000-0000-0000-000000000001', 'NOT_READY');
insert into public.call_turns (call_id, agent_id, team_id, seq, speaker, text)
  values ('ca000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 1, 'caller', 'Hello po');
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select is((select count(*)::int from public.calls where agent_id <> 'a0000000-0000-0000-0000-000000000011'), 0, 'an agent sees none of another agent''s calls');
select ok((select count(*) from public.calls) >= 1, 'an agent sees their own calls');
select is((select count(*)::int from public.call_turns), 1, 'an agent sees the transcript of their own call');
select is((select count(*)::int from public.agent_status), 1, 'an agent sees only their own status');
select throws_ok($$select * from public.scenarios$$, '42501', null, 'no client can read scenarios (they hold the caller''s facts)');
select throws_ok($$select * from public.customers$$, '42501', null, 'no client can read the customer table directly');
select throws_ok($$insert into public.calls (team_id, scenario_id, customer_id, language, mood) select team_id, scenario_id, customer_id, language, mood from public.calls limit 1$$, '42501', null, 'a client cannot create calls');
select throws_ok($$update public.calls set status = 'COMPLETED'$$, '42501', null, 'a client cannot change calls');
select throws_ok($$update public.agent_status set status = 'AVAILABLE'$$, '42501', null, 'a client cannot change its status directly (only through the RPCs)');
select throws_ok($$insert into public.call_turns (call_id, team_id, seq, speaker, text) values ('ca000000-0000-0000-0000-000000000002', 'aa000000-0000-0000-0000-000000000001', 99, 'agent', 'forged')$$, '42501', null, 'a client cannot forge a transcript');
reset role;
select pg_temp.login('a0000000-0000-0000-0000-000000000012', 'c1000000-0000-0000-0000-000000000012');
select is((select count(*)::int from public.call_turns), 0, 'another agent does not see that transcript');
reset role;
select pg_temp.login('a0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000001');
select ok((select count(*) from public.calls) >= 3, 'leader A sees their team''s calls');
select is((select count(*)::int from public.agent_status), 3, 'leader A sees all 3 agents'' status');
select is((select count(*)::int from public.call_turns), 1, 'leader A sees the transcript');
reset role;
select pg_temp.login('b0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000002');
select is((select count(*)::int from public.calls), 0, 'leader B sees no calls of team A');
select is((select count(*)::int from public.agent_status), 1, 'leader B sees only their own agent');
select is((select count(*)::int from public.call_turns), 0, 'leader B sees no transcripts of team A');
reset role;

-- an event can name the call it happened in, but only the agent''s own call
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000011","role":"authenticated","session_id":"c1000000-0000-0000-0000-000000000011"}', true);
insert into public.sessions (id, workstation_id) values ('e1000000-0000-0000-0000-000000000001', 'ac000000-0000-0000-0000-000000000001');
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select lives_ok($$insert into public.events (session_id, type, ts, call_id) values ('e1000000-0000-0000-0000-000000000001', 'YAWN', now(), 'ca000000-0000-0000-0000-000000000002')$$, 'an event can be tied to the agent''s own call');
select throws_ok($$insert into public.events (session_id, type, ts, call_id) values ('e1000000-0000-0000-0000-000000000001', 'YAWN', now(), 'ca000000-0000-0000-0000-000000000009')$$, '42501', null, 'an event cannot be tied to someone else''s call');
select lives_ok($$insert into public.metrics_minute (session_id, minute, status) values ('e1000000-0000-0000-0000-000000000001', now(), 'ON_CALL')$$, 'minute metrics can record the agent status');
reset role;

-- ============================================================================ 10. simulation runs
-- Run ids are kept in session settings (test.runN) so every role can read them; runs are started as
-- postgres with the leader's login claims, which is how the functions see "who is calling".
create function pg_temp.claims(p_user uuid, p_session uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',
    json_build_object('sub', p_user, 'role', 'authenticated', 'session_id', p_session)::text, true);
end $$;

-- park B1 (eligible since section 2) so the simulated calls stay in the queue for these tests
select pg_temp.set_agent('b0000000-0000-0000-0000-000000000011', 'bb000000-0000-0000-0000-000000000001', 'NOT_READY');
select pg_temp.login('a0000000-0000-0000-0000-000000000011', 'c1000000-0000-0000-0000-000000000011');
select throws_ok($$select public.start_run('{}')$$, '42501', null, 'an agent cannot start a run');
reset role;
select pg_temp.login('b0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000002');
select throws_ok($$select public.start_run('{"arrival_per_min": 0}')$$, '22023', null, 'an arrival rate of 0 is refused');
select throws_ok($$select public.start_run('{"language_mix": {"en": 0, "tl": 0}}')$$, '22023', null, 'a language mix adding up to 0 is refused');
select throws_ok($$select public.start_run('{"language_mix": {"fr": 50}}')$$, '22023', null, 'an unknown language in the mix is refused');
select throws_ok($$select public.start_run('{"max_concurrent": 500}')$$, '22023', null, 'too many concurrent calls is refused');
select lives_ok($$select public.start_run('{"arrival_per_min": 30, "max_concurrent": 3, "patience_s": 100}', 42)$$, 'leader B starts a run with seed 42');
select throws_ok($$select public.start_run('{}')$$, '55000', null, 'a team cannot have two running simulations');
reset role;
select set_config('test.run1', (select id::text from public.simulation_runs where team_id = 'bb000000-0000-0000-0000-000000000001' and status = 'RUNNING'), true);
select pg_temp.login('a0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000001');
select throws_ok($$select public.stop_run(current_setting('test.run1')::uuid)$$, '42501', null, 'a leader cannot stop another team''s run');
reset role;
select is((select count(*)::int from public.audit_log where action = 'simulation_started' and team_id = 'bb000000-0000-0000-0000-000000000001'), 1, 'starting a run is audited');

-- first tick: 30 per minute for 10 s = 5 arrivals, capped at max_concurrent 3
select is(private.sim_tick(), 3, 'the first tick creates calls up to the concurrency cap');
select is((select count(*)::int from public.calls where run_id = current_setting('test.run1')::uuid), 3, 'three calls belong to the run');
select is((select calls_created from public.simulation_runs where id = current_setting('test.run1')::uuid), 3, 'the run counts them');
update public.simulation_runs set last_tick_at = now() - interval '10 seconds' where id = current_setting('test.run1')::uuid;
select is(private.sim_tick(), 0, 'while 3 calls are active, no more are created');
create temp table seq1 as
  select row_number() over (order by c.scenario_id, c.language::text, c.mood, c.customer_id) as n, c.scenario_id, c.language::text as language, c.mood, c.customer_id
  from public.calls c where c.run_id = current_setting('test.run1')::uuid;
select ok((select count(distinct scenario_id) from seq1) >= 1, 'calls got scenarios');
select ok((select bool_and(language in ('en', 'tl', 'taglish')) from seq1), 'calls got valid languages');

-- stopping abandons the queued calls
select pg_temp.login('b0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000002');
select lives_ok($$select public.stop_run(current_setting('test.run1')::uuid)$$, 'leader B stops the run');
select throws_ok($$select public.stop_run(current_setting('test.run1')::uuid)$$, '42501', null, 'a stopped run cannot be stopped again');
reset role;
select is((select count(*)::int from public.calls where run_id = current_setting('test.run1')::uuid and status = 'ABANDONED' and end_reason = 'run_stopped'), 3, 'the waiting calls were abandoned (run_stopped)');
select is((select status from public.simulation_runs where id = current_setting('test.run1')::uuid), 'STOPPED', 'the run is STOPPED');

-- the same seed gives the same calls
select pg_temp.login('b0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000002');
select lives_ok($$select public.start_run('{"arrival_per_min": 30, "max_concurrent": 3, "patience_s": 100}', 42)$$, 'a second run with the same seed starts');
reset role;
select set_config('test.run2', (select id::text from public.simulation_runs where team_id = 'bb000000-0000-0000-0000-000000000001' and status = 'RUNNING'), true);
select is(private.sim_tick(), 3, 'the second run creates three calls');
create temp table seq2 as
  select row_number() over (order by c.scenario_id, c.language::text, c.mood, c.customer_id) as n, c.scenario_id, c.language::text as language, c.mood, c.customer_id
  from public.calls c where c.run_id = current_setting('test.run2')::uuid;
select is((select count(*)::int from seq1 a join seq2 b using (n, scenario_id, language, mood, customer_id)), 3, 'the same seed produces the same scenarios, languages, moods and customers');
select pg_temp.login('b0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000002');
select public.stop_run(current_setting('test.run2')::uuid);
select lives_ok($$select public.start_run('{"arrival_per_min": 30, "max_concurrent": 3, "patience_s": 100}', 43)$$, 'a third run with another seed starts');
reset role;
select set_config('test.run3', (select id::text from public.simulation_runs where team_id = 'bb000000-0000-0000-0000-000000000001' and status = 'RUNNING'), true);
select private.sim_tick();
create temp table seq3 as
  select row_number() over (order by c.scenario_id, c.language::text, c.mood, c.customer_id) as n, c.scenario_id, c.language::text as language, c.mood, c.customer_id
  from public.calls c where c.run_id = current_setting('test.run3')::uuid;
select ok((select count(*)::int from seq1 a join seq3 b using (n, scenario_id, language, mood, customer_id)) < 3, 'a different seed produces different calls');

-- configuration: language mix, scenarios, moods
select pg_temp.login('b0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000002');
select public.stop_run(current_setting('test.run3')::uuid);
select lives_ok($$select public.start_run('{"arrival_per_min": 30, "max_concurrent": 20, "language_mix": {"tl": 100}, "scenario_ids": ["internet_down"], "moods": ["frustrated"]}', 7)$$, 'a run limited to Tagalog, one scenario and one mood');
reset role;
select set_config('test.run4', (select id::text from public.simulation_runs where team_id = 'bb000000-0000-0000-0000-000000000001' and status = 'RUNNING'), true);
select private.sim_tick();
select is((select count(*)::int from public.calls where run_id = current_setting('test.run4')::uuid), 5, 'five calls arrived');
select is((select count(*)::int from public.calls where run_id = current_setting('test.run4')::uuid and language = 'tl' and scenario_id = 'internet_down' and mood = 'frustrated'), 5, 'all of them are Tagalog, internet_down and frustrated');

-- fractional arrival rates accumulate across ticks
select pg_temp.login('b0000000-0000-0000-0000-000000000001', 'c9000000-0000-0000-0000-000000000002');
select public.stop_run(current_setting('test.run4')::uuid);
select lives_ok($$select public.start_run('{"arrival_per_min": 3, "max_concurrent": 20}', 5)$$, 'a slow run: 3 calls per minute');
reset role;
select set_config('test.run5', (select id::text from public.simulation_runs where team_id = 'bb000000-0000-0000-0000-000000000001' and status = 'RUNNING'), true);
select is(private.sim_tick(), 0, 'first tick of a slow run: half a call has arrived, so none yet');
update public.simulation_runs set last_tick_at = now() - interval '10 seconds' where id = current_setting('test.run5')::uuid;
select is(private.sim_tick(), 1, 'the second tick: the carried half completes one call');

-- an eligible agent is offered a simulated call straight away
update public.calls set status = 'ABANDONED' where team_id = 'bb000000-0000-0000-0000-000000000001' and status = 'QUEUED';
select pg_temp.set_agent('b0000000-0000-0000-0000-000000000011', 'bb000000-0000-0000-0000-000000000001', 'AVAILABLE');
update public.simulation_runs set last_tick_at = now() - interval '10 seconds', arrival_carry = 0.9 where id = current_setting('test.run5')::uuid;
select private.sim_tick();
select is((select status::text from public.agent_status where agent_id = 'b0000000-0000-0000-0000-000000000011'), 'RINGING', 'B1 is offered a call as soon as a simulated call arrives');

select * from finish();
rollback;
