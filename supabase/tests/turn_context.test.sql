-- public.call_turn_context: the single database call behind caller-turn and transcribe.
-- Same rules as before it existed: active agent, valid face check for THIS login, own ACTIVE call, rate limit.
-- Run with scripts/run_pgtap.py. One transaction, rolled back.

begin;
create extension if not exists pgtap with schema extensions;
select no_plan();

insert into auth.users (id, email) values
  ('e0000000-0000-0000-0000-000000000011', 'tc1@example.test'),
  ('e0000000-0000-0000-0000-000000000012', 'tc2@example.test'),
  ('e0000000-0000-0000-0000-000000000001', 'tcl@example.test');
insert into public.teams (id, name) values ('ee000000-0000-0000-0000-000000000001', 'pgtap Team C');
insert into public.profiles (id, role, team_id, full_name, must_change_password) values
  ('e0000000-0000-0000-0000-000000000001', 'team_leader', 'ee000000-0000-0000-0000-000000000001', 'Leader C', false),
  ('e0000000-0000-0000-0000-000000000011', 'agent', 'ee000000-0000-0000-0000-000000000001', 'Agent C1', false),
  ('e0000000-0000-0000-0000-000000000012', 'agent', 'ee000000-0000-0000-0000-000000000001', 'Agent C2', false);
insert into public.workstations (id, team_id, token_hash) values ('ec000000-0000-0000-0000-000000000001', 'ee000000-0000-0000-0000-000000000001', '\x03');
insert into public.face_verifications (agent_id, team_id, auth_session_id, workstation_id, score, liveness_ok, expires_at) values
  ('e0000000-0000-0000-0000-000000000011', 'ee000000-0000-0000-0000-000000000001', 'f1000000-0000-0000-0000-000000000011', 'ec000000-0000-0000-0000-000000000001', 0.8, true, now() + interval '12 hours');
insert into public.scenarios (id, category, title, brief, customer, facts, difficulty, languages, moods) values
  ('tc_scenario', 'TECH_SUPPORT', 'Internet', 'brief text', 'customer text', '{"k":"v"}', 2, '{en,tl,taglish}', '{calm}');
insert into public.customers (id, account_no, full_name, plan, balance) values
  ('c0000000-0000-0000-0000-0000000000c1', 'TC-001', 'Customer TC', 'Plan TC', 100);
insert into public.calls (id, team_id, scenario_id, customer_id, language, mood, status, agent_id) values
  ('ca00000c-0000-0000-0000-000000000001', 'ee000000-0000-0000-0000-000000000001', 'tc_scenario', 'c0000000-0000-0000-0000-0000000000c1', 'en', 'calm', 'ACTIVE', 'e0000000-0000-0000-0000-000000000011'),
  ('ca00000c-0000-0000-0000-000000000002', 'ee000000-0000-0000-0000-000000000001', 'tc_scenario', 'c0000000-0000-0000-0000-0000000000c1', 'en', 'calm', 'WRAP_UP', 'e0000000-0000-0000-0000-000000000011');
insert into public.call_turns (call_id, agent_id, team_id, seq, speaker, text) values
  ('ca00000c-0000-0000-0000-000000000001', 'e0000000-0000-0000-0000-000000000011', 'ee000000-0000-0000-0000-000000000001', 1, 'caller', 'Hello'),
  ('ca00000c-0000-0000-0000-000000000001', 'e0000000-0000-0000-0000-000000000011', 'ee000000-0000-0000-0000-000000000001', 2, 'agent', 'Hi, how can I help');

create function pg_temp.ctx(p_user text, p_session text, p_call text, p_key text default 'tc:key', p_max int default 40, p_full boolean default true)
returns jsonb language sql as $$
  select public.call_turn_context(p_user::uuid, nullif(p_session, '')::uuid, p_call::uuid, p_key, p_max, p_full);
$$;

select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001') ->> 'error', null, 'a valid agent, login and call is accepted');
select is(jsonb_array_length(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001', 'tc:k2') -> 'turns'), 2, 'the transcript comes back in the same call');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001', 'tc:k3') #>> '{turns,1,text}', 'Hi, how can I help', 'turns are in order');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001', 'tc:k4') #>> '{customer,full_name}', 'Customer TC', 'the customer comes back');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001', 'tc:k5') #>> '{scenario,brief}', 'brief text', 'the scenario comes back');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001', 'tc:k6', 40, false) -> 'turns', null, 'without p_full there is no transcript');

select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000099', 'ca00000c-0000-0000-0000-000000000001') ->> 'error', 'face_check_required', 'a face check for a different login does not count');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', '', 'ca00000c-0000-0000-0000-000000000001') ->> 'error', 'unauthenticated', 'a token without a session is refused');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000012', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001') ->> 'error', 'face_check_required', 'another agent cannot borrow a face check');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000001', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001') ->> 'error', 'forbidden', 'a team leader is refused');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000099', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001') ->> 'error', 'forbidden', 'an unknown user is refused');

update public.face_verifications set revoked_at = now() where agent_id = 'e0000000-0000-0000-0000-000000000011';
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001') ->> 'error', 'face_check_required', 'a revoked face check is refused');
update public.face_verifications set revoked_at = null where agent_id = 'e0000000-0000-0000-0000-000000000011';

select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000002') ->> 'error', 'call_not_active', 'a call in wrap-up is not active');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-0000000000ff') ->> 'error', 'call_not_found', 'an unknown call is not found');

update public.profiles set active = false where id = 'e0000000-0000-0000-0000-000000000011';
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001') ->> 'error', 'forbidden', 'a deactivated agent is refused');
update public.profiles set active = true where id = 'e0000000-0000-0000-0000-000000000011';

select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001', 'tc:limit', 2) ->> 'error', null, 'hit 1 of 2 passes');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001', 'tc:limit', 2) ->> 'error', null, 'hit 2 of 2 passes');
select is(pg_temp.ctx('e0000000-0000-0000-0000-000000000011', 'f1000000-0000-0000-0000-000000000011', 'ca00000c-0000-0000-0000-000000000001', 'tc:limit', 2) ->> 'error', 'too_many_requests', 'hit 3 of 2 is limited');

select ok(not has_function_privilege('authenticated', 'public.call_turn_context(uuid, uuid, uuid, text, integer, boolean)', 'EXECUTE'), 'a client cannot run it');
select ok(not has_function_privilege('anon', 'public.call_turn_context(uuid, uuid, uuid, text, integer, boolean)', 'EXECUTE'), 'anon cannot run it');
select ok(has_function_privilege('service_role', 'public.call_turn_context(uuid, uuid, uuid, text, integer, boolean)', 'EXECUTE'), 'the server can run it');

select * from finish();
rollback;
