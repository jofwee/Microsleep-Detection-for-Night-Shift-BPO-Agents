// Team-leader administration: create and reset agent accounts, deactivate them, issue one-time
// enrollment and pairing codes, and delete an agent's biometric data. Every action is scoped to
// the caller's own team and written to the audit log.
import { audit } from "../_shared/audit.ts";
import { requireCaller } from "../_shared/auth.ts";
import { type Admin, adminClient } from "../_shared/clients.ts";
import { issueCode } from "../_shared/codes.ts";
import { settings } from "../_shared/config.ts";
import { randomPassword } from "../_shared/crypto.ts";
import { errorResponse, HttpError, json, preflight, readJson, reqString } from "../_shared/http.ts";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

async function agentInTeam(admin: Admin, agentId: string, teamId: string) {
  const { data, error } = await admin
    .from("profiles")
    .select("id, full_name, active")
    .eq("id", agentId)
    .eq("team_id", teamId)
    .eq("role", "agent")
    .maybeSingle();
  if (error) throw new Error(`agent lookup failed: ${error.message}`);
  if (!data) throw new HttpError(404, "agent_not_found", "no such agent in your team");
  return data as { id: string; full_name: string; active: boolean };
}

async function revokeFaceChecks(admin: Admin, agentId: string, reason: string) {
  await admin
    .from("face_verifications")
    .update({ revoked_at: new Date().toISOString(), revoked_reason: reason })
    .eq("agent_id", agentId)
    .is("revoked_at", null);
}

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  try {
    const body = await readJson(req, 5_000);
    const action = reqString(body, "action", 1, 40);
    const admin = adminClient();
    const cfg = settings();
    const leader = await requireCaller(req, admin, "team_leader");
    const teamId = leader.profile.team_id;
    const { data: team } = await admin.from("teams").select("leader_id").eq("id", teamId).maybeSingle();
    if (team?.leader_id !== leader.userId) throw new HttpError(403, "forbidden", "you do not lead this team");

    const { data: allowed } = await admin.rpc("rate_limit_hit", { p_key: `leader:${leader.userId}`, p_max: 60, p_window: "1 minute" });
    if (!allowed) throw new HttpError(429, "too_many_requests", "slow down");

    switch (action) {
      case "create_agent": {
        const email = reqString(body, "email", 5, 200).trim().toLowerCase();
        const fullName = reqString(body, "full_name", 1, 120).trim();
        if (!EMAIL.test(email)) throw new HttpError(400, "invalid_field", "email looks wrong");
        const password = randomPassword();
        const { data, error } = await admin.auth.admin.createUser({
          email,
          password,
          email_confirm: true,
          user_metadata: { full_name: fullName },
        });
        if (error || !data.user) {
          if (error && /already|exists|registered/i.test(error.message)) throw new HttpError(409, "email_taken");
          throw new Error(`could not create the user: ${error?.message}`);
        }
        const { error: pErr } = await admin.from("profiles").insert({
          id: data.user.id,
          role: "agent",
          team_id: teamId,
          full_name: fullName,
          must_change_password: true,
        });
        if (pErr) {
          await admin.auth.admin.deleteUser(data.user.id); // do not leave a half-made account behind
          throw new Error(`could not create the profile: ${pErr.message}`);
        }
        await audit(admin, { teamId, actorId: leader.userId, action: "agent_created", targetId: data.user.id, detail: { email } });
        return json({ agent_id: data.user.id, email, temp_password: password });
      }

      case "reset_password": {
        const agentId = reqString(body, "agent_id", 36, 36);
        await agentInTeam(admin, agentId, teamId);
        const password = randomPassword();
        const { error } = await admin.auth.admin.updateUserById(agentId, { password });
        if (error) throw new Error(`could not reset the password: ${error.message}`);
        await admin.from("profiles").update({ must_change_password: true }).eq("id", agentId);
        await revokeFaceChecks(admin, agentId, "password reset");
        await audit(admin, { teamId, actorId: leader.userId, action: "password_reset", targetId: agentId });
        return json({ agent_id: agentId, temp_password: password });
      }

      case "set_active": {
        const agentId = reqString(body, "agent_id", 36, 36);
        if (typeof body["active"] !== "boolean") throw new HttpError(400, "invalid_field", "active must be true or false");
        const active = body["active"];
        await agentInTeam(admin, agentId, teamId);
        const { error } = await admin.auth.admin.updateUserById(agentId, { ban_duration: active ? "none" : "876000h" });
        if (error) throw new Error(`could not update the account: ${error.message}`);
        await admin.from("profiles").update({ active }).eq("id", agentId);
        if (!active) await revokeFaceChecks(admin, agentId, "deactivated");
        await audit(admin, { teamId, actorId: leader.userId, action: active ? "agent_activated" : "agent_deactivated", targetId: agentId });
        return json({ agent_id: agentId, active });
      }

      case "issue_enrollment_code": {
        const agentId = reqString(body, "agent_id", 36, 36);
        const agent = await agentInTeam(admin, agentId, teamId);
        if (!agent.active) throw new HttpError(409, "agent_inactive");
        const issued = await issueCode(admin, { kind: "enrollment", teamId, agentId, createdBy: leader.userId, minutes: cfg.codeMinutes });
        await audit(admin, { teamId, actorId: leader.userId, action: "enrollment_code_issued", targetId: agentId });
        return json({ agent_id: agentId, ...issued });
      }

      case "issue_pairing_code": {
        const issued = await issueCode(admin, { kind: "pairing", teamId, createdBy: leader.userId, minutes: cfg.codeMinutes });
        await audit(admin, { teamId, actorId: leader.userId, action: "pairing_code_issued" });
        return json(issued);
      }

      case "delete_biometrics": {
        const agentId = reqString(body, "agent_id", 36, 36);
        await agentInTeam(admin, agentId, teamId);
        await admin.from("face_templates").delete().eq("agent_id", agentId);
        await admin.from("calibration_profiles").delete().eq("agent_id", agentId);
        await revokeFaceChecks(admin, agentId, "biometrics deleted");
        await admin.from("profiles").update({ consent_at: null }).eq("id", agentId);
        await audit(admin, { teamId, actorId: leader.userId, action: "biometrics_deleted", targetId: agentId });
        return json({ agent_id: agentId, deleted: true });
      }

      default:
        throw new HttpError(400, "unknown_action", `unknown action: ${action}`);
    }
  } catch (e) {
    return errorResponse(e);
  }
});
