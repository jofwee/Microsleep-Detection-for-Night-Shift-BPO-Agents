-- The simulated call center (part 2): agent presence, the call state machine, dispatch,
-- simulation runs and the scheduled jobs.
--
-- Clients (the agent's PC) only call the RPCs below: set_status, heartbeat, accept_call,
-- decline_call, end_call, finish_wrap_up, lookup_customer. The state machine is enforced here,
-- in SQL, so a modified client cannot jump to a state it is not allowed to reach.
-- Team leaders start and stop simulation runs with start_run / stop_run.
--
-- An agent receives calls only while: status AVAILABLE, monitor LIVE (the camera is on), identity
-- confirmed, no microsleep, a heartbeat from the last 15 s, and a valid face check.

create type public.agent_state as enum (
  'OFFLINE', 'NOT_READY', 'AVAILABLE', 'RINGING', 'ON_CALL', 'WRAP_UP', 'SYSTEM_HOLD'
);

create table public.agent_status (
  agent_id           uuid primary key references auth.users (id) on delete cascade,
  team_id            uuid not null references public.teams (id) on delete cascade,
  status             public.agent_state not null default 'OFFLINE',
  reason             text,
  status_since       timestamptz not null default now(),
  monitor_state      text not null default 'OFF' check (monitor_state in ('OFF', 'LIVE')),
  identity_ok        boolean not null default false,
  fatigue_state      text not null default 'ALERT' check (fatigue_state in ('ALERT', 'DROWSY', 'MICROSLEEP')),
  last_heartbeat_at  timestamptz,
  current_call_id    uuid references public.calls (id) on delete set null,
  missed_offers      integer not null default 0,
  hold_pending       text   -- a problem seen during a call: the agent goes to SYSTEM_HOLD after wrap-up
);
create index agent_status_team_idx on public.agent_status (team_id, status);

create table public.agent_status_log (
  id        bigint generated always as identity primary key,
  agent_id  uuid not null references auth.users (id) on delete cascade,
  team_id   uuid not null references public.teams (id) on delete cascade,
  status    public.agent_state not null,
  reason    text,
  from_ts   timestamptz not null default now(),
  to_ts     timestamptz
);
create index agent_status_log_agent_idx on public.agent_status_log (agent_id, from_ts desc);

-- ------------------------------------------------------------------------------ helpers
create function private.ensure_agent_row(p_agent uuid)
returns void
language sql security definer set search_path = ''
as $$
  insert into public.agent_status (agent_id, team_id)
  select p.id, p.team_id from public.profiles p where p.id = p_agent and p.role = 'agent' and p.active
  on conflict (agent_id) do nothing;
$$;

-- Every status change goes through here, so the log is complete.
create function private.set_agent_status(
  p_agent uuid, p_status public.agent_state, p_reason text default null, p_call uuid default null
)
returns void
language plpgsql security definer set search_path = ''
as $$
begin
  update public.agent_status_log set to_ts = now() where agent_id = p_agent and to_ts is null;
  insert into public.agent_status_log (agent_id, team_id, status, reason)
  select p_agent, s.team_id, p_status, p_reason from public.agent_status s where s.agent_id = p_agent;
  update public.agent_status
     set status = p_status, reason = p_reason, status_since = now(), current_call_id = p_call
   where agent_id = p_agent;
end;
$$;

-- A valid (unrevoked, unexpired) face check in any login session.
create function private.valid_face_check(p_agent uuid)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1 from public.face_verifications v
    where v.agent_id = p_agent and v.revoked_at is null and v.expires_at > now()
  );
$$;

-- Give an unanswered or declined call back to the queue.
create function private.release_offer(p_call uuid, p_agent uuid, p_outcome text)
returns void
language plpgsql security definer set search_path = ''
as $$
begin
  update public.call_offers
     set responded_at = now(), outcome = p_outcome
   where call_id = p_call and agent_id = p_agent and outcome is null;
  update public.calls
     set status = 'QUEUED', agent_id = null, offered_at = null, offer_expires_at = null
   where id = p_call and status = 'OFFERED';
end;
$$;

-- ------------------------------------------------------------------------------ dispatch
-- Oldest waiting call first; each goes to the longest-idle eligible agent who has not already
-- been offered that call. Calls nobody can take are skipped (no head-of-line blocking).
create function private.dispatch_calls(p_team uuid)
returns integer
language plpgsql security definer set search_path = ''
as $$
declare
  v_call    record;
  v_agent   uuid;
  v_offered integer := 0;
begin
  for v_call in
    select c.id from public.calls c
     where c.team_id = p_team and c.status = 'QUEUED'
     order by c.queued_at
       for update skip locked
  loop
    select s.agent_id into v_agent
      from public.agent_status s
     where s.team_id = p_team
       and s.status = 'AVAILABLE'
       and s.monitor_state = 'LIVE'
       and s.identity_ok
       and s.fatigue_state <> 'MICROSLEEP'
       and s.last_heartbeat_at > now() - interval '15 seconds'
       and private.valid_face_check(s.agent_id)
       and not exists (select 1 from public.call_offers o where o.call_id = v_call.id and o.agent_id = s.agent_id)
     order by s.status_since
     limit 1
       for update of s skip locked;
    if v_agent is null then
      continue;
    end if;
    update public.calls
       set status = 'OFFERED', agent_id = v_agent, offered_at = now(),
           offer_expires_at = now() + interval '20 seconds', offer_attempts = offer_attempts + 1
     where id = v_call.id;
    insert into public.call_offers (call_id, agent_id, team_id) values (v_call.id, v_agent, p_team);
    perform private.set_agent_status(v_agent, 'RINGING', null, v_call.id);
    v_offered := v_offered + 1;
  end loop;
  return v_offered;
end;
$$;

-- ------------------------------------------------------------------------------ agent RPCs
create function public.set_status(p_status public.agent_state, p_reason text default null)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v_agent uuid := (select auth.uid());
  v_s     public.agent_status%rowtype;
begin
  if not private.is_active_agent() then
    raise exception 'not an active agent' using errcode = '42501';
  end if;
  perform private.ensure_agent_row(v_agent);
  select * into v_s from public.agent_status where agent_id = v_agent for update;
  if p_status = v_s.status then
    return;
  end if;
  if v_s.status in ('RINGING', 'ON_CALL', 'WRAP_UP') then
    raise exception 'cannot change status during a call (finish the call first)' using errcode = '55000';
  end if;
  if p_status in ('RINGING', 'ON_CALL', 'WRAP_UP', 'SYSTEM_HOLD') then
    raise exception 'that status is set by the system' using errcode = '55000';
  end if;
  if p_status = 'OFFLINE' then            -- signing out is always possible between calls
    perform private.set_agent_status(v_agent, 'OFFLINE', p_reason);
    return;
  end if;
  if not private.face_mfa_ok() then
    raise exception 'face check required' using errcode = '42501';
  end if;
  if v_s.status = 'OFFLINE' and p_status <> 'NOT_READY' then
    raise exception 'sign in first (NOT_READY), then go available' using errcode = '55000';
  end if;
  if p_status = 'AVAILABLE' then
    if v_s.monitor_state <> 'LIVE' or not v_s.identity_ok then
      raise exception 'the camera monitor must be live and identity confirmed' using errcode = '55000';
    end if;
    if v_s.fatigue_state = 'MICROSLEEP' then
      raise exception 'not while a microsleep is flagged' using errcode = '55000';
    end if;
    update public.agent_status set missed_offers = 0, hold_pending = null where agent_id = v_agent;
  end if;
  perform private.set_agent_status(v_agent, p_status, p_reason);
  if p_status = 'AVAILABLE' then
    perform private.dispatch_calls(v_s.team_id);
  end if;
end;
$$;

-- Called every couple of seconds by the monitoring app. Returns what the app needs to react.
create function public.heartbeat(p_monitor text, p_identity_ok boolean, p_fatigue text)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_agent   uuid := (select auth.uid());
  v_s       public.agent_status%rowtype;
  v_problem text;
begin
  if not private.is_active_agent() then
    raise exception 'not an active agent' using errcode = '42501';
  end if;
  if not private.face_mfa_ok() then
    raise exception 'face check required' using errcode = '42501';
  end if;
  if p_monitor not in ('OFF', 'LIVE') or p_fatigue not in ('ALERT', 'DROWSY', 'MICROSLEEP') or p_identity_ok is null then
    raise exception 'invalid heartbeat' using errcode = '22023';
  end if;
  perform private.ensure_agent_row(v_agent);
  select * into v_s from public.agent_status where agent_id = v_agent for update;
  update public.agent_status
     set monitor_state = p_monitor, identity_ok = p_identity_ok, fatigue_state = p_fatigue, last_heartbeat_at = now()
   where agent_id = v_agent;

  -- a problem while the camera should be watching
  if v_s.status in ('AVAILABLE', 'RINGING', 'ON_CALL', 'WRAP_UP') then
    v_problem := case
      when not p_identity_ok then 'identity not confirmed'
      when p_fatigue = 'MICROSLEEP' then 'microsleep'
      when p_monitor <> 'LIVE' then 'monitor not live'
    end;
    if v_problem is not null then
      if v_s.status in ('AVAILABLE', 'RINGING') then
        if v_s.status = 'RINGING' then
          perform private.release_offer(v_s.current_call_id, v_agent, 'declined');
        end if;
        perform private.set_agent_status(v_agent, 'SYSTEM_HOLD', v_problem);
      else  -- do not drop the caller: hold the agent after wrap-up
        update public.agent_status set hold_pending = v_problem where agent_id = v_agent;
      end if;
    end if;
  end if;

  perform private.dispatch_calls(v_s.team_id);
  return (
    select jsonb_build_object('status', s.status, 'reason', s.reason, 'call_id', s.current_call_id,
                              'offer_expires_at', c.offer_expires_at)
    from public.agent_status s left join public.calls c on c.id = s.current_call_id
    where s.agent_id = v_agent
  );
end;
$$;

create function public.accept_call(p_call uuid)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v_agent uuid := (select auth.uid());
  v_c     public.calls%rowtype;
begin
  if not private.is_active_agent() or not private.face_mfa_ok() then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  select * into v_c from public.calls where id = p_call for update;
  if not found or v_c.status <> 'OFFERED' or v_c.agent_id is distinct from v_agent then
    raise exception 'that call is not being offered to you' using errcode = '55000';
  end if;
  if v_c.offer_expires_at < now() then
    raise exception 'the offer has expired' using errcode = '55000';
  end if;
  update public.calls set status = 'ACTIVE', answered_at = now(), offer_expires_at = null where id = p_call;
  update public.call_offers set responded_at = now(), outcome = 'accepted'
   where call_id = p_call and agent_id = v_agent and outcome is null;
  update public.agent_status set missed_offers = 0 where agent_id = v_agent;
  perform private.set_agent_status(v_agent, 'ON_CALL', null, p_call);
end;
$$;

create function public.decline_call(p_call uuid)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v_agent uuid := (select auth.uid());
  v_c     public.calls%rowtype;
begin
  if not private.is_active_agent() or not private.face_mfa_ok() then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  select * into v_c from public.calls where id = p_call for update;
  if not found or v_c.status <> 'OFFERED' or v_c.agent_id is distinct from v_agent then
    raise exception 'that call is not being offered to you' using errcode = '55000';
  end if;
  perform private.release_offer(p_call, v_agent, 'declined');
  perform private.set_agent_status(v_agent, 'AVAILABLE');
  perform private.dispatch_calls(v_c.team_id);
end;
$$;

create function public.end_call(p_call uuid)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v_agent uuid := (select auth.uid());
  v_c     public.calls%rowtype;
begin
  if not private.is_active_agent() or not private.face_mfa_ok() then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  select * into v_c from public.calls where id = p_call for update;
  if not found or v_c.status <> 'ACTIVE' or v_c.agent_id is distinct from v_agent then
    raise exception 'you are not on that call' using errcode = '55000';
  end if;
  update public.calls set status = 'WRAP_UP', ended_at = now(), end_reason = coalesce(end_reason, 'agent_ended')
   where id = p_call;
  perform private.set_agent_status(v_agent, 'WRAP_UP', null, p_call);
end;
$$;

-- The caller ended the call (satisfied, hung up, asked for a supervisor). Only the
-- caller-turn Edge Function (secret key) calls this.
create function public.system_end_call(p_call uuid, p_reason text)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v_c public.calls%rowtype;
begin
  if p_reason not in ('caller_satisfied', 'caller_hangup', 'caller_escalated') then
    raise exception 'invalid reason' using errcode = '22023';
  end if;
  select * into v_c from public.calls where id = p_call for update;
  if not found or v_c.status <> 'ACTIVE' then
    return;  -- already ended: nothing to do
  end if;
  update public.calls set status = 'WRAP_UP', ended_at = now(), end_reason = p_reason where id = p_call;
  perform private.set_agent_status(v_c.agent_id, 'WRAP_UP', null, p_call);
end;
$$;

create function public.finish_wrap_up(p_call uuid, p_disposition text, p_notes text default null)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v_agent uuid := (select auth.uid());
  v_c     public.calls%rowtype;
  v_s     public.agent_status%rowtype;
begin
  if not private.is_active_agent() or not private.face_mfa_ok() then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  if p_disposition not in ('resolved', 'unresolved', 'escalated') then
    raise exception 'disposition must be resolved, unresolved or escalated' using errcode = '22023';
  end if;
  select * into v_c from public.calls where id = p_call for update;
  if not found or v_c.status <> 'WRAP_UP' or v_c.agent_id is distinct from v_agent then
    raise exception 'that call is not in wrap-up for you' using errcode = '55000';
  end if;
  update public.calls
     set status = 'COMPLETED', disposition = p_disposition, notes = left(p_notes, 2000), wrap_up_ended_at = now()
   where id = p_call;
  select * into v_s from public.agent_status where agent_id = v_agent for update;
  if v_s.hold_pending is not null then
    perform private.set_agent_status(v_agent, 'SYSTEM_HOLD', v_s.hold_pending);
  elsif v_s.monitor_state = 'LIVE' and v_s.identity_ok and v_s.fatigue_state <> 'MICROSLEEP' then
    perform private.set_agent_status(v_agent, 'AVAILABLE');
  else
    perform private.set_agent_status(v_agent, 'SYSTEM_HOLD', 'monitor not live');
  end if;
  update public.agent_status set hold_pending = null where agent_id = v_agent;
  perform private.dispatch_calls(v_c.team_id);
end;
$$;

-- The agent's customer search. Works only during a call, and only for the fictional customers.
create function public.lookup_customer(p_account text)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_agent uuid := (select auth.uid());
  v_row   jsonb;
begin
  if not private.is_active_agent() or not private.face_mfa_ok() then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  if not exists (select 1 from public.agent_status s where s.agent_id = v_agent and s.status in ('ON_CALL', 'WRAP_UP')) then
    raise exception 'customer lookup is available during a call' using errcode = '55000';
  end if;
  select to_jsonb(c) - 'id' into v_row
    from public.customers c
   where upper(c.account_no) = upper(btrim(p_account));
  return v_row;
end;
$$;

-- ------------------------------------------------------------------------------ simulation
-- Deterministic "random": the same seed and counter always give the same number in [0, 1).
create function private.rand01(p_seed bigint, p_k bigint, p_salt integer)
returns double precision
language sql immutable
as $$
  select (abs(hashtextextended(p_seed::text || ':' || p_k::text || ':' || p_salt::text, 0)) % 1000000)::double precision / 1000000.0;
$$;

create function private.pick_language(p_mix jsonb, p_u double precision)
returns public.call_language
language plpgsql immutable
as $$
declare
  v_en double precision := coalesce((p_mix ->> 'en')::double precision, 0);
  v_tg double precision := coalesce((p_mix ->> 'taglish')::double precision, 0);
  v_tl double precision := coalesce((p_mix ->> 'tl')::double precision, 0);
  v_total double precision := v_en + v_tg + v_tl;
  v_x double precision := p_u * v_total;
begin
  if v_x < v_en then
    return 'en';
  elsif v_x < v_en + v_tg then
    return 'taglish';
  end if;
  return 'tl';
end;
$$;

-- Team leader: start a run. Config (all optional): arrival_per_min, max_concurrent, patience_s,
-- language_mix {"en":40,"taglish":40,"tl":20}, moods ["calm", ...], scenario_ids ["internet_down", ...].
create function public.start_run(p_config jsonb default '{}'::jsonb, p_seed bigint default null)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
  v_leader uuid := (select auth.uid());
  v_team   uuid;
  v_cfg    jsonb;
  v_id     uuid;
begin
  select p.team_id into v_team from public.profiles p
   where p.id = v_leader and p.role = 'team_leader' and p.active;
  if v_team is null then
    raise exception 'only a team leader can start a run' using errcode = '42501';
  end if;
  v_cfg := jsonb_build_object(
             'arrival_per_min', 4, 'max_concurrent', 10, 'patience_s', 90,
             'language_mix', jsonb_build_object('en', 40, 'taglish', 40, 'tl', 20)
           ) || coalesce(p_config, '{}'::jsonb);
  if (v_cfg ->> 'arrival_per_min')::numeric not between 0.5 and 30
     or (v_cfg ->> 'max_concurrent')::integer not between 1 and 50
     or (v_cfg ->> 'patience_s')::integer not between 10 and 600 then
    raise exception 'arrival_per_min must be 0.5-30, max_concurrent 1-50, patience_s 10-600' using errcode = '22023';
  end if;
  if exists (select 1 from jsonb_each_text(v_cfg -> 'language_mix') w where w.key not in ('en', 'tl', 'taglish') or w.value::numeric < 0)
     or (select coalesce(sum(w.value::numeric), 0) from jsonb_each_text(v_cfg -> 'language_mix') w) <= 0 then
    raise exception 'language_mix needs non-negative weights for en, tl and taglish, adding up to more than 0' using errcode = '22023';
  end if;
  if exists (select 1 from public.simulation_runs r where r.team_id = v_team and r.status = 'RUNNING') then
    raise exception 'your team already has a running simulation' using errcode = '55000';
  end if;
  insert into public.simulation_runs (team_id, started_by, seed, config)
  values (v_team, v_leader, coalesce(p_seed, (random() * 9e15)::bigint), v_cfg)
  returning id into v_id;
  insert into public.audit_log (team_id, actor_id, action, target_id, detail)
  values (v_team, v_leader, 'simulation_started', v_id, v_cfg);
  return v_id;
end;
$$;

create function public.stop_run(p_run uuid)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v_team uuid;
begin
  select r.team_id into v_team from public.simulation_runs r where r.id = p_run and r.status = 'RUNNING' for update;
  if v_team is null or not private.leads_team(v_team) then
    raise exception 'no such running simulation in your team' using errcode = '42501';
  end if;
  update public.simulation_runs set status = 'STOPPED', ended_at = now() where id = p_run;
  update public.calls set status = 'ABANDONED', ended_at = now(), end_reason = 'run_stopped'
   where run_id = p_run and status = 'QUEUED';
  insert into public.audit_log (team_id, actor_id, action, target_id)
  values (v_team, (select auth.uid()), 'simulation_stopped', p_run);
end;
$$;

-- Runs every 10 s (pg_cron). Creates the calls that arrived since the last tick.
create function private.sim_tick()
returns integer
language plpgsql security definer set search_path = ''
as $$
declare
  r        public.simulation_runs%rowtype;
  v_dt     double precision;
  v_carry  numeric;
  v_n      integer;
  v_active integer;
  v_made   integer;
  v_total  integer := 0;
  v_k      bigint;
  v_lang   public.call_language;
  v_scen   record;
  v_cust   uuid;
  v_moods  text[];
  v_count  integer;
begin
  for r in select * from public.simulation_runs where status = 'RUNNING' for update skip locked loop
    v_dt := least(coalesce(extract(epoch from (now() - r.last_tick_at)), 10), 30);
    v_carry := r.arrival_carry + (r.config ->> 'arrival_per_min')::numeric * v_dt / 60.0;
    v_n := floor(v_carry);
    v_carry := v_carry - v_n;
    select count(*) into v_active from public.calls
     where team_id = r.team_id and status in ('QUEUED', 'OFFERED', 'ACTIVE', 'WRAP_UP');
    v_n := least(v_n, greatest((r.config ->> 'max_concurrent')::integer - v_active, 0));
    v_made := 0;
    while v_made < v_n loop
      v_k := r.calls_created + v_made + 1;
      v_lang := private.pick_language(r.config -> 'language_mix', private.rand01(r.seed, v_k, 1));
      select count(*) into v_count from public.scenarios s
       where s.active and v_lang = any (s.languages)
         and (not (r.config ? 'scenario_ids') or s.id in (select jsonb_array_elements_text(r.config -> 'scenario_ids')));
      exit when v_count = 0;
      select s.* into v_scen from public.scenarios s
       where s.active and v_lang = any (s.languages)
         and (not (r.config ? 'scenario_ids') or s.id in (select jsonb_array_elements_text(r.config -> 'scenario_ids')))
       order by s.id
      offset floor(private.rand01(r.seed, v_k, 2) * v_count)::integer limit 1;
      v_moods := v_scen.moods;
      if r.config ? 'moods' then
        v_moods := coalesce(nullif(array(select m from unnest(v_scen.moods) m where m in (select jsonb_array_elements_text(r.config -> 'moods'))), '{}'), v_scen.moods);
      end if;
      select count(*) into v_count from public.customers;
      exit when v_count = 0;
      select c.id into v_cust from public.customers c order by c.account_no
      offset floor(private.rand01(r.seed, v_k, 4) * v_count)::integer limit 1;
      insert into public.calls (run_id, team_id, scenario_id, customer_id, language, mood, patience_s)
      values (r.id, r.team_id, v_scen.id, v_cust, v_lang,
              v_moods[1 + floor(private.rand01(r.seed, v_k, 3) * cardinality(v_moods))::integer],
              (r.config ->> 'patience_s')::integer);
      v_made := v_made + 1;
    end loop;
    update public.simulation_runs
       set last_tick_at = now(), calls_created = calls_created + v_made, arrival_carry = v_carry
     where id = r.id;
    v_total := v_total + v_made;
    perform private.dispatch_calls(r.team_id);
  end loop;
  return v_total;
end;
$$;

-- ------------------------------------------------------------------------------ housekeeping
-- Runs every 5 s (pg_cron): unanswered offers, impatient callers, silent agents, lost face
-- checks, and offering whatever is waiting.
create function private.sweep_calls()
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  r record;
begin
  -- 1. offers nobody answered in time
  for r in select c.id as call_id, c.agent_id from public.calls c
            where c.status = 'OFFERED' and c.offer_expires_at < now() for update skip locked
  loop
    perform private.release_offer(r.call_id, r.agent_id, 'timeout');
    update public.agent_status set missed_offers = missed_offers + 1 where agent_id = r.agent_id;
    if exists (select 1 from public.agent_status s where s.agent_id = r.agent_id and s.status = 'RINGING' and s.current_call_id = r.call_id) then
      if (select s.missed_offers from public.agent_status s where s.agent_id = r.agent_id) >= 2 then
        perform private.set_agent_status(r.agent_id, 'SYSTEM_HOLD', 'missed offers');
      else
        perform private.set_agent_status(r.agent_id, 'AVAILABLE');
      end if;
    end if;
  end loop;

  -- 2. callers who gave up waiting
  update public.calls set status = 'ABANDONED', ended_at = now(), end_reason = 'abandoned'
   where status = 'QUEUED' and queued_at + make_interval(secs => patience_s) < now();

  -- 3. agents whose app stopped sending heartbeats
  for r in select s.agent_id, s.status, s.current_call_id from public.agent_status s
            where s.status <> 'OFFLINE' and coalesce(s.last_heartbeat_at, s.status_since) < now() - interval '60 seconds'
  loop
    if r.status = 'RINGING' then
      perform private.release_offer(r.current_call_id, r.agent_id, 'timeout');
    elsif r.status in ('ON_CALL', 'WRAP_UP') then
      update public.calls set status = 'COMPLETED', ended_at = coalesce(ended_at, now()), wrap_up_ended_at = now(),
             end_reason = coalesce(end_reason, 'agent_lost')
       where id = r.current_call_id;
    end if;
    perform private.set_agent_status(r.agent_id, 'OFFLINE', 'heartbeat lost');
  end loop;

  -- 4. a face check that was revoked or has expired (agents mid-call finish their call first)
  for r in select s.agent_id from public.agent_status s
            where s.status in ('NOT_READY', 'AVAILABLE', 'SYSTEM_HOLD') and not private.valid_face_check(s.agent_id)
  loop
    perform private.set_agent_status(r.agent_id, 'OFFLINE', 'face check no longer valid');
  end loop;

  -- 5. offer whatever is waiting
  for r in select distinct c.team_id from public.calls c where c.status = 'QUEUED' loop
    perform private.dispatch_calls(r.team_id);
  end loop;
end;
$$;

-- ------------------------------------------------------------------------------ access
alter table public.agent_status     enable row level security;
alter table public.agent_status_log enable row level security;
revoke all on public.agent_status, public.agent_status_log from anon, authenticated;
grant select on public.agent_status, public.agent_status_log to authenticated;

create policy agent_status_select on public.agent_status for select to authenticated
  using (agent_id = (select auth.uid()) or private.leads_team(team_id));
create policy agent_status_log_select on public.agent_status_log for select to authenticated
  using (agent_id = (select auth.uid()) or private.leads_team(team_id));

revoke all on function
  public.set_status(public.agent_state, text), public.heartbeat(text, boolean, text),
  public.accept_call(uuid), public.decline_call(uuid), public.end_call(uuid),
  public.finish_wrap_up(uuid, text, text), public.lookup_customer(text),
  public.start_run(jsonb, bigint), public.stop_run(uuid), public.system_end_call(uuid, text)
  from public, anon;
grant execute on function
  public.set_status(public.agent_state, text), public.heartbeat(text, boolean, text),
  public.accept_call(uuid), public.decline_call(uuid), public.end_call(uuid),
  public.finish_wrap_up(uuid, text, text), public.lookup_customer(text),
  public.start_run(jsonb, bigint), public.stop_run(uuid)
  to authenticated;
grant execute on function public.system_end_call(uuid, text) to service_role;

revoke all on function
  private.ensure_agent_row(uuid), private.set_agent_status(uuid, public.agent_state, text, uuid),
  private.valid_face_check(uuid), private.release_offer(uuid, uuid, text), private.dispatch_calls(uuid),
  private.rand01(bigint, bigint, integer), private.pick_language(jsonb, double precision),
  private.sim_tick(), private.sweep_calls()
  from public;

-- live updates for the dashboard (RLS still applies to what each viewer receives)
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.calls, public.agent_status;
  end if;
end;
$$;

-- scheduled jobs (pg_cron 1.5+ supports schedules in seconds)
create extension if not exists pg_cron with schema pg_catalog;
select cron.schedule('bpo-sweep', '5 seconds', 'select private.sweep_calls()');
select cron.schedule('bpo-sim-tick', '10 seconds', 'select private.sim_tick()');
