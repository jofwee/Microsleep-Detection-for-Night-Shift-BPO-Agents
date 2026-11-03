-- The offline queue resends events after a dropped connection. One event of a kind per session and
-- timestamp (to the millisecond) makes a resend harmless: the client inserts with "ignore duplicates".
create unique index events_dedupe_idx on public.events (session_id, type, ts);
