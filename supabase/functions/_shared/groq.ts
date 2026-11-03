// Groq: the caller's brain (chat) and the agent's ears (Whisper). The key lives only in the
// Edge Function secret GROQ_API_KEY, so it never reaches a PC.
import { HttpError } from "./http.ts";

const BASE = "https://api.groq.com/openai/v1";

// Chosen in Spike S1 (docs/spike-s1-results.md). Tried in order; a model that is missing or failing
// is skipped, because the live model list of an account can differ from the documentation.
export const CALLER_MODELS: { id: string; extra: Record<string, unknown> }[] = [
  { id: "qwen/qwen3.8-27b", extra: { reasoning_effort: "none" } },
  { id: "openai/gpt-oss-120b", extra: { reasoning_effort: "low" } },
];
export const STT_MODEL = "whisper-large-v3";
// Whisper reads the prompt as "what was just said", so it is written the way a Filipino agent talks
// (Spike S1: Taglish error rate 13% -> 10%, English unchanged). Keep it free of the spike's test sentences.
export const STT_PROMPT =
  "Opo, sandali lang po. Nakikita ko po na may balanse po kayo sa account. Pasensya na po kayo, " +
  "ire-reset ko po yung modem ninyo. Sir, pwede po bang i-restart niyo muna? Okay po, na-process na po yung request ninyo.";

export type FetchFn = typeof fetch;
export type CallerState = "continuing" | "satisfied" | "escalating" | "hangup";
const STATES: CallerState[] = ["continuing", "satisfied", "escalating", "hangup"];

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface CallerReply {
  text: string;
  state: CallerState;
  jsonOk: boolean;
}

export function groqKey(): string {
  const key = Deno.env.get("GROQ_API_KEY");
  if (!key) throw new Error("GROQ_API_KEY is not set");
  return key;
}

/** Models sometimes wrap the JSON in code fences or print their reasoning first: tolerate both. */
export function parseCallerReply(raw: string): CallerReply {
  const stripped = raw.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
  const cleaned = stripped.replace(/^```(?:json)?/gm, "").replace(/```$/gm, "").trim();
  const unquote = (t: string) => t.trim().replace(/^["“”]+|["“”]+$/g, "").slice(0, 600);
  try {
    const obj = JSON.parse(cleaned) as { text?: unknown; state?: unknown };
    const text = unquote(String(obj.text ?? ""));
    const state = STATES.includes(obj.state as CallerState) ? (obj.state as CallerState) : "continuing";
    return text ? { text, state, jsonOk: true } : { text: "Hello?", state: "continuing", jsonOk: false };
  } catch {
    const text = unquote(cleaned);
    return { text: text || "Hello?", state: "continuing", jsonOk: false };
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * One caller reply. For each model in turn: ask for JSON mode, and quietly drop settings the model
 * refuses (response_format, reasoning_effort); wait once if rate limited; move to the next model
 * if this one does not exist or is failing.
 */
export async function callerTurn(
  key: string,
  messages: ChatMessage[],
  fetchFn: FetchFn = fetch,
): Promise<{ reply: CallerReply; model: string; ms: number }> {
  let lastProblem = "no model tried";
  for (const model of CALLER_MODELS) {
    const body: Record<string, unknown> = {
      model: model.id,
      messages,
      temperature: 0.8,
      max_tokens: 300,
      response_format: { type: "json_object" },
      ...model.extra,
    };
    let waited = false;
    for (let attempt = 0; attempt < 5; attempt++) {
      const t0 = performance.now();
      const res = await fetchFn(`${BASE}/chat/completions`, {
        method: "POST",
        headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(20_000),
      });
      if (res.ok) {
        const data = await res.json() as { choices?: { message?: { content?: string } }[] };
        return {
          reply: parseCallerReply(data.choices?.[0]?.message?.content ?? ""),
          model: model.id,
          ms: Math.round(performance.now() - t0),
        };
      }
      const text = await res.text();
      lastProblem = `${model.id}: HTTP ${res.status}`;
      if (res.status === 400 && "response_format" in body && /response_format/i.test(text)) {
        delete body.response_format;
        continue;
      }
      if (res.status === 400 && "reasoning_effort" in body && /reasoning_effort/i.test(text)) {
        delete body.reasoning_effort;
        continue;
      }
      if (res.status === 429 && !waited) {
        waited = true;
        await sleep(Math.min(Number(res.headers.get("retry-after") ?? 1) * 1000, 2000));
        continue;
      }
      break; // 404, 5xx, or still failing: try the next model
    }
  }
  console.error("all caller models failed:", lastProblem);
  throw new HttpError(503, "caller_unavailable", "the simulated caller is temporarily unavailable");
}

/** Whisper, with the Taglish prompt and automatic language detection. */
export async function transcribe(
  key: string,
  wav: Blob,
  fetchFn: FetchFn = fetch,
): Promise<{ text: string; ms: number }> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const form = new FormData();
    form.append("file", wav, "speech.wav");
    form.append("model", STT_MODEL);
    form.append("prompt", STT_PROMPT);
    form.append("temperature", "0");
    form.append("response_format", "json");
    const t0 = performance.now();
    const res = await fetchFn(`${BASE}/audio/transcriptions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${key}` },
      body: form,
      signal: AbortSignal.timeout(30_000),
    });
    if (res.ok) {
      const data = await res.json() as { text?: string };
      return { text: (data.text ?? "").trim(), ms: Math.round(performance.now() - t0) };
    }
    if (res.status === 429 && attempt === 0) {
      await sleep(Math.min(Number(res.headers.get("retry-after") ?? 1) * 1000, 2000));
      continue;
    }
    console.error("transcription failed:", res.status, (await res.text()).slice(0, 200));
    break;
  }
  throw new HttpError(503, "transcription_unavailable", "speech recognition is temporarily unavailable");
}
