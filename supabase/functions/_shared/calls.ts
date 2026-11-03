// Shared rules for the two functions that serve a live call (caller-turn and transcribe).
import type { Admin } from "./clients.ts";
import { bearer, claimsOf, type Caller } from "./auth.ts";
import { HttpError } from "./http.ts";
import type { CallerState, ChatMessage } from "./groq.ts";

export interface CallRow {
  id: string;
  team_id: string;
  scenario_id: string;
  customer_id: string;
  language: string;
  mood: string;
  status: string;
  agent_id: string | null;
}

/** The same rule as the database function face_mfa_ok(): a valid, unrevoked face check for THIS login. */
export async function requireFaceCheck(admin: Admin, caller: Caller): Promise<void> {
  if (!caller.sessionId) throw new HttpError(401, "unauthenticated", "the token has no session");
  const { data, error } = await admin
    .from("face_verifications")
    .select("id")
    .eq("agent_id", caller.userId)
    .eq("auth_session_id", caller.sessionId)
    .is("revoked_at", null)
    .gt("expires_at", new Date().toISOString())
    .limit(1);
  if (error) throw new Error(`face check lookup failed: ${error.message}`);
  if (!data || data.length === 0) throw new HttpError(403, "face_check_required", "a face check is required for this login");
}

/** The call must exist, belong to this agent, and be live. */
export async function requireActiveCall(admin: Admin, callId: string, agentId: string): Promise<CallRow> {
  const { data, error } = await admin
    .from("calls")
    .select("id, team_id, scenario_id, customer_id, language, mood, status, agent_id")
    .eq("id", callId)
    .maybeSingle();
  if (error) throw new Error(`call lookup failed: ${error.message}`);
  if (!data || data.agent_id !== agentId) throw new HttpError(404, "call_not_found", "no such call for you");
  if (data.status !== "ACTIVE") throw new HttpError(409, "call_not_active", "that call is not active");
  return data as CallRow;
}

export const END_REASON: Record<Exclude<CallerState, "continuing">, string> = {
  satisfied: "caller_satisfied",
  hangup: "caller_hangup",
  escalating: "caller_escalated",
};

export interface TurnInput {
  opening?: unknown;
  silence?: unknown;
  agent_text?: unknown;
}

/** What the model is shown as the agent's latest words, and whether it is stored as an agent turn. */
export function userMessageFor(input: TurnInput, hasTurns: boolean): { message: string; storeAgentTurn: boolean } {
  const given = [input.opening === true, input.silence === true, typeof input.agent_text === "string"].filter(Boolean).length;
  if (given !== 1) throw new HttpError(400, "invalid_field", "send exactly one of opening, silence or agent_text");
  if (input.opening === true) {
    if (hasTurns) throw new HttpError(409, "already_started", "the call has already started");
    return { message: "[call connected]", storeAgentTurn: false };
  }
  if (!hasTurns) throw new HttpError(409, "not_started", "the caller has not spoken yet; send opening first");
  if (input.silence === true) return { message: "[silence]", storeAgentTurn: true };
  const text = String(input.agent_text).trim();
  if (text.length === 0 || text.length > 1000) {
    throw new HttpError(400, "invalid_field", "agent_text must be 1-1000 characters");
  }
  return { message: text, storeAgentTurn: true };
}

/** The conversation so far as chat messages. Same-role neighbours are merged (models expect alternation). */
export function buildMessages(
  system: string,
  turns: { speaker: string; text: string }[],
  userMessage: string,
): ChatMessage[] {
  const messages: ChatMessage[] = [{ role: "system", content: system }];
  const push = (role: "user" | "assistant", content: string) => {
    const last = messages[messages.length - 1];
    if (last.role === role) last.content += "\n" + content;
    else messages.push({ role, content });
  };
  for (const t of turns) {
    if (t.speaker === "agent") push("user", t.text);
    else push("assistant", JSON.stringify({ text: t.text, state: "continuing" }));
  }
  push("user", userMessage);
  return messages;
}

// ---------------------------------------------------------------------------- one round trip per turn

const CONTEXT_ERRORS: Record<string, number> = {
  unauthenticated: 401,
  forbidden: 403,
  face_check_required: 403,
  call_not_found: 404,
  call_not_active: 409,
  too_many_requests: 429,
};

export interface TurnContext {
  userId: string;
  teamId: string;
  call: CallRow;
  turns: { seq: number; speaker: string; text: string }[];
  scenario: unknown;
  customer: unknown;
}

/**
 * Everything a live-call function needs, in two steps: Supabase Auth confirms the token, then ONE database
 * function (call_turn_context) checks the agent, the face check for this login, the call and the rate limit,
 * and returns the transcript, scenario and customer. The rate limit is only charged after the token is
 * confirmed, so a forged token cannot use up someone else's allowance.
 */
export async function loadTurnContext(
  req: Request,
  admin: Admin,
  opts: { callId: string; rateKey: (userId: string) => string; max: number; full: boolean },
): Promise<TurnContext> {
  const jwt = bearer(req);
  const { data: auth, error: aErr } = await admin.auth.getUser(jwt);
  if (aErr || !auth?.user) throw new HttpError(401, "unauthenticated", "invalid or expired token");
  const userId = auth.user.id;
  const sessionId = claimsOf(jwt)["session_id"];

  const { data, error } = await admin.rpc("call_turn_context", {
    p_user: userId,
    p_session: typeof sessionId === "string" ? sessionId : null,
    p_call: opts.callId,
    p_rate_key: opts.rateKey(userId),
    p_rate_max: opts.max,
    p_full: opts.full,
  });
  if (error) throw new Error(`turn context failed: ${error.message}`);
  const out = data as Record<string, unknown>;
  if (typeof out.error === "string") {
    throw new HttpError(CONTEXT_ERRORS[out.error] ?? 403, out.error, String(out.message ?? out.error));
  }
  return {
    userId,
    teamId: out.team_id as string,
    call: out.call as CallRow,
    turns: (out.turns ?? []) as TurnContext["turns"],
    scenario: out.scenario,
    customer: out.customer,
  };
}
