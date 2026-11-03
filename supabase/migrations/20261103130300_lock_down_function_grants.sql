-- Function privileges: only what is meant to be callable.
--
-- Supabase's default privileges grant EXECUTE on every new function in the public schema to anon
-- and authenticated. Migration 20261103130200 revoked it from public and anon but not from
-- authenticated, so a signed-in user could call public.system_end_call, which is for the
-- caller-turn Edge Function (secret key) only. Fix it, and stop new functions inheriting the grant.

revoke all on function public.system_end_call(uuid, text) from authenticated;
grant execute on function public.system_end_call(uuid, text) to service_role;

-- From now on a new function in public is callable by clients only after an explicit GRANT.
-- (Applies to functions created by this role, which is what migrations run as.)
alter default privileges in schema public revoke execute on functions from anon, authenticated;

-- The trigger functions from migration 20261103120500 were created without revoking the default
-- PUBLIC execute right. Postgres will not run a trigger function outside a trigger, and firing a
-- trigger does not need the caller to hold EXECUTE, so nothing was exposed, but nobody should hold it.
revoke all on function
  private.sessions_before_insert(), private.events_before_insert(),
  private.events_after_insert(), private.metrics_before_insert()
  from public, anon, authenticated;
