-- Access-rule tests (pgTAP). Run with:  supabase test db --remote
-- Everything happens in one transaction that is rolled back, so no data is left behind.
-- Each user is simulated by setting the login claims PostgREST would set, then switching role.
--
-- Cast: team A (leader LA, agents A1 and A2), team B (leader LB, agent B1).
--   A1 holds a face verification for login session S1; A2 holds none.

begin;
create extension if not exists pgtap with schema extensions;
select no_plan();

-- ---------------------------------------------------------------------------- fixtures
insert into auth.users (id, email) values
  ('a0000000-0000-0000-0000-000000000001', 'la@example.test'),
  ('a0000000-0000-0000-0000-000000000011', 'a1@example.test'),
  ('a0000000-0000-0000-0000-000000000012', 'a2@example.test'),
  ('b0000000-0000-0000-0000-000000000001', 'lb@example.test'),
  ('b0000000-0000-0000-0000-000000000011', 'b1@example.test');

insert into public.teams (id, name) values
  ('aa000000-0000-0000-0000-000000000001', 'pgtap Team A'),
  ('bb000000-0000-0000-0000-000000000001', 'pgtap Team B');

insert into public.profiles (id, role, team_id, full_name, must_change_password) values
  ('a0000000-0000-0000-0000-000000000001', 'team_leader', 'aa000000-0000-0000-0000-000000000001', 'Leader A', false),
  ('a0000000-0000-0000-0000-000000000011', 'agent',       'aa000000-0000-0000-0000-000000000001', 'Agent A1', true),
  ('a0000000-0000-0000-0000-000000000012', 'agent',       'aa000000-0000-0000-0000-000000000001', 'Agent A2', false),
  ('b0000000-0000-0000-0000-000000000001', 'team_leader', 'bb000000-0000-0000-0000-000000000001', 'Leader B', false),
  ('b0000000-0000-0000-0000-000000000011', 'agent',       'bb000000-0000-0000-0000-000000000001', 'Agent B1', false);

update public.teams set leader_id = 'a0000000-0000-0000-0000-000000000001' where id = 'aa000000-0000-0000-0000-000000000001';
update public.teams set leader_id = 'b0000000-0000-0000-0000-000000000001' where id = 'bb000000-0000-0000-0000-000000000001';

insert into public.workstations (id, team_id, token_hash) values
  ('ac000000-0000-0000-0000-000000000001', 'aa000000-0000-0000-0000-000000000001', '\x01'),
  ('bc000000-0000-0000-0000-000000000001', 'bb000000-0000-0000-0000-000000000001', '\x02');

insert into public.face_templates (agent_id, embeddings_enc, model_name, model_version)
  values ('a0000000-0000-0000-0000-000000000011', '\x00', 'm', 'v');
insert into public.calibration_profiles (agent_id, team_id, ear_open, ear_closed, mar_closed) values
  ('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 0.30, 0.10, 0.03),
  ('b0000000-0000-0000-0000-000000000011', 'bb000000-0000-0000-0000-000000000001', 0.31, 0.11, 0.03);
insert into public.setup_codes (kind, team_id, agent_id, code_hash, created_by, expires_at) values
  ('enrollment', 'aa000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000011', '\x0a',
   'a0000000-0000-0000-0000-000000000001', now() + interval '15 minutes');
insert into public.face_challenges (agent_id, team_id, workstation_id, auth_session_id, nonce_hash, actions, expires_at) values
  ('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001',
   'ac000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000001', '\x0b',
   '{BLINK_TWICE,TURN_LEFT}', now() + interval '60 seconds');

-- A1's face check for login session S1 (c1...01)
insert into public.face_verifications (id, agent_id, team_id, auth_session_id, workstation_id, score, liveness_ok, expires_at) values
  ('d1000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000011',
   'aa000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000001',
   'ac000000-0000-0000-0000-000000000001', 0.81, true, now() + interval '12 hours');

-- ---------------------------------------------------------------------------- reading: agents
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000011","role":"authenticated","session_id":"c1000000-0000-0000-0000-000000000001"}', true);
set local role authenticated;

select is((select count(*) from public.profiles)::int, 1, 'an agent sees only their own profile');
select is((select count(*) from public.teams)::int, 1, 'an agent sees only their own team');
select is((select count(*) from public.calibration_profiles)::int, 1, 'an agent sees only their own calibration');
select is((select count(*) from public.workstations)::int, 0, 'an agent cannot list workstations');

-- ---------------------------------------------------------------------------- nobody reads secrets
select throws_ok($$select * from public.face_templates$$, '42501', null, 'no client can read face templates');
select throws_ok($$select * from public.setup_codes$$, '42501', null, 'no client can read setup codes');
select throws_ok($$select * from public.face_challenges$$, '42501', null, 'no client can read face challenges (nonces)');
select throws_ok($$update public.profiles set role = 'team_leader' where id = 'a0000000-0000-0000-0000-000000000011'$$,
                 '42501', null, 'an agent cannot promote themselves');
select throws_ok($$update public.profiles set team_id = 'bb000000-0000-0000-0000-000000000001' where id = 'a0000000-0000-0000-0000-000000000011'$$,
                 '42501', null, 'an agent cannot move themselves to another team');
select throws_ok($$insert into public.teams (name) values ('rogue')$$, '42501', null, 'clients cannot create teams');
select throws_ok($$insert into public.alerts (team_id, agent_id, kind, message) values ('aa000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000011', 'FACE_LOCKOUT', 'x')$$,
                 '42501', null, 'clients cannot create alerts');
select throws_ok($$insert into public.face_verifications (agent_id, team_id, auth_session_id, workstation_id, score, liveness_ok, expires_at) values ('a0000000-0000-0000-0000-000000000011', 'aa000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000001', 'ac000000-0000-0000-0000-000000000001', 1, true, now() + interval '1 day')$$,
                 '42501', null, 'a client cannot give itself a face verification');
reset role;

-- anon can read nothing
set local role anon;
select throws_ok($$select * from public.profiles$$, '42501', null, 'anon cannot read profiles');
reset role;

-- ---------------------------------------------------------------------------- reading: leaders
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000001","role":"authenticated","session_id":"c9000000-0000-0000-0000-000000000009"}', true);
set local role authenticated;
select is((select count(*) from public.profiles)::int, 3, 'leader A sees their 3 team members');
select is((select count(*) from public.profiles where team_id = 'bb000000-0000-0000-0000-000000000001')::int, 0, 'leader A sees nothing of team B');
select is((select count(*) from public.workstations)::int, 1, 'leader A sees their own workstation');
select throws_ok($$select token_hash from public.workstations$$, '42501', null, 'not even a leader can read a token hash');
select is((select count(*) from public.calibration_profiles)::int, 1, 'leader A sees their team calibration only');
select is((select count(*) from public.face_verifications)::int, 1, 'leader A sees their team face-check history');
reset role;

select set_config('request.jwt.claims', '{"sub":"b0000000-0000-0000-0000-000000000001","role":"authenticated","session_id":"c9000000-0000-0000-0000-000000000008"}', true);
set local role authenticated;
select is((select count(*) from public.profiles)::int, 2, 'leader B sees only their 2 team members');
select is((select count(*) from public.face_verifications)::int, 0, 'leader B sees no face checks of team A');
reset role;

-- ---------------------------------------------------------------------------- the face gate
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000011","role":"authenticated","session_id":"c1000000-0000-0000-0000-000000000001"}', true);
set local role authenticated;
select ok(private.face_mfa_ok(), 'A1 passes the gate in the session that was face-checked');
reset role;

select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000011","role":"authenticated","session_id":"c2000000-0000-0000-0000-000000000002"}', true);
set local role authenticated;
select ok(not private.face_mfa_ok(), 'the same password login in a different session does not pass');
select throws_ok($$insert into public.sessions (workstation_id) values ('ac000000-0000-0000-0000-000000000001')$$,
                 '42501', null, 'no face check for this session: cannot start a session');
reset role;

select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000012","role":"authenticated","session_id":"c1000000-0000-0000-0000-000000000001"}', true);
set local role authenticated;
select ok(not private.face_mfa_ok(), 'A2 has no face check, so a password alone is not enough');
select throws_ok($$insert into public.sessions (workstation_id) values ('ac000000-0000-0000-0000-000000000001')$$,
                 '42501', null, 'A2 cannot start a session without a face check');
reset role;

-- ---------------------------------------------------------------------------- writing as A1
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000011","role":"authenticated","session_id":"c1000000-0000-0000-0000-000000000001"}', true);
set local role authenticated;
select throws_ok($$insert into public.sessions (agent_id, workstation_id) values ('a0000000-0000-0000-0000-000000000012', 'ac000000-0000-0000-0000-000000000001')$$,
                 '42501', null, 'a client cannot choose the agent_id of a session');
select throws_ok($$insert into public.sessions (workstation_id) values ('bc000000-0000-0000-0000-000000000001')$$,
                 '42501', null, 'a session cannot use another team''s workstation');
select lives_ok($$insert into public.sessions (id, workstation_id) values ('e1000000-0000-0000-0000-000000000001', 'ac000000-0000-0000-0000-000000000001')$$,
                'A1 with a valid face check can start a session');
select lives_ok($$insert into public.events (session_id, type, ts, duration_ms, severity) values ('e1000000-0000-0000-0000-000000000001', 'YAWN', now(), 2100, 'warning')$$,
                'A1 can record an event');
select lives_ok($$insert into public.metrics_minute (session_id, minute, perclos, yawns) values ('e1000000-0000-0000-0000-000000000001', now(), 0.05, 1)$$,
                'A1 can record minute metrics');
select throws_ok($$insert into public.events (session_id, type, ts) values ('e1000000-0000-0000-0000-000000000001', 'YAWN', now() - interval '10 days')$$,
                 '22008', null, 'an event older than 7 days is rejected');
select throws_ok($$insert into public.events (session_id, type, ts) values ('e1000000-0000-0000-0000-000000000001', 'YAWN', now() + interval '1 hour')$$,
                 '22008', null, 'an event from the future is rejected');
select throws_ok($$insert into public.events (session_id, agent_id, team_id, type, ts) values ('e1000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000012', 'aa000000-0000-0000-0000-000000000001', 'YAWN', now())$$,
                 '42501', null, 'a client cannot choose agent_id or team_id on an event');
reset role;

select is((select agent_id from public.sessions where id = 'e1000000-0000-0000-0000-000000000001'),
          'a0000000-0000-0000-0000-000000000011'::uuid, 'the session belongs to A1 (filled from the login)');
select is((select team_id from public.events where session_id = 'e1000000-0000-0000-0000-000000000001' limit 1),
          'aa000000-0000-0000-0000-000000000001'::uuid, 'the event team_id was filled in by the server');
select is((select auth_session_id from public.sessions where id = 'e1000000-0000-0000-0000-000000000001'),
          'c1000000-0000-0000-0000-000000000001'::uuid, 'the session is bound to the login session');

-- A2 (no face check) and B1 cannot write into A1's session
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000012","role":"authenticated","session_id":"c1000000-0000-0000-0000-000000000001"}', true);
set local role authenticated;
select throws_ok($$insert into public.events (session_id, type, ts) values ('e1000000-0000-0000-0000-000000000001', 'YAWN', now())$$,
                 '42501', null, 'another agent cannot write into A1''s session');
reset role;

-- ---------------------------------------------------------------------------- leaders read events
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000001","role":"authenticated","session_id":"c9000000-0000-0000-0000-000000000009"}', true);
set local role authenticated;
select is((select count(*) from public.events)::int, 1, 'leader A sees their team''s events');
reset role;
select set_config('request.jwt.claims', '{"sub":"b0000000-0000-0000-0000-000000000001","role":"authenticated","session_id":"c9000000-0000-0000-0000-000000000008"}', true);
set local role authenticated;
select is((select count(*) from public.events)::int, 0, 'leader B sees no events of team A');
select is((select count(*) from public.sessions)::int, 0, 'leader B sees no sessions of team A');
reset role;

-- ---------------------------------------------------------------------------- escalation
-- two microsleeps in 10 minutes -> one alert, not two
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000011","role":"authenticated","session_id":"c1000000-0000-0000-0000-000000000001"}', true);
set local role authenticated;
select lives_ok($$insert into public.events (session_id, type, ts, duration_ms, severity) values ('e1000000-0000-0000-0000-000000000001', 'MICROSLEEP', now() - interval '3 minutes', 650, 'critical')$$, 'first microsleep');
reset role;
select is((select count(*) from public.alerts where kind = 'REPEATED_MICROSLEEP')::int, 0, 'one microsleep raises no alert');
set local role authenticated;
select lives_ok($$insert into public.events (session_id, type, ts, duration_ms, severity) values ('e1000000-0000-0000-0000-000000000001', 'MICROSLEEP', now() - interval '1 minute', 700, 'critical')$$, 'second microsleep');
select lives_ok($$insert into public.events (session_id, type, ts, duration_ms, severity) values ('e1000000-0000-0000-0000-000000000001', 'MICROSLEEP', now(), 800, 'critical')$$, 'third microsleep');
reset role;
select is((select count(*) from public.alerts where kind = 'REPEATED_MICROSLEEP')::int, 1, 'repeated microsleeps raise exactly one alert');

-- an identity event revokes the face check, closes the session and alerts the leader
set local role authenticated;
select lives_ok($$insert into public.events (session_id, type, ts, duration_ms, severity) values ('e1000000-0000-0000-0000-000000000001', 'IDENTITY_MISMATCH', now(), 5200, 'critical')$$, 'A1 reports IDENTITY_MISMATCH');
reset role;
select ok((select revoked_at is not null from public.face_verifications where id = 'd1000000-0000-0000-0000-000000000001'), 'the face verification was revoked');
select ok((select ended_at is not null from public.sessions where id = 'e1000000-0000-0000-0000-000000000001'), 'the session was closed');
select is((select count(*) from public.alerts where kind = 'IDENTITY_MISMATCH')::int, 1, 'an IDENTITY_MISMATCH alert exists');

select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000011","role":"authenticated","session_id":"c1000000-0000-0000-0000-000000000001"}', true);
set local role authenticated;
select ok(not private.face_mfa_ok(), 'after the revocation the same login no longer passes the gate');
select throws_ok($$insert into public.events (session_id, type, ts) values ('e1000000-0000-0000-0000-000000000001', 'YAWN', now())$$,
                 '42501', null, 'a revoked client cannot write any more');
reset role;

-- leaders and alerts
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000001","role":"authenticated","session_id":"c9000000-0000-0000-0000-000000000009"}', true);
set local role authenticated;
select is((select count(*) from public.alerts)::int, 2, 'leader A sees both alerts');
select lives_ok($$select public.ack_alert((select id from public.alerts where kind = 'IDENTITY_MISMATCH'))$$, 'leader A acknowledges an alert');
reset role;
select ok((select acknowledged_at is not null from public.alerts where kind = 'IDENTITY_MISMATCH'), 'the acknowledgement is recorded');

select set_config('request.jwt.claims', '{"sub":"b0000000-0000-0000-0000-000000000001","role":"authenticated","session_id":"c9000000-0000-0000-0000-000000000008"}', true);
set local role authenticated;
select is((select count(*) from public.alerts)::int, 0, 'leader B sees no alerts of team A');
select throws_ok($$select public.ack_alert((select id from public.alerts limit 1))$$, null, null, 'leader B cannot acknowledge a team A alert');
reset role;
select throws_ok($$select public.ack_alert('00000000-0000-0000-0000-000000000000')$$, null, null, 'acknowledging a missing alert fails (as postgres: no leader)');

-- ---------------------------------------------------------------------------- deactivation
insert into public.face_verifications (id, agent_id, team_id, auth_session_id, workstation_id, score, liveness_ok, expires_at) values
  ('d1000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000011',
   'aa000000-0000-0000-0000-000000000001', 'c3000000-0000-0000-0000-000000000003',
   'ac000000-0000-0000-0000-000000000001', 0.8, true, now() + interval '12 hours');
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000011","role":"authenticated","session_id":"c3000000-0000-0000-0000-000000000003"}', true);
set local role authenticated;
select ok(private.face_mfa_ok(), 'a fresh face check passes the gate');
reset role;
update public.profiles set active = false where id = 'a0000000-0000-0000-0000-000000000011';
set local role authenticated;
select ok(not private.face_mfa_ok(), 'a deactivated agent fails the gate even with a valid face check');
reset role;
update public.profiles set active = true where id = 'a0000000-0000-0000-0000-000000000011';
update public.face_verifications set expires_at = now() - interval '1 second' where id = 'd1000000-0000-0000-0000-000000000002';
set local role authenticated;
select ok(not private.face_mfa_ok(), 'an expired face check fails the gate');
reset role;

-- ---------------------------------------------------------------------------- small RPCs
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-0000-0000-000000000011","role":"authenticated","session_id":"c3000000-0000-0000-0000-000000000003"}', true);
set local role authenticated;
select lives_ok($$select public.ack_password_changed()$$, 'an agent can mark their password as changed');
select throws_ok($$select public.rate_limit_hit('x', 1, '1 minute')$$, '42501', null, 'clients cannot call the rate limiter');
reset role;
select is((select must_change_password from public.profiles where id = 'a0000000-0000-0000-0000-000000000011'), false, 'the password gate opened');

set local role service_role;
select ok(public.rate_limit_hit('k', 2, '1 minute'), 'rate limit: first call allowed');
select ok(public.rate_limit_hit('k', 2, '1 minute'), 'rate limit: second call allowed');
select ok(not public.rate_limit_hit('k', 2, '1 minute'), 'rate limit: third call refused');
select ok(public.rate_limit_hit('other', 2, '1 minute'), 'rate limit: keys are independent');
reset role;

select * from finish();
rollback;
