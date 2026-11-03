-- Monitoring data: sessions, events, per-minute metrics, alerts, audit log.
--
-- Agent writes need a valid face check (private.face_mfa_ok()). The client never supplies
-- agent_id / team_id: BEFORE INSERT triggers fill them from the login, so they cannot be forged.

create type public.event_type as enum (
  'MICROSLEEP', 'LONG_BLINK', 'YAWN', 'HEAD_NOD', 'HEAD_TILT', 'DROWSY',
  'ABSENT', 'IDENTITY_MISMATCH', 'MULTIPLE_FACES', 'SPOOF_SUSPECTED'
);

create table public.sessions (
  id               uuid primary key default gen_random_uuid(),
  agent_id         uuid not null references auth.users (id) on delete cascade,
  team_id          uuid not null references public.teams (id) on delete cascade,
  workstation_id   uuid references public.workstations (id) on delete set null,
  auth_session_id  uuid not null,
  started_at       timestamptz not null default now(),
  ended_at         timestamptz
);
create index sessions_agent_idx on public.sessions (agent_id, started_at desc);
create index sessions_team_idx  on public.sessions (team_id, started_at desc);

create table public.events (
  id           bigint generated always as identity primary key,
  session_id   uuid not null references public.sessions (id) on delete cascade,
  agent_id     uuid not null references auth.users (id) on delete cascade,
  team_id      uuid not null references public.teams (id) on delete cascade,
  type         public.event_type not null,
  ts           timestamptz not null,
  duration_ms  integer not null default 0 check (duration_ms >= 0),
  value        real,
  severity     text not null default 'info' check (severity in ('info', 'warning', 'critical'))
);
create index events_session_idx on public.events (session_id, ts);
create index events_team_idx    on public.events (team_id, ts desc);
create index events_agent_idx   on public.events (agent_id, type, ts desc);

create table public.metrics_minute (
  session_id  uuid not null references public.sessions (id) on delete cascade,
  minute      timestamptz not null,
  agent_id    uuid not null references auth.users (id) on delete cascade,
  team_id     uuid not null references public.teams (id) on delete cascade,
  blink_rate  real,
  perclos     real check (perclos is null or (perclos >= 0 and perclos <= 1)),
  mean_ear    real,
  yawns       integer not null default 0 check (yawns >= 0),
  nods        integer not null default 0 check (nods >= 0),
  primary key (session_id, minute)
);

create table public.alerts (
  id               uuid primary key default gen_random_uuid(),
  team_id          uuid not null references public.teams (id) on delete cascade,
  agent_id         uuid not null references auth.users (id) on delete cascade,
  event_id         bigint references public.events (id) on delete set null,
  kind             text not null check (kind in
                     ('IDENTITY_MISMATCH', 'SPOOF_SUSPECTED', 'REPEATED_MICROSLEEP', 'FACE_LOCKOUT')),
  message          text not null,
  created_at       timestamptz not null default now(),
  acknowledged_by  uuid references auth.users (id) on delete set null,
  acknowledged_at  timestamptz
);
create index alerts_team_idx on public.alerts (team_id, created_at desc);

create table public.audit_log (
  id         bigint generated always as identity primary key,
  team_id    uuid references public.teams (id) on delete set null,
  actor_id   uuid references auth.users (id) on delete set null,
  action     text not null,
  target_id  uuid,
  ts         timestamptz not null default now(),
  detail     jsonb not null default '{}'::jsonb
);
create index audit_log_team_idx on public.audit_log (team_id, ts desc);

-- ---------------------------------------------------------------------------------------
-- Triggers that fill identity columns and validate writes
-- ---------------------------------------------------------------------------------------
create function private.sessions_before_insert()
returns trigger
language plpgsql security definer set search_path = ''
as $$
declare
  v_team uuid;
begin
  select p.team_id into v_team
  from public.profiles p
  where p.id = (select auth.uid()) and p.role = 'agent' and p.active;
  if v_team is null then
    raise exception 'not an active agent' using errcode = '42501';
  end if;
  if new.workstation_id is not null and not exists (
    select 1 from public.workstations w
    where w.id = new.workstation_id and w.team_id = v_team and w.revoked_at is null
  ) then
    raise exception 'unknown workstation' using errcode = '42501';
  end if;
  new.agent_id := (select auth.uid());
  new.team_id := v_team;
  new.auth_session_id := nullif((select auth.jwt() ->> 'session_id'), '')::uuid;
  new.started_at := now();
  new.ended_at := null;
  return new;
end;
$$;
create trigger sessions_before_insert before insert on public.sessions
  for each row execute function private.sessions_before_insert();

create function private.events_before_insert()
returns trigger
language plpgsql security definer set search_path = ''
as $$
declare
  v_agent uuid;
  v_team  uuid;
begin
  select s.agent_id, s.team_id into v_agent, v_team
  from public.sessions s
  where s.id = new.session_id and s.agent_id = (select auth.uid()) and s.ended_at is null;
  if v_agent is null then
    raise exception 'unknown or closed session' using errcode = '42501';
  end if;
  -- the offline queue may sync late, but not absurdly late or from the future
  if new.ts < now() - interval '7 days' or new.ts > now() + interval '5 minutes' then
    raise exception 'event timestamp out of range' using errcode = '22008';
  end if;
  new.agent_id := v_agent;
  new.team_id := v_team;
  return new;
end;
$$;
create trigger events_before_insert before insert on public.events
  for each row execute function private.events_before_insert();

create function private.metrics_before_insert()
returns trigger
language plpgsql security definer set search_path = ''
as $$
declare
  v_agent uuid;
  v_team  uuid;
begin
  select s.agent_id, s.team_id into v_agent, v_team
  from public.sessions s
  where s.id = new.session_id and s.agent_id = (select auth.uid()) and s.ended_at is null;
  if v_agent is null then
    raise exception 'unknown or closed session' using errcode = '42501';
  end if;
  new.agent_id := v_agent;
  new.team_id := v_team;
  new.minute := date_trunc('minute', new.minute);
  return new;
end;
$$;
create trigger metrics_before_insert before insert on public.metrics_minute
  for each row execute function private.metrics_before_insert();

-- ---------------------------------------------------------------------------------------
-- Escalation: what an event sets in motion
-- ---------------------------------------------------------------------------------------
-- IDENTITY_MISMATCH / SPOOF_SUSPECTED: revoke the agent's face verifications (so a modified
-- client cannot keep writing) and alert the team leader. Two MICROSLEEPs in 10 minutes: alert.
create function private.events_after_insert()
returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  if new.type in ('IDENTITY_MISMATCH', 'SPOOF_SUSPECTED') then
    update public.face_verifications
       set revoked_at = now(), revoked_reason = new.type::text
     where agent_id = new.agent_id and revoked_at is null;
    -- the revoked client can no longer close its own sessions, so close them here
    update public.sessions set ended_at = now()
     where agent_id = new.agent_id and ended_at is null;
    insert into public.alerts (team_id, agent_id, event_id, kind, message)
    values (new.team_id, new.agent_id, new.id, new.type::text,
            case new.type
              when 'IDENTITY_MISMATCH' then 'A face that is not the enrolled agent was in the seat. The agent was signed out.'
              else 'Possible photo, screen or video of the agent. The agent was signed out.'
            end);
  elsif new.type = 'MICROSLEEP' then
    if (select count(*) from public.events e
         where e.agent_id = new.agent_id and e.type = 'MICROSLEEP'
           and e.ts > new.ts - interval '10 minutes') >= 2
       and not exists (select 1 from public.alerts a
                        where a.agent_id = new.agent_id and a.kind = 'REPEATED_MICROSLEEP'
                          and a.created_at > now() - interval '10 minutes') then
      insert into public.alerts (team_id, agent_id, event_id, kind, message)
      values (new.team_id, new.agent_id, new.id, 'REPEATED_MICROSLEEP',
              'Two or more microsleeps within 10 minutes.');
    end if;
  end if;
  return null;
end;
$$;
create trigger events_after_insert after insert on public.events
  for each row execute function private.events_after_insert();

-- Team leader acknowledges an alert (only for their own team).
create function public.ack_alert(p_alert uuid)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v_team uuid;
begin
  select a.team_id into v_team from public.alerts a where a.id = p_alert;
  if v_team is null or not private.leads_team(v_team) then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  update public.alerts
     set acknowledged_by = (select auth.uid()), acknowledged_at = now()
   where id = p_alert and acknowledged_at is null;
end;
$$;
revoke all on function public.ack_alert(uuid) from public, anon;
grant execute on function public.ack_alert(uuid) to authenticated;

-- ---------------------------------------------------------------------------------------
-- Row level security
-- ---------------------------------------------------------------------------------------
alter table public.sessions       enable row level security;
alter table public.events         enable row level security;
alter table public.metrics_minute enable row level security;
alter table public.alerts         enable row level security;
alter table public.audit_log      enable row level security;
revoke all on public.sessions, public.events, public.metrics_minute, public.alerts, public.audit_log
  from anon, authenticated;

-- Reads: an agent sees their own rows, a team leader their team's.
grant select on public.sessions, public.events, public.metrics_minute to authenticated;
create policy sessions_select on public.sessions for select to authenticated
  using (agent_id = (select auth.uid()) or private.leads_team(team_id));
create policy events_select on public.events for select to authenticated
  using (agent_id = (select auth.uid()) or private.leads_team(team_id));
create policy metrics_select on public.metrics_minute for select to authenticated
  using (agent_id = (select auth.uid()) or private.leads_team(team_id));

-- Writes: only with a valid face check. The triggers above fix agent_id and team_id.
grant insert (session_id, type, ts, duration_ms, value, severity) on public.events to authenticated;
grant insert (session_id, minute, blink_rate, perclos, mean_ear, yawns, nods) on public.metrics_minute to authenticated;
grant insert (workstation_id) on public.sessions to authenticated;
grant update (ended_at) on public.sessions to authenticated;

create policy sessions_insert on public.sessions for insert to authenticated
  with check (agent_id = (select auth.uid()) and private.face_mfa_ok());
create policy sessions_end on public.sessions for update to authenticated
  using (agent_id = (select auth.uid()) and private.face_mfa_ok())
  with check (agent_id = (select auth.uid()));
create policy events_insert on public.events for insert to authenticated
  with check (agent_id = (select auth.uid()) and private.face_mfa_ok());
create policy metrics_insert on public.metrics_minute for insert to authenticated
  with check (agent_id = (select auth.uid()) and private.face_mfa_ok());

-- Alerts: team leaders read them (and acknowledge through ack_alert()). Created by the trigger.
grant select on public.alerts to authenticated;
create policy alerts_select on public.alerts for select to authenticated
  using (private.leads_team(team_id));

-- Audit log: team leaders read their team's entries. Written by Edge Functions only.
grant select on public.audit_log to authenticated;
create policy audit_select on public.audit_log for select to authenticated
  using (team_id is not null and private.leads_team(team_id));

-- Live updates for the dashboard (RLS still applies to what each viewer receives).
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.alerts, public.events;
  end if;
end;
$$;
