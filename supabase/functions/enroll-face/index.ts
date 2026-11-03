// Stores an agent's face template, encrypted, after they redeem a team leader's one-time
// enrollment code and give consent. The server never sees a photo, only embeddings.
import { audit } from "../_shared/audit.ts";
import { requireCaller, requireWorkstation } from "../_shared/auth.ts";
import { adminClient } from "../_shared/clients.ts";
import { redeemCode } from "../_shared/codes.ts";
import { templateKey } from "../_shared/config.ts";
import { encryptJson, toPgBytea } from "../_shared/crypto.ts";
import { enrollmentConsistent, meanTemplate, parseEmbeddings } from "../_shared/face.ts";
import { errorResponse, HttpError, json, optNumber, preflight, readJson, reqNumber, reqString } from "../_shared/http.ts";

function parseCalibration(v: unknown) {
  if (typeof v !== "object" || v === null) throw new HttpError(400, "invalid_field", "calibration is required");
  const o = v as Record<string, unknown>;
  const earOpen = reqNumber(o, "ear_open", 0.05, 0.9);
  const earClosed = reqNumber(o, "ear_closed", 0, 0.9);
  if (earOpen - earClosed < 0.03) {
    throw new HttpError(400, "invalid_field", "calibration: eyes-open and eyes-closed readings are too close");
  }
  return {
    ear_open: earOpen,
    ear_closed: earClosed,
    mar_closed: reqNumber(o, "mar_closed", 0, 3),
    mar_open: optNumber(o, "mar_open", 0, 5),
    neutral_pitch: reqNumber(o, "neutral_pitch", -90, 90),
    neutral_yaw: reqNumber(o, "neutral_yaw", -90, 90),
    neutral_roll: reqNumber(o, "neutral_roll", -90, 90),
  };
}

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  try {
    const body = await readJson(req);
    if (body["consent"] !== true) {
      throw new HttpError(400, "consent_required", "written consent must be given before a face is enrolled");
    }
    const code = reqString(body, "code", 6, 40);
    const modelName = reqString(body, "model_name", 1, 80);
    const modelVersion = reqString(body, "model_version", 1, 80);
    const embeddings = parseEmbeddings(body["embeddings"], 5, 8);
    const calibration = parseCalibration(body["calibration"]);
    if (!enrollmentConsistent(embeddings)) {
      throw new HttpError(400, "inconsistent_embeddings", "the captured faces do not look like one person");
    }

    const admin = adminClient();
    const caller = await requireCaller(req, admin, "agent");
    if (caller.profile.must_change_password) {
      throw new HttpError(403, "password_change_required", "set a new password before enrolling");
    }
    const ws = await requireWorkstation(req, admin, caller.profile.team_id);

    const { data: allowed, error: rlErr } = await admin.rpc("rate_limit_hit", {
      p_key: `enroll:${caller.userId}`,
      p_max: 5,
      p_window: "1 hour",
    });
    if (rlErr) throw new Error(`rate limiter failed: ${rlErr.message}`);
    if (!allowed) throw new HttpError(429, "too_many_attempts", "too many enrollment attempts");

    // after validation, so a bad request does not burn the code; before any write
    await redeemCode(admin, { kind: "enrollment", code, agentId: caller.userId, teamId: caller.profile.team_id });

    const template = { embeddings, mean: meanTemplate(embeddings) };
    const blob = await encryptJson(await templateKey(), template, `${caller.userId}:${modelVersion}`);
    const now = new Date().toISOString();

    const { error: tErr } = await admin.from("face_templates").upsert({
      agent_id: caller.userId,
      embeddings_enc: toPgBytea(blob),
      model_name: modelName,
      model_version: modelVersion,
      enrolled_at: now,
    });
    if (tErr) throw new Error(`could not store the template: ${tErr.message}`);

    const { error: cErr } = await admin
      .from("calibration_profiles")
      .upsert({ agent_id: caller.userId, team_id: caller.profile.team_id, ...calibration, updated_at: now });
    if (cErr) throw new Error(`could not store the calibration: ${cErr.message}`);

    await admin.from("profiles").update({ consent_at: now }).eq("id", caller.userId);
    // a new template invalidates face checks made against the old one
    await admin
      .from("face_verifications")
      .update({ revoked_at: now, revoked_reason: "re-enrolled" })
      .eq("agent_id", caller.userId)
      .is("revoked_at", null);

    await audit(admin, {
      teamId: caller.profile.team_id,
      actorId: caller.userId,
      action: "face_enrolled",
      targetId: caller.userId,
      detail: { model: modelName, version: modelVersion, frames: embeddings.length, workstation_id: ws.id },
    });
    return json({ enrolled: true });
  } catch (e) {
    return errorResponse(e);
  }
});
