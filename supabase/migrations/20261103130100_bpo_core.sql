-- The simulated call center (part 1): scenarios, fictional customers, simulation runs, calls,
-- call offers and transcripts. Nothing here can be written by a client: calls and turns are
-- created by SQL functions (migration 20261103130200) and by Edge Functions with the secret key.

create type public.call_language as enum ('en', 'tl', 'taglish');
create type public.call_status as enum ('QUEUED', 'OFFERED', 'ACTIVE', 'WRAP_UP', 'COMPLETED', 'ABANDONED');

-- Reference data. No client access at all: a scenario holds the caller's private facts, and
-- the customer table is looked up only through lookup_customer() during a call.
create table public.scenarios (
  id                text primary key,
  category          text not null,
  title             text not null,
  brief             text not null,
  customer          text not null,                       -- persona sentence for the caller prompt
  facts             jsonb not null default '{}'::jsonb,  -- what the caller knows
  resolution_steps  text[] not null default '{}',
  difficulty        integer not null check (difficulty between 1 and 3),
  languages         public.call_language[] not null,
  moods             text[] not null,
  expected_turns    integer not null default 8,
  active            boolean not null default true
);

create table public.customers (
  id          uuid primary key default gen_random_uuid(),
  account_no  text not null unique,
  full_name   text not null,
  plan        text not null,
  balance     numeric(10, 2) not null default 0,
  status      text not null default 'ACTIVE',
  profile     jsonb not null default '{}'::jsonb          -- fictional extra details
);

create table public.simulation_runs (
  id             uuid primary key default gen_random_uuid(),
  team_id        uuid not null references public.teams (id) on delete cascade,
  started_by     uuid references auth.users (id) on delete set null,
  seed           bigint not null,
  config         jsonb not null,
  status         text not null default 'RUNNING' check (status in ('RUNNING', 'STOPPED')),
  started_at     timestamptz not null default now(),
  ended_at       timestamptz,
  last_tick_at   timestamptz,
  calls_created  integer not null default 0,
  arrival_carry  numeric not null default 0               -- fractional arrivals carried to the next tick
);
-- at most one running simulation per team
create unique index simulation_runs_one_running on public.simulation_runs (team_id) where status = 'RUNNING';

create table public.calls (
  id                uuid primary key default gen_random_uuid(),
  run_id            uuid references public.simulation_runs (id) on delete set null,
  team_id           uuid not null references public.teams (id) on delete cascade,
  scenario_id       text not null references public.scenarios (id),
  customer_id       uuid not null references public.customers (id),
  language          public.call_language not null,
  mood              text not null,
  status            public.call_status not null default 'QUEUED',
  agent_id          uuid references auth.users (id) on delete set null,   -- who it is offered to / who has it
  queued_at         timestamptz not null default now(),
  offered_at        timestamptz,
  offer_expires_at  timestamptz,
  answered_at       timestamptz,
  ended_at          timestamptz,
  wrap_up_ended_at  timestamptz,
  offer_attempts    integer not null default 0,
  patience_s        integer not null default 90 check (patience_s between 10 and 600),
  disposition       text check (disposition in ('resolved', 'unresolved', 'escalated')),
  end_reason        text,   -- agent_ended, caller_satisfied, caller_hangup, caller_escalated, abandoned, run_stopped, agent_lost
  notes             text
);
create index calls_team_status_idx on public.calls (team_id, status, queued_at);
create index calls_agent_idx       on public.calls (agent_id, status);
create index calls_run_idx         on public.calls (run_id);

-- Every offer of a call to an agent, and what became of it (for the missed-call statistics).
create table public.call_offers (
  id            bigint generated always as identity primary key,
  call_id       uuid not null references public.calls (id) on delete cascade,
  agent_id      uuid not null references auth.users (id) on delete cascade,
  team_id       uuid not null references public.teams (id) on delete cascade,
  offered_at    timestamptz not null default now(),
  responded_at  timestamptz,
  outcome       text check (outcome in ('accepted', 'declined', 'timeout'))
);
create index call_offers_call_idx on public.call_offers (call_id);

-- The words said, in order. Written only by the caller-turn Edge Function (secret key), so a
-- client cannot forge a transcript. Audio is never stored.
create table public.call_turns (
  id          bigint generated always as identity primary key,
  call_id     uuid not null references public.calls (id) on delete cascade,
  agent_id    uuid references auth.users (id) on delete set null,
  team_id     uuid not null references public.teams (id) on delete cascade,
  seq         integer not null,
  speaker     text not null check (speaker in ('agent', 'caller')),
  text        text not null,
  lang        text,
  created_at  timestamptz not null default now(),
  stt_ms      integer,   -- agent turns: speech to text
  llm_ms      integer,   -- caller turns: the model
  tts_ms      integer,   -- caller turns: synthesis on the PC, reported by the client
  unique (call_id, seq)
);

-- Events and per-minute metrics can now say which call they happened in, and the agent status.
alter table public.events         add column call_id uuid references public.calls (id) on delete set null;
alter table public.metrics_minute add column status  text;
grant insert (call_id) on public.events to authenticated;
grant insert (status)  on public.metrics_minute to authenticated;
create index events_call_idx on public.events (call_id) where call_id is not null;

-- The event trigger now also checks that a call_id belongs to the writing agent.
create or replace function private.events_before_insert()
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
  if new.ts < now() - interval '7 days' or new.ts > now() + interval '5 minutes' then
    raise exception 'event timestamp out of range' using errcode = '22008';
  end if;
  if new.call_id is not null and not exists (
    select 1 from public.calls c where c.id = new.call_id and c.agent_id = v_agent
  ) then
    raise exception 'unknown call' using errcode = '42501';
  end if;
  new.agent_id := v_agent;
  new.team_id := v_team;
  return new;
end;
$$;

-- Row level security: agents see their own calls, team leaders their team's.
alter table public.scenarios       enable row level security;
alter table public.customers       enable row level security;
alter table public.simulation_runs enable row level security;
alter table public.calls           enable row level security;
alter table public.call_offers     enable row level security;
alter table public.call_turns      enable row level security;
revoke all on public.scenarios, public.customers, public.simulation_runs, public.calls,
              public.call_offers, public.call_turns from anon, authenticated;

grant select on public.simulation_runs, public.calls, public.call_offers, public.call_turns to authenticated;

create policy runs_select on public.simulation_runs for select to authenticated
  using (private.leads_team(team_id));
create policy calls_select on public.calls for select to authenticated
  using (agent_id = (select auth.uid()) or private.leads_team(team_id));
create policy call_offers_select on public.call_offers for select to authenticated
  using (agent_id = (select auth.uid()) or private.leads_team(team_id));
create policy call_turns_select on public.call_turns for select to authenticated
  using (agent_id = (select auth.uid()) or private.leads_team(team_id));
-- scenarios and customers: no policy and no grant, on purpose.
