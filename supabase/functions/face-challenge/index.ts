// Step 1 of face sign-in: hand the PC a one-time nonce and two random actions to perform.
import { adminClient } from "../_shared/clients.ts";
import { requireCaller, requireWorkstation } from "../_shared/auth.ts";
import { settings } from "../_shared/config.ts";
import { lockState, pickActions } from "../_shared/decide.ts";
import { randomToken, sha256, toPgBytea } from "../_shared/crypto.ts";
import { errorResponse, HttpError, json, preflight, readJson } from "../_shared/http.ts";

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  try {
    await readJson(req, 1_000);
    const admin = adminClient();
    const cfg = settings();

    const caller = await requireCaller(req, admin, "agent");
    if (!caller.sessionId) throw new HttpError(401, "unauthenticated", "the token has no session");
    if (caller.profile.must_change_password) {
      throw new HttpError(403, "password_change_required", "set a new password before the face check");
    }
    const ws = await requireWorkstation(req, admin, caller.profile.team_id);

    // after repeated failures the face check is locked for a while
    const { data: recent, error: rErr } = await admin
      .from("face_challenges")
      .select("outcome, used_at")
      .eq("agent_id", caller.userId)
      .not("outcome", "is", null)
      .order("used_at", { ascending: false })
      .limit(cfg.maxFails);
    if (rErr) throw new Error(`lockout lookup failed: ${rErr.message}`);
    const lock = lockState(
      (recent ?? []).map((r) => ({ outcome: r.outcome as "passed" | "failed", used_at: r.used_at as string })),
      new Date(),
      cfg.maxFails,
      cfg.lockMinutes,
    );
    if (lock.locked) {
      throw new HttpError(429, "face_locked", "too many failed face checks; try again later", {
        retry_after_seconds: lock.retryAfterSeconds,
      });
    }

    const { data: tpl, error: tErr } = await admin
      .from("face_templates")
      .select("agent_id")
      .eq("agent_id", caller.userId)
      .maybeSingle();
    if (tErr) throw new Error(`template lookup failed: ${tErr.message}`);
    if (!tpl) throw new HttpError(409, "not_enrolled", "no face is enrolled for this account yet");

    const { data: allowed, error: rlErr } = await admin.rpc("rate_limit_hit", {
      p_key: `challenge:${caller.userId}`,
      p_max: 12,
      p_window: "1 minute",
    });
    if (rlErr) throw new Error(`rate limiter failed: ${rlErr.message}`);
    if (!allowed) throw new HttpError(429, "too_many_requests", "slow down");

    const nonce = randomToken(32);
    const actions = pickActions(2);
    const expires = new Date(Date.now() + cfg.challengeSeconds * 1000).toISOString();
    const { data: row, error } = await admin
      .from("face_challenges")
      .insert({
        agent_id: caller.userId,
        team_id: caller.profile.team_id,
        workstation_id: ws.id,
        auth_session_id: caller.sessionId,
        nonce_hash: toPgBytea(await sha256(nonce)),
        actions,
        expires_at: expires,
      })
      .select("id, expires_at")
      .single();
    if (error) throw new Error(`could not create the challenge: ${error.message}`);

    return json({ challenge_id: row.id, nonce, actions, expires_at: row.expires_at });
  } catch (e) {
    return errorResponse(e);
  }
});
