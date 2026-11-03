// Tests for the simulated-caller functions: prompt building (against the Python golden file),
// reply parsing, turn validation, and the Groq client with a fake network.
import { assert, assertEquals, assertRejects, assertThrows } from "jsr:@std/assert@1";
import { parse as parseYaml } from "jsr:@std/yaml@1";
import { buildMessages, END_REASON, userMessageFor } from "../_shared/calls.ts";
import { callerTurn, parseCallerReply, STT_PROMPT, transcribe } from "../_shared/groq.ts";
import { HttpError } from "../_shared/http.ts";
import { buildSystemPrompt, customerValues, fill } from "../_shared/prompt.ts";
import { BASE_PROMPT, LANGUAGE_STYLES, MOODS } from "../_shared/prompts.generated.ts";

const root = new URL("../../../", import.meta.url);
// Windows editors save CRLF; the Python builder (and so the generated file) normalises to LF.
const read = async (path: string) => (await Deno.readTextFile(new URL(path, root))).replaceAll("\r\n", "\n");

const CUSTOMER = {
  full_name: "Maria Santos", account_no: "AC-1234-5678", plan: "Fiber 300", balance: 1500,
  profile: { area: "Quezon City", birthdate: "1990-04-12", email: "maria.santos@example.com", contact_number: "09171234567", member_since: "2019" },
};

// ------------------------------------------------------------------------------ prompt
Deno.test("the TypeScript prompt is identical to the Python golden prompt", async () => {
  const scenario = parseYaml(await read("simulation/scenarios/internet_down.yaml")) as {
    customer: string; brief: string; facts: Record<string, unknown>;
  };
  const golden = await read("simulation/prompts/golden/internet_down_taglish_frustrated.txt");
  assertEquals(buildSystemPrompt(scenario, CUSTOMER, "frustrated", "taglish"), golden);
});

Deno.test("every scenario, language and mood builds with nothing left unfilled", async () => {
  for await (const f of Deno.readDir(new URL("simulation/scenarios/", root))) {
    if (!f.name.endsWith(".yaml")) continue; // skips the .gitkeep placeholder
    const sc = parseYaml(await read(`simulation/scenarios/${f.name}`)) as { customer: string; brief: string; facts: Record<string, unknown>; moods: string[] };
    for (const lang of Object.keys(LANGUAGE_STYLES)) {
      for (const mood of sc.moods) {
        const prompt = buildSystemPrompt(sc, CUSTOMER, mood, lang);
        assertEquals(prompt.match(/\{[a-z_]+\}/g), null, `${f.name} ${lang} ${mood}`);
        assert(prompt.includes("Maria Santos") && prompt.includes(MOODS[mood]));
      }
    }
  }
});

Deno.test("the generated prompt file matches the source files", async () => {
  assertEquals(BASE_PROMPT, await read("simulation/prompts/base.md"));
  for (const lang of Object.keys(LANGUAGE_STYLES)) {
    assertEquals(LANGUAGE_STYLES[lang], (await read(`simulation/prompts/${lang}.md`)).trim());
  }
});

Deno.test("fill and customerValues", () => {
  assertEquals(fill("Hi {full_name} {nope}", { full_name: "Ana" }), "Hi Ana {nope}");
  const v = customerValues(CUSTOMER);
  assertEquals([v.balance, v.area, v.account_no], ["1500.00", "Quezon City", "AC-1234-5678"]);
  assertEquals(customerValues({ ...CUSTOMER, balance: "99.5", profile: null }).balance, "99.50");
});

Deno.test("an unknown language or mood is refused, and special characters in data stay literal", () => {
  const sc = { customer: "{full_name}", brief: "price is $& and $1", facts: { note: "cost $' now" } };
  assertThrows(() => buildSystemPrompt(sc, CUSTOMER, "calm", "fr"), Error, "unknown language");
  assertThrows(() => buildSystemPrompt(sc, CUSTOMER, "sulky", "en"), Error, "unknown mood");
  const prompt = buildSystemPrompt(sc, { ...CUSTOMER, full_name: "A $& B" }, "calm", "en");
  assert(prompt.includes("price is $& and $1") && prompt.includes("cost $' now") && prompt.includes("A $& B"));
});

// ------------------------------------------------------------------------------ reply parsing
Deno.test("parseCallerReply handles clean JSON, code fences, reasoning text and bad states", () => {
  assertEquals(parseCallerReply('{"text": "Hello po", "state": "continuing"}'), { text: "Hello po", state: "continuing", jsonOk: true });
  assertEquals(parseCallerReply('```json\n{"text": "Salamat po", "state": "satisfied"}\n```').state, "satisfied");
  assertEquals(parseCallerReply('<think>let me think about this</think>{"text": "Hi", "state": "hangup"}').text, "Hi");
  assertEquals(parseCallerReply('{"text": "Hi", "state": "furious"}').state, "continuing");
  assertEquals(parseCallerReply('{"text": "“Hello po”", "state": "continuing"}').text, "Hello po");
});

Deno.test("parseCallerReply never returns nothing", () => {
  const plain = parseCallerReply("Hello? Are you there?");
  assertEquals([plain.text, plain.state, plain.jsonOk], ["Hello? Are you there?", "continuing", false]);
  assertEquals(parseCallerReply('{"text": "", "state": "continuing"}').text, "Hello?");
  assertEquals(parseCallerReply("").text, "Hello?");
  assertEquals(parseCallerReply(JSON.stringify({ text: "x".repeat(2000), state: "continuing" })).text.length, 600);
});

// ------------------------------------------------------------------------------ turns
Deno.test("userMessageFor: exactly one kind of input, in the right order", () => {
  assertEquals(userMessageFor({ opening: true }, false), { message: "[call connected]", storeAgentTurn: false });
  assertEquals(userMessageFor({ silence: true }, true), { message: "[silence]", storeAgentTurn: true });
  assertEquals(userMessageFor({ agent_text: "  Hello po  " }, true), { message: "Hello po", storeAgentTurn: true });
  assertThrows(() => userMessageFor({}, true), HttpError);
  assertThrows(() => userMessageFor({ opening: true, silence: true }, false), HttpError);
  assertThrows(() => userMessageFor({ opening: true }, true), HttpError, "already");
  assertThrows(() => userMessageFor({ agent_text: "hi" }, false), HttpError, "opening first");
  assertThrows(() => userMessageFor({ agent_text: "   " }, true), HttpError);
  assertThrows(() => userMessageFor({ agent_text: "x".repeat(1001) }, true), HttpError);
  assertThrows(() => userMessageFor({ agent_text: 42 }, true), HttpError);
});

Deno.test("buildMessages alternates roles, wraps caller lines as JSON and merges neighbours", () => {
  const m = buildMessages("SYS", [
    { speaker: "caller", text: "Hello po" },
    { speaker: "agent", text: "Good day" },
    { speaker: "agent", text: "[silence]" },
    { speaker: "caller", text: "Hello?" },
  ], "Account number?");
  assertEquals(m.map((x) => x.role), ["system", "assistant", "user", "assistant", "user"]);
  assertEquals(m[2].content, "Good day\n[silence]");
  assertEquals(JSON.parse(m[1].content), { text: "Hello po", state: "continuing" });
  assertEquals(m[4].content, "Account number?");
  assertEquals(buildMessages("SYS", [], "[call connected]").map((x) => x.role), ["system", "user"]);
});

Deno.test("each caller ending maps to a call end reason", () => {
  assertEquals(END_REASON, { satisfied: "caller_satisfied", hangup: "caller_hangup", escalating: "caller_escalated" });
});

// ------------------------------------------------------------------------------ Groq client (fake network)
const ok = (content: string) =>
  new Response(JSON.stringify({ choices: [{ message: { content } }] }), { status: 200, headers: { "content-type": "application/json" } });
const bad = (status: number, text: string, headers: Record<string, string> = {}) => new Response(text, { status, headers });

function fakeNetwork(...handlers: ((body: Record<string, unknown>) => Response)[]) {
  const sent: { url: string; auth: string | null; body: Record<string, unknown> }[] = [];
  let i = 0;
  const fn: typeof fetch = (input, init) => {
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : {};
    sent.push({ url: String(input), auth: new Headers(init?.headers).get("authorization"), body });
    return Promise.resolve(handlers[Math.min(i++, handlers.length - 1)](body));
  };
  return { fn, sent };
}

const MSGS = [{ role: "system" as const, content: "S" }, { role: "user" as const, content: "[call connected]" }];
const REPLY = '{"text": "Hello po", "state": "continuing"}';

Deno.test("callerTurn: uses the first model with JSON mode and the key", async () => {
  const net = fakeNetwork(() => ok(REPLY));
  const r = await callerTurn("gsk_test", MSGS, net.fn);
  assertEquals([r.reply.text, r.model], ["Hello po", "qwen/qwen3.8-27b"]);
  assertEquals(net.sent.length, 1);
  assertEquals(net.sent[0].auth, "Bearer gsk_test");
  assertEquals(net.sent[0].body.response_format, { type: "json_object" });
  assertEquals(net.sent[0].body.reasoning_effort, "none");
  assertEquals(net.sent[0].body.messages, MSGS);
});

Deno.test("callerTurn: a model that does not exist falls through to the next one", async () => {
  const net = fakeNetwork(() => bad(404, '{"error":{"code":"model_not_found"}}'), () => ok(REPLY));
  const r = await callerTurn("k", MSGS, net.fn);
  assertEquals(r.model, "openai/gpt-oss-120b");
  assertEquals(net.sent[1].body.reasoning_effort, "low");
});

Deno.test("callerTurn: settings the model refuses are dropped and the call is retried", async () => {
  const net = fakeNetwork(
    () => bad(400, "response_format is not supported"),
    () => bad(400, "reasoning_effort is not supported"),
    () => ok(REPLY),
  );
  const r = await callerTurn("k", MSGS, net.fn);
  assertEquals(r.model, "qwen/qwen3.8-27b");
  assertEquals("response_format" in net.sent[2].body, false);
  assertEquals("reasoning_effort" in net.sent[2].body, false);
  assertEquals(net.sent.length, 3);
});

Deno.test("callerTurn: a rate limit is waited out once", async () => {
  const net = fakeNetwork(() => bad(429, "slow down", { "retry-after": "0" }), () => ok(REPLY));
  const r = await callerTurn("k", MSGS, net.fn);
  assertEquals([r.model, net.sent.length], ["qwen/qwen3.8-27b", 2]);
});

Deno.test("callerTurn: when every model fails the caller sees a plain 503", async () => {
  const net = fakeNetwork(() => bad(500, "boom"));
  const e = await assertRejects(() => callerTurn("k", MSGS, net.fn), HttpError);
  assertEquals([e.status, e.code], [503, "caller_unavailable"]);
  assert(!e.message.includes("boom") && !e.message.includes("gsk"));
});

Deno.test("transcribe: sends the audio, the Taglish prompt and the right model", async () => {
  let seen: FormData | null = null;
  let auth: string | null = null;
  const fn: typeof fetch = (_url, init) => {
    seen = init?.body as FormData;
    auth = new Headers(init?.headers).get("authorization");
    return Promise.resolve(new Response(JSON.stringify({ text: " Hello po, good day " }), { status: 200 }));
  };
  const r = await transcribe("gsk_test", new Blob([new Uint8Array(4000)], { type: "audio/wav" }), fn);
  assertEquals(r.text, "Hello po, good day");
  const form = seen as unknown as FormData;
  assertEquals([form.get("model"), form.get("prompt"), form.get("temperature")], ["whisper-large-v3", STT_PROMPT, "0"]);
  assert(form.get("file") instanceof File);
  assertEquals(auth, "Bearer gsk_test");
});

Deno.test("transcribe: waits out one rate limit, then reports a plain 503 on failure", async () => {
  let calls = 0;
  const flaky: typeof fetch = () => {
    calls++;
    return Promise.resolve(calls === 1 ? bad(429, "slow", { "retry-after": "0" }) : new Response(JSON.stringify({ text: "ok" }), { status: 200 }));
  };
  assertEquals((await transcribe("k", new Blob(["x"]), flaky)).text, "ok");
  const down: typeof fetch = () => Promise.resolve(bad(500, "boom"));
  const e = await assertRejects(() => transcribe("k", new Blob(["x"]), down), HttpError);
  assertEquals([e.status, e.code], [503, "transcription_unavailable"]);
});

// ------------------------------------------------------------------------------ loadTurnContext (fake admin)
import { loadTurnContext } from "../_shared/calls.ts";

function jwtFor(claims: Record<string, unknown>): string {
  const enc = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${enc({ alg: "HS256" })}.${enc(claims)}.sig`;
}
const CALL = "11111111-1111-1111-1111-111111111111";
function fakeAdmin(user: string | null, rpcData: unknown, seen: Record<string, unknown>[] = []) {
  return {
    auth: { getUser: (_jwt: string) => Promise.resolve(user ? { data: { user: { id: user } }, error: null } : { data: { user: null }, error: { message: "bad" } }) },
    rpc: (_name: string, args: Record<string, unknown>) => {
      seen.push(args);
      return Promise.resolve({ data: rpcData, error: null });
    },
  // deno-lint-ignore no-explicit-any
  } as any;
}
const reqWith = (jwt: string) => new Request("https://x.test", { headers: { authorization: `Bearer ${jwt}` } });
const opts = { callId: CALL, rateKey: (u: string) => `turn:${u}`, max: 40, full: true };

Deno.test("loadTurnContext: passes the verified user, session and rate key to the database", async () => {
  const seen: Record<string, unknown>[] = [];
  const data = { team_id: "t1", call: { id: CALL }, turns: [{ seq: 1, speaker: "caller", text: "Hi" }], scenario: {}, customer: {} };
  const ctx = await loadTurnContext(reqWith(jwtFor({ sub: "u1", session_id: "s1" })), fakeAdmin("u1", data, seen), opts);
  assertEquals(ctx.userId, "u1");
  assertEquals(ctx.turns.length, 1);
  assertEquals(seen[0].p_user, "u1");
  assertEquals(seen[0].p_session, "s1");
  assertEquals(seen[0].p_rate_key, "turn:u1");
});

Deno.test("loadTurnContext: an unverified token never reaches the database", async () => {
  const seen: Record<string, unknown>[] = [];
  await assertRejects(
    () => loadTurnContext(reqWith(jwtFor({ sub: "victim", session_id: "s1" })), fakeAdmin(null, {}, seen), opts),
    Error,
  );
  assertEquals(seen.length, 0);
});

Deno.test("loadTurnContext: database refusals become the right HTTP errors", async () => {
  for (const [code, status] of [["face_check_required", 403], ["call_not_active", 409], ["call_not_found", 404], ["too_many_requests", 429], ["unauthenticated", 401]] as const) {
    const err = await assertRejects(() =>
      loadTurnContext(reqWith(jwtFor({ sub: "u1", session_id: "s1" })), fakeAdmin("u1", { error: code, message: "m" }), opts)
    );
    assertEquals((err as { status?: number }).status, status);
  }
});
