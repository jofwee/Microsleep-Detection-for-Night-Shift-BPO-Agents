-- Paired workstations, one-time setup codes, and a small rate limiter.
--
-- A workstation token is a random secret handed to a PC once, when a team leader's pairing
-- code is redeemed. Only its SHA-256 hash is stored. Setup codes are single-use, expire after
-- 15 minutes, and are also stored only as hashes, so a database leak reveals no usable code.

create table public.workstations (
  id           uuid primary key default gen_random_uuid(),
  team_id      uuid not null references public.teams (id) on delete cascade,
  name         text not null default 'workstation' check (length(name) <= 80),
  token_hash   bytea not null unique,
  paired_by    uuid references auth.users (id) on delete set null,
  created_at   timestamptz not null default now(),
  last_seen_at timestamptz,
  revoked_at   timestamptz
);
create index workstations_team_idx on public.workstations (team_id);

create type public.setup_code_kind as enum ('enrollment', 'pairing');

create table public.setup_codes (
  id          uuid primary key default gen_random_uuid(),
  kind        public.setup_code_kind not null,
  team_id     uuid not null references public.teams (id) on delete cascade,
  agent_id    uuid references auth.users (id) on delete cascade,  -- enrollment codes only
  code_hash   bytea not null unique,
  created_by  uuid not null references auth.users (id) on delete cascade,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null,
  used_at     timestamptz,
  check ((kind = 'enrollment') = (agent_id is not null))
);
create index setup_codes_open_idx on public.setup_codes (team_id, kind) where used_at is null;

alter table public.workstations enable row level security;
alter table public.setup_codes  enable row level security;
revoke all on public.workstations, public.setup_codes from anon, authenticated;

-- Team leaders may list their team's PCs, but never the token hash. setup_codes: no client access.
grant select (id, team_id, name, paired_by, created_at, last_seen_at, revoked_at)
  on public.workstations to authenticated;

create policy workstations_leader_select on public.workstations
  for select to authenticated
  using (private.leads_team(team_id));

-- Rate limiter for public endpoints (pairing) and lockouts. Called only by Edge Functions.
create table private.rate_limits (
  key     text not null,
  hit_at  timestamptz not null default now()
);
create index rate_limits_key_idx on private.rate_limits (key, hit_at);

-- Returns true if the call is allowed (and records it), false if the limit is exceeded.
create function public.rate_limit_hit(p_key text, p_max integer, p_window interval)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
  v_count integer;
begin
  delete from private.rate_limits where hit_at < now() - interval '1 day';
  select count(*) into v_count
  from private.rate_limits
  where key = p_key and hit_at > now() - p_window;
  if v_count >= p_max then
    return false;
  end if;
  insert into private.rate_limits (key) values (p_key);
  return true;
end;
$$;
revoke all on function public.rate_limit_hit(text, integer, interval) from public, anon, authenticated;
grant execute on function public.rate_limit_hit(text, integer, interval) to service_role;
