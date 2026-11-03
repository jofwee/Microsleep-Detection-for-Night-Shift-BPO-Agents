// The rules behind face sign-in, as pure functions so they can be tested without a database.
import { fromPgBytea, randomInt, sha256, timingSafeEqual } from "./crypto.ts";
import { median } from "./face.ts";

export const ACTIONS = ["BLINK_TWICE", "TURN_LEFT", "TURN_RIGHT"] as const;

/** n different actions in a random order. Unpredictable, so a pre-recorded video cannot match them. */
export function pickActions(n = 2): string[] {
  const pool: string[] = [...ACTIONS];
  const out: string[] = [];
  while (out.length < n && pool.length > 0) out.push(pool.splice(randomInt(pool.length), 1)[0]);
  return out;
}

/** Row of face_challenges as PostgREST returns it. */
export interface Challenge {
  id: string;
  agent_id: string;
  workstation_id: string;
  auth_session_id: string;
  nonce_hash: string; // bytea, "\\x..." hex
  actions: string[];
  expires_at: string;
  used_at: string | null;
}

export type ChallengeProblem = "wrong_owner" | "expired" | "used";

/** The challenge must belong to this agent, this login session and this PC, be unexpired and unused. */
export function checkChallenge(
  ch: Challenge,
  ctx: { agentId: string; sessionId: string; workstationId: string; now: Date },
): ChallengeProblem | null {
  if (ch.agent_id !== ctx.agentId || ch.auth_session_id !== ctx.sessionId || ch.workstation_id !== ctx.workstationId) {
    return "wrong_owner";
  }
  if (ch.used_at !== null) return "used";
  if (new Date(ch.expires_at).getTime() <= ctx.now.getTime()) return "expired";
  return null;
}

export async function nonceMatches(ch: Challenge, nonce: string): Promise<boolean> {
  return timingSafeEqual(fromPgBytea(ch.nonce_hash), await sha256(nonce));
}

export interface Liveness {
  passive: number[]; // passive anti-spoof scores, one per sampled frame (1 = real)
  challenge_passed: boolean; // the client says the blink/turn challenge was completed
}

export type Verdict = "ok" | "challenge_failed" | "liveness_low" | "no_match";

/** Order matters: report the first thing that is wrong. */
export function decide(args: { score: number; liveness: Liveness; tau: number; livenessMin: number }): Verdict {
  if (!args.liveness.challenge_passed) return "challenge_failed";
  if (args.liveness.passive.length === 0 || median(args.liveness.passive) < args.livenessMin) return "liveness_low";
  if (!(args.score >= args.tau)) return "no_match";
  return "ok";
}

// --- lockout -----------------------------------------------------------------------------
export interface Outcome {
  outcome: "passed" | "failed";
  used_at: string;
}

export interface LockState {
  locked: boolean;
  retryAfterSeconds: number;
  consecutiveFails: number;
}

/**
 * `recent` is the agent's latest face-check outcomes, newest first (the last `maxFails` are enough).
 * Locked when the newest `maxFails` all failed within `lockMinutes` of each other; the lock lasts
 * `lockMinutes` after the newest failure. A pass ends the streak.
 */
export function lockState(recent: Outcome[], now: Date, maxFails = 5, lockMinutes = 15): LockState {
  let streak = 0;
  for (const o of recent) {
    if (o.outcome !== "failed") break;
    streak++;
  }
  const none: LockState = { locked: false, retryAfterSeconds: 0, consecutiveFails: streak };
  if (streak < maxFails) return none;
  const newest = new Date(recent[0].used_at).getTime();
  const nth = new Date(recent[maxFails - 1].used_at).getTime();
  const span = lockMinutes * 60_000;
  if (newest - nth > span) return none; // the failures were spread out, not a burst
  const until = newest + span;
  if (now.getTime() >= until) return none;
  return { locked: true, retryAfterSeconds: Math.ceil((until - now.getTime()) / 1000), consecutiveFails: streak };
}
