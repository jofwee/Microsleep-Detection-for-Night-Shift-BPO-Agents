// Unit tests for the shared server logic. Run:  deno test supabase/functions/tests
import { assert, assertAlmostEquals, assertEquals, assertFalse, assertRejects, assertThrows } from "jsr:@std/assert@1";
import {
  b64urlDecode,
  b64urlEncode,
  decryptJson,
  encryptJson,
  formatCode,
  fromHex,
  fromPgBytea,
  importAesKey,
  normalizeCode,
  randomBytes,
  randomCode,
  randomInt,
  randomPassword,
  sha256,
  timingSafeEqual,
  toHex,
  toPgBytea,
} from "../_shared/crypto.ts";
import { ACTIONS, checkChallenge, type Challenge, decide, lockState, nonceMatches, type Outcome, pickActions } from "../_shared/decide.ts";
import { DIM, enrollmentConsistent, matchScore, meanTemplate, normalise, parseEmbedding, parseEmbeddings, scoreOne, type Template } from "../_shared/face.ts";
import { HttpError, readJson, reqNumber, reqString } from "../_shared/http.ts";

// ------------------------------------------------------------------------------ helpers
let seed = 12345;
const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32) - 0.5;
const randomUnit = () => normalise(Array.from({ length: DIM }, rnd));
const near = (base: number[], noise: number) => normalise(base.map((x) => x + noise * rnd()));
const dotp = (a: number[], b: number[]) => a.reduce((s, x, i) => s + x * b[i], 0);

// ------------------------------------------------------------------------------ crypto
Deno.test("hex and bytea round-trip, and bad input is refused", () => {
  const b = new Uint8Array([0, 1, 254, 255]);
  assertEquals(toHex(b), "0001feff");
  assertEquals(fromHex("0001feff"), b);
  assertEquals(toPgBytea(b), "\\x0001feff");
  assertEquals(fromPgBytea("\\x0001feff"), b);
  assertThrows(() => fromHex("abc"));
  assertThrows(() => fromHex("zz"));
  assertThrows(() => fromPgBytea("0001"));
});

Deno.test("sha256 matches the known test vector", async () => {
  assertEquals(toHex(await sha256("abc")), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
});

Deno.test("base64url round-trips", () => {
  const b = randomBytes(33);
  assertEquals(b64urlDecode(b64urlEncode(b)), b);
});

const keyB64 = btoa(String.fromCharCode(...randomBytes(32)));

Deno.test("AES-GCM: round trip, fresh IV each time", async () => {
  const key = await importAesKey(keyB64);
  const value = { embeddings: [[0.1, 0.2], [0.3, 0.4]], mean: [0.5, 0.6] };
  const a = await encryptJson(key, value, "agent-1:v1");
  const b = await encryptJson(key, value, "agent-1:v1");
  assertEquals(await decryptJson(key, a, "agent-1:v1"), value);
  assert(toHex(a) !== toHex(b), "two encryptions of the same data must differ");
});

Deno.test("AES-GCM: a tampered blob, the wrong owner (AAD) and the wrong key are all refused", async () => {
  const key = await importAesKey(keyB64);
  const blob = await encryptJson(key, { secret: 1 }, "agent-1:v1");
  const tampered = blob.slice();
  tampered[20] ^= 1;
  await assertRejects(() => decryptJson(key, tampered, "agent-1:v1"));
  await assertRejects(() => decryptJson(key, blob, "agent-2:v1"), Error, "", "copied onto another agent");
  await assertRejects(() => decryptJson(key, blob, "agent-1:v2"), Error, "", "different model version");
  const other = await importAesKey(btoa(String.fromCharCode(...randomBytes(32))));
  await assertRejects(() => decryptJson(other, blob, "agent-1:v1"));
  await assertRejects(() => decryptJson(key, new Uint8Array(10), "x"));
});

Deno.test("the AES key must be exactly 32 bytes", async () => {
  await assertRejects(() => importAesKey(btoa("too short")));
});

Deno.test("codes use the safe alphabet and survive formatting", () => {
  for (let i = 0; i < 200; i++) {
    const c = randomCode();
    assertEquals(c.length, 10);
    assert(/^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]+$/.test(c), c);
    assertEquals(normalizeCode(formatCode(c)), c);
  }
  assertEquals(normalizeCode(" abcde-fghjk "), "ABCDEFGHJK");
  assertEquals(formatCode("ABCDEFGHJK"), "ABCDE-FGHJK");
  assertEquals(new Set(Array.from({ length: 50 }, () => randomCode())).size, 50);
});

Deno.test("randomInt is in range and roughly uniform", () => {
  const counts = [0, 0, 0];
  for (let i = 0; i < 3000; i++) counts[randomInt(3)]++;
  for (const c of counts) assert(c > 800 && c < 1200, `counts ${counts}`);
  assertThrows(() => randomInt(0));
});

Deno.test("temporary passwords meet the policy (12+, upper, lower, digit)", () => {
  for (let i = 0; i < 300; i++) {
    const p = randomPassword();
    assertEquals(p.length, 16);
    assert(/[A-Z]/.test(p) && /[a-z]/.test(p) && /[0-9]/.test(p), p);
  }
});

Deno.test("timingSafeEqual", () => {
  assert(timingSafeEqual(new Uint8Array([1, 2]), new Uint8Array([1, 2])));
  assertFalse(timingSafeEqual(new Uint8Array([1, 2]), new Uint8Array([1, 3])));
  assertFalse(timingSafeEqual(new Uint8Array([1, 2]), new Uint8Array([1, 2, 3])));
});

// ------------------------------------------------------------------------------ face
Deno.test("parseEmbedding accepts a unit vector and refuses everything else", () => {
  const v = randomUnit();
  assertEquals(parseEmbedding(v).length, DIM);
  assertThrows(() => parseEmbedding(v.slice(1)), HttpError);
  assertThrows(() => parseEmbedding("nope"), HttpError);
  assertThrows(() => parseEmbedding(v.map((x) => x * 2)), HttpError, "L2-normalised");
  assertThrows(() => parseEmbedding(v.map((x) => x * 0)), HttpError);
  const withNaN = [...v];
  withNaN[3] = NaN;
  assertThrows(() => parseEmbedding(withNaN), HttpError);
  const withText = [...v] as unknown[];
  withText[3] = "0.1";
  assertThrows(() => parseEmbedding(withText), HttpError);
});

Deno.test("parseEmbeddings enforces the count", () => {
  const v = randomUnit();
  assertEquals(parseEmbeddings([v, v, v], 3, 12).length, 3);
  assertThrows(() => parseEmbeddings([v, v], 3, 12), HttpError);
  assertThrows(() => parseEmbeddings(Array(13).fill(v), 3, 12), HttpError);
  assertThrows(() => parseEmbeddings("x", 3, 12), HttpError);
});

const agent = randomUnit();
const template: Template = { embeddings: Array.from({ length: 5 }, () => near(agent, 0.05)), mean: [] };
template.mean = meanTemplate(template.embeddings);

Deno.test("the mean template has unit length", () => {
  assertAlmostEquals(dotp(template.mean, template.mean), 1, 1e-9);
});

Deno.test("scores: the agent is high, a stranger is low", () => {
  assert(scoreOne(template, near(agent, 0.05)) > 0.8);
  assert(scoreOne(template, randomUnit()) < 0.3);
});

Deno.test("matchScore: one lucky frame cannot pass, one bad frame cannot fail", () => {
  const good = Array.from({ length: 6 }, () => near(agent, 0.05));
  const strangers = Array.from({ length: 6 }, () => randomUnit());
  assert(matchScore(template, good) > 0.8);
  assert(matchScore(template, strangers) < 0.45);
  assert(matchScore(template, [...strangers.slice(0, 5), good[0]]) < 0.45);
  assert(matchScore(template, [...good.slice(0, 5), strangers[0]]) > 0.8);
});

Deno.test("enrollment consistency catches a stranger in the capture", () => {
  const same = Array.from({ length: 20 }, () => near(agent, 0.05));
  assert(enrollmentConsistent(same));
  assertFalse(enrollmentConsistent([...same.slice(0, 19), randomUnit()]));
  assertFalse(enrollmentConsistent([...same.slice(0, 11), ...Array.from({ length: 9 }, () => randomUnit())]));
  assertFalse(enrollmentConsistent([agent]));
});

// ------------------------------------------------------------------------------ decide
const NOW = new Date("2026-11-03T12:00:00Z");
const challenge = async (over: Partial<Challenge> = {}): Promise<Challenge> => ({
  id: "c1",
  agent_id: "agent",
  workstation_id: "ws",
  auth_session_id: "sess",
  nonce_hash: toPgBytea(await sha256("the-nonce")),
  actions: ["BLINK_TWICE", "TURN_LEFT"],
  expires_at: new Date(NOW.getTime() + 30_000).toISOString(),
  used_at: null,
  ...over,
});
const ctx = { agentId: "agent", sessionId: "sess", workstationId: "ws", now: NOW };

Deno.test("checkChallenge: only the right agent, login session and PC, fresh and unused", async () => {
  assertEquals(checkChallenge(await challenge(), ctx), null);
  assertEquals(checkChallenge(await challenge({ agent_id: "other" }), ctx), "wrong_owner");
  assertEquals(checkChallenge(await challenge({ auth_session_id: "other-login" }), ctx), "wrong_owner");
  assertEquals(checkChallenge(await challenge({ workstation_id: "other-pc" }), ctx), "wrong_owner");
  assertEquals(checkChallenge(await challenge({ used_at: NOW.toISOString() }), ctx), "used");
  assertEquals(checkChallenge(await challenge({ expires_at: new Date(NOW.getTime() - 1).toISOString() }), ctx), "expired");
  assertEquals(checkChallenge(await challenge({ expires_at: NOW.toISOString() }), ctx), "expired", "the boundary counts as expired");
});

Deno.test("nonceMatches", async () => {
  const ch = await challenge();
  assert(await nonceMatches(ch, "the-nonce"));
  assertFalse(await nonceMatches(ch, "another-nonce"));
  assertFalse(await nonceMatches(ch, ""));
});

Deno.test("decide reports the first failure, in order", () => {
  const live = { passive: [0.99, 0.98, 1], challenge_passed: true };
  const base = { score: 0.8, liveness: live, tau: 0.45, livenessMin: 0.5 };
  assertEquals(decide(base), "ok");
  assertEquals(decide({ ...base, liveness: { ...live, challenge_passed: false }, score: 0.1 }), "challenge_failed");
  assertEquals(decide({ ...base, liveness: { passive: [], challenge_passed: true } }), "liveness_low");
  assertEquals(decide({ ...base, liveness: { passive: [0.01, 0.02, 0.9], challenge_passed: true } }), "liveness_low");
  assertEquals(decide({ ...base, score: 0.2 }), "no_match");
  assertEquals(decide({ ...base, score: 0.45 }), "ok", "exactly tau passes");
  assertEquals(decide({ ...base, score: 0.4499 }), "no_match");
  assertEquals(decide({ ...base, score: NaN }), "no_match");
});

const at = (min: number) => new Date(NOW.getTime() - min * 60_000).toISOString();
const failed = (min: number): Outcome => ({ outcome: "failed", used_at: at(min) });
const passed = (min: number): Outcome => ({ outcome: "passed", used_at: at(min) });

Deno.test("lockState: five failures in a burst lock for 15 minutes after the last one", () => {
  assertEquals(lockState([], NOW).locked, false);
  assertEquals(lockState([failed(1), failed(2), failed(3), failed(4)], NOW).locked, false);
  const burst = [failed(1), failed(2), failed(3), failed(4), failed(5)];
  const s = lockState(burst, NOW);
  assert(s.locked);
  assertEquals(s.consecutiveFails, 5);
  assertEquals(s.retryAfterSeconds, 14 * 60, "15 minutes after the newest failure, which was 1 minute ago");
  assertFalse(lockState(burst, new Date(NOW.getTime() + 14 * 60_000 + 1000)).locked, "lock has expired");
});

Deno.test("lockState: failures spread over more than 15 minutes do not lock, and a pass ends the streak", () => {
  assertFalse(lockState([failed(1), failed(6), failed(11), failed(16), failed(21)], NOW).locked);
  const s = lockState([failed(1), failed(2), failed(3), passed(4), failed(5)], NOW);
  assertEquals(s.consecutiveFails, 3);
  assertFalse(s.locked);
  assertEquals(lockState([passed(1), failed(2), failed(3), failed(4), failed(5)], NOW).consecutiveFails, 0);
});

Deno.test("pickActions: distinct, valid, and in varying order", () => {
  const seen = new Set<string>();
  for (let i = 0; i < 300; i++) {
    const a = pickActions(2);
    assertEquals(a.length, 2);
    assert(a[0] !== a[1]);
    for (const x of a) assert((ACTIONS as readonly string[]).includes(x));
    seen.add(a.join(","));
  }
  assertEquals(seen.size, 6, "all 6 ordered pairs occur");
  assertEquals(new Set(pickActions(3)).size, 3);
  assertEquals(pickActions(9).length, 3, "cannot ask for more than exist");
});

// ------------------------------------------------------------------------------ http
const post = (body: string) => new Request("https://x.test/f", { method: "POST", body });

Deno.test("readJson accepts an object and refuses everything else", async () => {
  assertEquals(await readJson(post('{"a":1}')), { a: 1 });
  for (const bad of ["not json", "[1,2]", "null", '"str"', "42"]) {
    const e = await assertRejects(() => readJson(post(bad)), HttpError);
    assertEquals(e.status, 400);
  }
  const big = await assertRejects(() => readJson(post("x".repeat(500)), 100), HttpError);
  assertEquals(big.status, 413);
  const get = await assertRejects(() => readJson(new Request("https://x.test/f")), HttpError);
  assertEquals(get.status, 405);
});

Deno.test("field validation", () => {
  assertEquals(reqString({ k: "abc" }, "k", 1, 5), "abc");
  assertThrows(() => reqString({ k: "abcdef" }, "k", 1, 5), HttpError);
  assertThrows(() => reqString({ k: 5 }, "k"), HttpError);
  assertThrows(() => reqString({}, "k"), HttpError);
  assertEquals(reqNumber({ k: 0.5 }, "k", 0, 1), 0.5);
  assertThrows(() => reqNumber({ k: 2 }, "k", 0, 1), HttpError);
  assertThrows(() => reqNumber({ k: NaN }, "k", 0, 1), HttpError);
  assertThrows(() => reqNumber({ k: "0.5" }, "k", 0, 1), HttpError);
});
