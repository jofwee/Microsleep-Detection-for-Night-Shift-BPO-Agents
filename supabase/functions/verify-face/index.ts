// Step 2 of face sign-in: check the evidence and, if it holds, record a face verification
// for THIS login session. Row level security then lets the agent write data.
//
// What the server can and cannot know: embeddings and liveness scores are computed on the PC,
// so a modified client could lie. The server enforces what it can: a fresh single-use nonce,
// the right agent, session and PC, a match against the encrypted template, and lockout.
import { audit } from "../_shared/audit.ts";
import { requireCaller, requireWorkstation } from "../_shared/auth.ts";
import { adminClient } from "../_shared/clients.ts";
import { settings, templateKey } from "../_shared/config.ts";
import { fromPgBytea } from "../_shared/crypto.ts";
import { checkChallenge, type Challenge, decide, type Liveness, lockState, nonceMatches } from "../_shared/decide.ts";
import { decryptJson } from "../_shared/crypto.ts";
import { matchScore, parseEmbeddings, type Template } from "../_shared/face.ts";
import { errorResponse, HttpError, json, preflight, readJson, reqString } from "../_shared/http.ts";

function parseLiveness(v: unknown): Liveness {
  const o = (typeof v === "object" && v !== null ? v : {}) as Record<string, unknown>;
  const passive = o["passive"];
  if (!Array.isArray(passive) || passive.length > 40 || passive.some((x) => typeof x !== "number" || !(x >= 0 && x <= 1))) {
    throw new HttpError(400, "invalid_field", "liveness.passive must be up to 40 numbers between 0 and 1");
  }
  if (typeof o["challenge_passed"] !== "boolean") {
    throw new HttpError(400, "invalid_field", "liveness.challenge_passed must be true or false");
  }
  return { passive: passive as number[], challenge_passed: o["challenge_passed"] };
}

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  try {
    const body = await readJson(req);
    const challengeId = reqString(body, "challenge_id", 36, 36);
    const nonce = reqString(body, "nonce", 20, 100);
    const liveness = parseLiveness(body["liveness"]);
    const embeddings = parseEmbeddings(body["embeddings"], 3, 12);

    const admin = adminClient();
    const cfg = settings();
    const caller = await requireCaller(req, admin, "agent");
    if (!caller.sessionId) throw new HttpError(401, "unauthenticated", "the token has no session");
    const ws = await requireWorkstation(req, admin, caller.profile.team_id);

    const recentOutcomes = async () => {
      const { data, error } = await admin
        .from("face_challenges")
        .select("outcome, used_at")
        .eq("agent_id", caller.userId)
        .not("outcome", "is", null)
        .order("used_at", { ascending: false })
        .limit(cfg.maxFails);
      if (error) throw new Error(`lockout lookup failed: ${error.message}`);
      return (data ?? []).map((r) => ({ outcome: r.outcome as "passed" | "failed", used_at: r.used_at as string }));
    };

    const lock = lockState(await recentOutcomes(), new Date(), cfg.maxFails, cfg.lockMinutes);
    if (lock.locked) {
      throw new HttpError(429, "face_locked", "too many failed face checks; try again later", {
        retry_after_seconds: lock.retryAfterSeconds,
      });
    }

    // the challenge: must exist, belong to this agent + login + PC, be fresh, and the nonce must match
    const { data: ch, error: cErr } = await admin.from("face_challenges").select("*").eq("id", challengeId).maybeSingle();
    if (cErr) throw new Error(`challenge lookup failed: ${cErr.message}`);
    if (!ch) throw new HttpError(404, "challenge_not_found");
    const problem = checkChallenge(ch as Challenge, {
      agentId: caller.userId,
      sessionId: caller.sessionId,
      workstationId: ws.id,
      now: new Date(),
    });
    if (problem === "wrong_owner") throw new HttpError(403, "challenge_mismatch");
    if (problem === "used") throw new HttpError(409, "challenge_used", "start a new face check");
    if (problem === "expired") throw new HttpError(410, "challenge_expired", "start a new face check");
    if (!(await nonceMatches(ch as Challenge, nonce))) throw new HttpError(403, "bad_nonce");

    // single use: claim it before doing anything else (the UPDATE succeeds for one caller only)
    const { data: claimed, error: uErr } = await admin
      .from("face_challenges")
      .update({ used_at: new Date().toISOString() })
      .eq("id", challengeId)
      .is("used_at", null)
      .select("id");
    if (uErr) throw new Error(`could not claim the challenge: ${uErr.message}`);
    if (!claimed || claimed.length !== 1) throw new HttpError(409, "challenge_used", "start a new face check");

    const { data: tpl, error: tErr } = await admin
      .from("face_templates")
      .select("embeddings_enc, model_version")
      .eq("agent_id", caller.userId)
      .maybeSingle();
    if (tErr) throw new Error(`template lookup failed: ${tErr.message}`);
    if (!tpl) throw new HttpError(409, "not_enrolled");
    const template = await decryptJson<Template>(
      await templateKey(),
      fromPgBytea(tpl.embeddings_enc as string),
      `${caller.userId}:${tpl.model_version}`,
    );

    const score = matchScore(template, embeddings);
    const verdict = decide({ score, liveness, tau: cfg.tau, livenessMin: cfg.livenessMin });
    await admin
      .from("face_challenges")
      .update({ outcome: verdict === "ok" ? "passed" : "failed", reason: verdict, score })
      .eq("id", challengeId);

    if (verdict !== "ok") {
      const after = lockState(await recentOutcomes(), new Date(), cfg.maxFails, cfg.lockMinutes);
      if (after.locked && after.consecutiveFails === cfg.maxFails) {
        await admin.from("alerts").insert({
          team_id: caller.profile.team_id,
          agent_id: caller.userId,
          kind: "FACE_LOCKOUT",
          message: `${cfg.maxFails} failed face checks in a row. Face sign-in is locked for ${cfg.lockMinutes} minutes.`,
        });
      }
      await audit(admin, { teamId: caller.profile.team_id, actorId: caller.userId, action: "face_check_failed", detail: { reason: verdict } });
      // the score is not returned on failure: it would help someone tune an attack
      throw new HttpError(403, "verification_failed", "the face check did not pass", { reason: verdict });
    }

    const expires = new Date(Date.now() + cfg.verificationHours * 3600_000).toISOString();
    const { error: vErr } = await admin.from("face_verifications").insert({
      agent_id: caller.userId,
      team_id: caller.profile.team_id,
      auth_session_id: caller.sessionId,
      workstation_id: ws.id,
      score,
      liveness_ok: true,
      expires_at: expires,
    });
    if (vErr) throw new Error(`could not record the verification: ${vErr.message}`);
    await audit(admin, { teamId: caller.profile.team_id, actorId: caller.userId, action: "face_verified", detail: { workstation_id: ws.id } });

    return json({ verified: true, expires_at: expires, score });
  } catch (e) {
    return errorResponse(e);
  }
});
