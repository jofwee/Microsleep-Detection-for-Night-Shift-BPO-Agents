// One-time setup codes (enrollment and pairing): single use, short lived, stored only as hashes.
import type { Admin } from "./clients.ts";
import { formatCode, normalizeCode, randomCode, sha256, toPgBytea } from "./crypto.ts";
import { HttpError } from "./http.ts";

export type CodeKind = "enrollment" | "pairing";

export interface IssuedCode {
  code: string; // shown once, in the form ABCDE-FGHJK
  expires_at: string;
}

export async function issueCode(
  admin: Admin,
  args: { kind: CodeKind; teamId: string; agentId?: string; createdBy: string; minutes: number },
): Promise<IssuedCode> {
  const code = randomCode();
  const expires = new Date(Date.now() + args.minutes * 60_000).toISOString();
  const { error } = await admin.from("setup_codes").insert({
    kind: args.kind,
    team_id: args.teamId,
    agent_id: args.kind === "enrollment" ? args.agentId : null,
    code_hash: toPgBytea(await sha256(code)),
    created_by: args.createdBy,
    expires_at: expires,
  });
  if (error) throw new Error(`could not store the code: ${error.message}`);
  return { code: formatCode(code), expires_at: expires };
}

export interface RedeemedCode {
  id: string;
  team_id: string;
  agent_id: string | null;
  created_by: string;
}

/**
 * Marks the code used in the same statement that checks it (kind, unused, unexpired, and for
 * enrollment the right agent), so two simultaneous attempts cannot both succeed.
 */
export async function redeemCode(
  admin: Admin,
  args: { kind: CodeKind; code: string; agentId?: string; teamId?: string },
): Promise<RedeemedCode> {
  const hash = toPgBytea(await sha256(normalizeCode(args.code)));
  let q = admin
    .from("setup_codes")
    .update({ used_at: new Date().toISOString() })
    .eq("code_hash", hash)
    .eq("kind", args.kind)
    .is("used_at", null)
    .gt("expires_at", new Date().toISOString());
  if (args.agentId) q = q.eq("agent_id", args.agentId);
  if (args.teamId) q = q.eq("team_id", args.teamId);
  const { data, error } = await q.select("id, team_id, agent_id, created_by");
  if (error) throw new Error(`code redemption failed: ${error.message}`);
  if (!data || data.length !== 1) {
    throw new HttpError(400, "invalid_code", "that code is not valid, has expired, or was already used");
  }
  return data[0] as RedeemedCode;
}
