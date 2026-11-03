-- The team-leader dashboard shows transcripts and simulation runs live. alerts, events, calls and agent_status
-- were already published; add the two tables it also subscribes to. (Row level security still decides who sees what.)
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.call_turns, public.simulation_runs;
  end if;
end;
$$;
