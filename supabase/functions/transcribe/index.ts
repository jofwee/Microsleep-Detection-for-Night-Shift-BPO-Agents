// Speech to text for the agent's side of a call: a short WAV in, the words out.
// multipart/form-data with `call_id` and `file` (16 kHz mono 16-bit WAV, up to about 90 s).
// The audio is forwarded to Groq Whisper and never stored.
import { adminClient } from "../_shared/clients.ts";
import { loadTurnContext } from "../_shared/calls.ts";
import { groqKey, transcribe } from "../_shared/groq.ts";
import { errorResponse, HttpError, json, preflight } from "../_shared/http.ts";

const MIN_BYTES = 2_000; // about 60 ms: nothing worth sending
const MAX_BYTES = 3_000_000; // about 94 s of 16 kHz mono 16-bit audio

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  try {
    if (req.method !== "POST") throw new HttpError(405, "method_not_allowed");
    // warm-up ping: wakes this function so the first real utterance does not pay for a cold start
    if ((req.headers.get("content-type") ?? "").includes("application/json")) return json({ ok: true });
    const t0 = performance.now();
    const declared = Number(req.headers.get("content-length") ?? 0);
    if (declared > MAX_BYTES + 10_000) throw new HttpError(413, "payload_too_large", "the audio is too long");
    let form: FormData;
    try {
      form = await req.formData();
    } catch {
      throw new HttpError(400, "invalid_body", "send multipart/form-data with call_id and file");
    }
    const callId = form.get("call_id");
    const file = form.get("file");
    if (typeof callId !== "string" || callId.length !== 36) throw new HttpError(400, "invalid_field", "call_id is required");
    if (!(file instanceof File)) throw new HttpError(400, "invalid_field", "file is required");
    if (file.size < MIN_BYTES) throw new HttpError(400, "audio_too_short", "that recording is too short");
    if (file.size > MAX_BYTES) throw new HttpError(413, "payload_too_large", "the audio is too long");

    const admin = adminClient();
    await loadTurnContext(req, admin, { callId, rateKey: (u) => `stt:${u}`, max: 40, full: false });
    const checks_ms = Math.round(performance.now() - t0);

    const { text, ms } = await transcribe(groqKey(), file);
    return json({ text, ms, checks_ms, server_ms: Math.round(performance.now() - t0) });
  } catch (e) {
    return errorResponse(e);
  }
});
