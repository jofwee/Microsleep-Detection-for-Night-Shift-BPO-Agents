-- Fix: a fresh sign-in could be switched OFFLINE ("heartbeat lost") within seconds.
--
-- The sweeper (every 5 s) marked an agent OFFLINE when the last heartbeat was older than 60 s. But a
-- sign-in only changes the status, not last_heartbeat_at, so after a long gap the old heartbeat of the
-- previous session was still the value compared, and the sweeper could run before the app's first new
-- heartbeat arrived. The desk then stayed on "offline" (a heartbeat never brings an OFFLINE agent back).
--
-- Now the silence is measured from the later of the last heartbeat and the last status change, so every
-- sign-in (and every status change) gets a full 60 s to send its first heartbeat. An app that really
-- died is still marked OFFLINE, 60 s after its last heartbeat or status change.
--
-- Only step 3 differs from the function in ..._agent_status_dispatch.sql. CREATE OR REPLACE keeps the
-- existing grants (no client can run it).

create or replace function private.sweep_calls()
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

  -- 3. agents whose app stopped sending heartbeats (silence counted from the later of the last
  --    heartbeat and the last status change, so a new sign-in is not judged by an old session)
  for r in select s.agent_id, s.status, s.current_call_id from public.agent_status s
            where s.status <> 'OFFLINE'
              and greatest(coalesce(s.last_heartbeat_at, s.status_since), s.status_since) < now() - interval '60 seconds'
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
