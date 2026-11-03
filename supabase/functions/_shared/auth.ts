// Who is calling, and from which PC? Every function that is not public starts here.
import type { Admin } from "./clients.ts";
import { b64urlDecode, sha256, toPgBytea } from "./crypto.ts";
import { HttpError } from "./http.ts";

export interface Profile {
  id: string;
  role: "agent" | "team_leader";
  team_id: string;
  full_name: string;
  active: boolean;
  must_change_password: boolean;
}

export interface Caller {
  userId: string;
  sessionId: string | null; // the login session (JWT session_id claim)
  profile: Profile;
}

export function bearer(req: Request): string {
  const header = req.headers.get("authorization") ?? "";
  const m = /^Bearer\s+(\S+)$/i.exec(header);
  if (!m) throw new HttpError(401, "unauthenticated", "missing bearer token");
  return m[1];
}

export function claimsOf(jwt: string): Record<string, unknown> {
  try {
    return JSON.parse(new TextDecoder().decode(b64urlDecode(jwt.split(".")[1]))) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/**
 * Verifies the user's token with Supabase Auth (which also checks the session is still alive),
 * then loads their profile. Does not depend on the project's JWT signing-key type.
 */
export async function requireCaller(
  req: Request,
  admin: Admin,
  role: "agent" | "team_leader",
): Promise<Caller> {
  const jwt = bearer(req);
  const { data, error } = await admin.auth.getUser(jwt);
  if (error || !data?.user) throw new HttpError(401, "unauthenticated", "invalid or expired token");

  const { data: profile, error: pErr } = await admin
    .from("profiles")
    .select("id, role, team_id, full_name, active, must_change_password")
    .eq("id", data.user.id)
    .maybeSingle();
  if (pErr) throw new Error(`profile lookup failed: ${pErr.message}`);
  if (!profile || !profile.active) throw new HttpError(403, "forbidden", "no active account");
  if (profile.role !== role) throw new HttpError(403, "forbidden", `this action is for ${role}s`);

  const sessionId = claimsOf(jwt)["session_id"];
  return {
    userId: data.user.id,
    sessionId: typeof sessionId === "string" ? sessionId : null,
    profile: profile as Profile,
  };
}

export interface Workstation {
  id: string;
  team_id: string;
}

/** The PC must present the token it was given at pairing, and belong to the caller's team. */
export async function requireWorkstation(req: Request, admin: Admin, teamId: string): Promise<Workstation> {
  const token = req.headers.get("x-workstation-token") ?? "";
  if (token.length < 20 || token.length > 200) {
    throw new HttpError(403, "workstation_not_paired", "this PC is not paired");
  }
  const { data, error } = await admin
    .from("workstations")
    .select("id, team_id")
    .eq("token_hash", toPgBytea(await sha256(token)))
    .is("revoked_at", null)
    .maybeSingle();
  if (error) throw new Error(`workstation lookup failed: ${error.message}`);
  if (!data || data.team_id !== teamId) {
    throw new HttpError(403, "workstation_not_paired", "this PC is not paired to your team");
  }
  await admin.from("workstations").update({ last_seen_at: new Date().toISOString() }).eq("id", data.id);
  return data as Workstation;
}
