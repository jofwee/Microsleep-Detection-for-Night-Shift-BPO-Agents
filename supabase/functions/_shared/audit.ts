// Who did what: one row per account or biometric action, written with the secret key.
import type { Admin } from "./clients.ts";

export async function audit(
  admin: Admin,
  entry: { teamId: string | null; actorId: string | null; action: string; targetId?: string | null; detail?: Record<string, unknown> },
): Promise<void> {
  const { error } = await admin.from("audit_log").insert({
    team_id: entry.teamId,
    actor_id: entry.actorId,
    action: entry.action,
    target_id: entry.targetId ?? null,
    detail: entry.detail ?? {},
  });
  // An audit failure must not undo the action that already happened, but it must be visible.
  if (error) console.error("audit_log insert failed:", error.message);
}
