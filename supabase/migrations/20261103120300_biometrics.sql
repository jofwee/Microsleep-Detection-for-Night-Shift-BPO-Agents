-- Face templates and calibration.
--
-- face_templates holds the AES-256-GCM encrypted embeddings. NO client can read or write it:
-- RLS is on with no policy and no grants, so only the Edge Functions (secret key) touch it.
-- Photos are never stored, only numbers. Delete on request: manage-team "delete_biometrics".

create table public.face_templates (
  agent_id         uuid primary key references auth.users (id) on delete cascade,
  embeddings_enc   bytea not null,
  model_name       text not null,
  model_version    text not null,  -- embeddings from different models cannot be compared
  enrolled_at      timestamptz not null default now()
);

create table public.calibration_profiles (
  agent_id       uuid primary key references auth.users (id) on delete cascade,
  team_id        uuid not null references public.teams (id) on delete cascade,
  ear_open       real not null check (ear_open > 0 and ear_open < 1),
  ear_closed     real not null check (ear_closed >= 0 and ear_closed < ear_open),
  mar_closed     real not null check (mar_closed >= 0),
  mar_open       real,
  neutral_pitch  real not null default 0,
  neutral_yaw    real not null default 0,
  neutral_roll   real not null default 0,
  updated_at     timestamptz not null default now()
);

alter table public.face_templates       enable row level security;
alter table public.calibration_profiles enable row level security;
revoke all on public.face_templates, public.calibration_profiles from anon, authenticated;

-- The client loads its own calibration after sign-in; team leaders can read their team's.
grant select on public.calibration_profiles to authenticated;
create policy calibration_select on public.calibration_profiles
  for select to authenticated
  using (agent_id = (select auth.uid()) or private.leads_team(team_id));
