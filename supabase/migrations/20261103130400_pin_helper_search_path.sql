-- The security advisor flagged two helpers whose search_path was not fixed. Pin it (pg_catalog is always searched).
alter function private.rand01(bigint, bigint, integer) set search_path = '';
alter function private.pick_language(jsonb, double precision) set search_path = '';
