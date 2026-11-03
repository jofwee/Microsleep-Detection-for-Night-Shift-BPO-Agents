-- Face second factor.
--
-- Sign-in for an agent is email + password (Supabase Auth) + a face check. A passed face
-- check is a row in face_verifications bound to the login's session_id. Row level security
-- on every agent-written table asks private.face_mfa_ok(), so a password alone writes nothing.
--
-- Only the Edge Functions (secret key) create challenges and verifications.

create table public.face_challenges (
  id               uuid primary key default gen_random_uuid(),
  agent_id         uuid not null references auth.users (id) on delete cascade,
  team_id          uuid not null references public.teams (id) on delete cascade,
  workstation_id   uuid not null references public.workstations (id) on delete cascade,
  auth_session_id  uuid not null,
  nonce_hash       bytea not null,
  actions          text[] not null,            -- e.g. {BLINK_TWICE,TURN_LEFT}
  created_at       timestamptz not null default now(),
  expires_at       timestamptz not null,       -- 60 s after creation
  used_at          timestamptz,                -- single use
  outcome          text check (outcome in ('passed', 'failed')),
  reason           text,
  score            real
);
create index face_challenges_agent_idx on public.face_challenges (agent_id, created_at desc);

create table public.face_verifications (
  id               uuid primary key default gen_random_uuid(),
  agent_id         uuid not null references auth.users (id) on delete cascade,
  team_id          uuid not null references public.teams (id) on delete cascade,
  auth_session_id  uuid not null,
  workstation_id   uuid not null references public.workstations (id) on delete cascade,
  score            real not null,
  liveness_ok      boolean not null,
  verified_at      timestamptz not null default now(),
  expires_at       timestamptz not null,       -- 12 h
  revoked_at       timestamptz,
  revoked_reason   text
);
create index face_verifications_lookup_idx
  on public.face_verifications (agent_id, auth_session_id) where revoked_at is null;

-- The gate. True only if the caller is an active agent and holds an unexpired, unrevoked
-- face verification for THIS login session.
create function private.face_mfa_ok()
returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1
    from public.face_verifications v
    join public.profiles p on p.id = v.agent_id
    where v.agent_id = (select auth.uid())
      and v.auth_session_id = nullif((select auth.jwt() ->> 'session_id'), '')::uuid
      and v.revoked_at is null
      and v.expires_at > now()
      and p.active
      and p.role = 'agent'
  );
$$;
revoke all on function private.face_mfa_ok() from public;
grant execute on function private.face_mfa_ok() to authenticated, service_role;

alter table public.face_challenges   enable row level security;
alter table public.face_verifications enable row level security;
revoke all on public.face_challenges, public.face_verifications from anon, authenticated;

-- Agents may see their own verification state; team leaders see their team's face-check history.
grant select on public.face_verifications to authenticated;
create policy face_verifications_select on public.face_verifications
  for select to authenticated
  using (agent_id = (select auth.uid()) or private.leads_team(team_id));
-- face_challenges: no client access at all (nonces are secret).
