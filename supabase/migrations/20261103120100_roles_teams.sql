-- Roles, teams, profiles, and the helper functions every access rule uses.
--
-- Access model (plan.md, "Authentication and roles"):
--   agent        sees only their own rows
--   team_leader  sees the rows of the team they lead (teams.leader_id)
-- Clients never write to these tables; accounts are created by the manage-team Edge Function
-- and scripts/create_team.py, both of which use the secret key.

create schema if not exists private;
revoke all on schema private from public;
grant usage on schema private to authenticated, service_role;

create type public.app_role as enum ('agent', 'team_leader');

create table public.teams (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique check (length(name) between 1 and 80),
  leader_id   uuid unique references auth.users (id) on delete restrict,
  created_at  timestamptz not null default now()
);

create table public.profiles (
  id                    uuid primary key references auth.users (id) on delete cascade,
  role                  public.app_role not null,
  team_id               uuid not null references public.teams (id) on delete restrict,
  full_name             text not null check (length(full_name) between 1 and 120),
  consent_at            timestamptz,
  must_change_password  boolean not null default true,
  active                boolean not null default true,
  created_at            timestamptz not null default now()
);
create index profiles_team_idx on public.profiles (team_id);

-- Helpers. SECURITY DEFINER with an empty search_path, so they cannot be hijacked and can
-- read the tables whatever the caller's own rights are.
create function private.leads_team(p_team uuid)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1
    from public.teams t
    join public.profiles p on p.id = t.leader_id
    where t.id = p_team
      and t.leader_id = (select auth.uid())
      and p.active and p.role = 'team_leader'
  );
$$;

create function private.my_team()
returns uuid
language sql stable security definer set search_path = ''
as $$
  select p.team_id from public.profiles p where p.id = (select auth.uid()) and p.active;
$$;

create function private.is_active_agent()
returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1 from public.profiles p
    where p.id = (select auth.uid()) and p.role = 'agent' and p.active
  );
$$;

revoke all on function private.leads_team(uuid), private.my_team(), private.is_active_agent() from public;
grant execute on function private.leads_team(uuid), private.my_team(), private.is_active_agent()
  to authenticated, service_role;

-- Row level security: default deny, then only these reads.
alter table public.teams    enable row level security;
alter table public.profiles enable row level security;

revoke all on public.teams, public.profiles from anon, authenticated;
grant select on public.teams, public.profiles to authenticated;

create policy teams_select on public.teams
  for select to authenticated
  using (id = private.my_team() or private.leads_team(id));

create policy profiles_select on public.profiles
  for select to authenticated
  using (id = (select auth.uid()) or private.leads_team(team_id));

-- The one thing an agent may change about their own profile: after they set a new password
-- through Supabase Auth, tell us so the "change your temporary password" gate opens.
create function public.ack_password_changed()
returns void
language sql security definer set search_path = ''
as $$
  update public.profiles set must_change_password = false where id = (select auth.uid());
$$;
revoke all on function public.ack_password_changed() from public, anon;
grant execute on function public.ack_password_changed() to authenticated;
