-- Let the client choose the id of its own monitoring session.
--
-- The offline queue (milestone 6) must be able to create a session id on the PC, keep recording
-- events against it while offline, and upload everything later without creating duplicates on a
-- retry. Choosing an id gives a client no extra power: agent_id, team_id and auth_session_id are
-- still forced by the BEFORE INSERT trigger, and the row level security check still requires a
-- valid face check. A clashing id simply fails the insert.

grant insert (id) on public.sessions to authenticated;
