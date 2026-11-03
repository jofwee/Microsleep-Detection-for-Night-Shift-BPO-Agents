-- One database round trip per spoken turn.
-- caller-turn and transcribe used to make five or six separate queries (profile, face check, call,
-- rate limit, transcript, scenario, customer), which cost about a second per turn. This function does
-- the same checks, in the same order and with the same rules, and returns what the turn needs.
-- Only the Edge Functions (secret key) can run it. The functions still verify the JWT with Supabase Auth.
create function public.call_turn_context(
  p_user uuid, p_session uuid, p_call uuid, p_rate_key text, p_rate_max integer, p_full boolean
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_profile public.profiles%rowtype;
  v_call    public.calls%rowtype;
  v_out     jsonb;
begin
  select * into v_profile from public.profiles where id = p_user;
  if not found or not v_profile.active then
    return jsonb_build_object('error', 'forbidden', 'message', 'no active account');
  end if;
  if v_profile.role <> 'agent' then
    return jsonb_build_object('error', 'forbidden', 'message', 'this action is for agents');
  end if;
  if p_session is null then
    return jsonb_build_object('error', 'unauthenticated', 'message', 'the token has no session');
  end if;
  if not exists (
    select 1 from public.face_verifications v
     where v.agent_id = p_user and v.auth_session_id = p_session and v.revoked_at is null and v.expires_at > now()
  ) then
    return jsonb_build_object('error', 'face_check_required', 'message', 'a face check is required for this login');
  end if;

  select * into v_call from public.calls where id = p_call;
  if not found or v_call.agent_id is distinct from p_user then
    return jsonb_build_object('error', 'call_not_found', 'message', 'no such call for you');
  end if;
  if v_call.status <> 'ACTIVE' then
    return jsonb_build_object('error', 'call_not_active', 'message', 'that call is not active');
  end if;
  if not public.rate_limit_hit(p_rate_key, p_rate_max, interval '1 minute') then
    return jsonb_build_object('error', 'too_many_requests', 'message', 'slow down');
  end if;

  v_out := jsonb_build_object(
    'call', jsonb_build_object(
      'id', v_call.id, 'team_id', v_call.team_id, 'scenario_id', v_call.scenario_id, 'customer_id', v_call.customer_id,
      'language', v_call.language, 'mood', v_call.mood, 'status', v_call.status, 'agent_id', v_call.agent_id),
    'team_id', v_profile.team_id);
  if p_full then
    v_out := v_out || jsonb_build_object(
      'turns', coalesce((select jsonb_agg(jsonb_build_object('seq', t.seq, 'speaker', t.speaker, 'text', t.text) order by t.seq)
                           from public.call_turns t where t.call_id = p_call), '[]'::jsonb),
      'scenario', (select jsonb_build_object('customer', s.customer, 'brief', s.brief, 'facts', s.facts)
                     from public.scenarios s where s.id = v_call.scenario_id),
      'customer', (select jsonb_build_object('full_name', c.full_name, 'account_no', c.account_no, 'plan', c.plan,
                                            'balance', c.balance, 'profile', c.profile)
                     from public.customers c where c.id = v_call.customer_id));
  end if;
  return v_out;
end;
$$;
revoke all on function public.call_turn_context(uuid, uuid, uuid, text, integer, boolean) from public, anon, authenticated;
grant execute on function public.call_turn_context(uuid, uuid, uuid, text, integer, boolean) to service_role;
