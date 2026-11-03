// One turn of a simulated call: the agent's words in, the AI caller's reply out.
//
//   {call_id, opening: true}              the call just connected: the caller speaks first
//   {call_id, agent_text: "..."}          the agent said something (already transcribed)
//   {call_id, silence: true}              the agent has been silent for several seconds
//   optional: stt_ms (how long the transcription took), tts_ms (how long the previous caller line
//             took to synthesize on the PC); both are stored for the latency report.
//
// The transcript is written here with the secret key, so a client cannot forge it. When the caller
// says goodbye (satisfied, hangs up, asks for a supervisor) the call moves to wrap-up.
import { adminClient } from "../_shared/clients.ts";
import { buildMessages, END_REASON, loadTurnContext, userMessageFor } from "../_shared/calls.ts";
import { callerTurn, groqKey } from "../_shared/groq.ts";
import { errorResponse, HttpError, json, preflight, readJson, reqString } from "../_shared/http.ts";
import { buildSystemPrompt, type CustomerRow, type ScenarioRow } from "../_shared/prompt.ts";

const MAX_TURNS_PER_CALL = 80;

const optMs = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 120_000 ? Math.round(v) : null;

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  try {
    const t0 = performance.now();
    const body = await readJson(req, 10_000);
    // warm-up ping: wakes this function so the first real turn does not pay for a cold start
    if (body["warmup"] === true) return json({ ok: true });
    const callId = reqString(body, "call_id", 36, 36);
    const admin = adminClient();
    const ctx = await loadTurnContext(req, admin, { callId, rateKey: (u) => `turn:${u}`, max: 40, full: true });
    const { call, scenario, customer } = ctx;
    const caller = { userId: ctx.userId };
    const history = ctx.turns;
    if (!scenario || !customer) throw new Error("could not load the scenario or customer");

    if (history.length >= MAX_TURNS_PER_CALL) throw new HttpError(409, "call_too_long", "this call has reached its length limit");

    const { message, storeAgentTurn } = userMessageFor(body, history.length > 0);

    const prep_ms = Math.round(performance.now() - t0);

    const system = buildSystemPrompt(scenario as ScenarioRow, customer as CustomerRow, call.mood, call.language);
    const { reply, model, ms } = await callerTurn(groqKey(), buildMessages(system, history, message));

    // the agent's turn (if any) and the caller's reply, in order
    let seq = history.length === 0 ? 0 : history[history.length - 1].seq;
    const rows = [];
    if (storeAgentTurn) {
      rows.push({
        call_id: call.id, agent_id: caller.userId, team_id: call.team_id, seq: ++seq, speaker: "agent",
        text: message, lang: call.language, stt_ms: optMs(body["stt_ms"]),
      });
    }
    rows.push({
      call_id: call.id, agent_id: caller.userId, team_id: call.team_id, seq: ++seq, speaker: "caller",
      text: reply.text, lang: call.language, llm_ms: ms,
    });
    // how long the previous caller line took to synthesize on the PC
    const ttsMs = optMs(body["tts_ms"]);
    const previousCaller = [...history].reverse().find((t) => t.speaker === "caller");
    const ttsUpdate = ttsMs !== null && previousCaller
      ? admin.from("call_turns").update({ tts_ms: ttsMs }).eq("call_id", call.id).eq("seq", previousCaller.seq)
      : Promise.resolve(null);
    const [{ error: iErr }] = await Promise.all([admin.from("call_turns").insert(rows), ttsUpdate]);
    if (iErr) {
      if (iErr.code === "23505") throw new HttpError(409, "turn_conflict", "another turn was recorded at the same time");
      throw new Error(`could not store the turn: ${iErr.message}`);
    }

    let ended = false;
    if (reply.state !== "continuing") {
      const { error: eErr } = await admin.rpc("system_end_call", { p_call: call.id, p_reason: END_REASON[reply.state] });
      if (eErr) throw new Error(`could not end the call: ${eErr.message}`);
      ended = true;
    }
    return json({ text: reply.text, state: reply.state, language: call.language, seq, ended, model, llm_ms: ms, json_ok: reply.jsonOk, prep_ms, server_ms: Math.round(performance.now() - t0) });
  } catch (e) {
    return errorResponse(e);
  }
});
