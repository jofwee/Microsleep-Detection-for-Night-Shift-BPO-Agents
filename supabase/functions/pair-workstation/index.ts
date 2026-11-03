// Public endpoint: a PC trades a team leader's one-time pairing code for a workstation token.
// The token is stored only as a hash; the PC keeps the real one (Windows Credential Manager).
import { audit } from "../_shared/audit.ts";
import { adminClient } from "../_shared/clients.ts";
import { randomToken, sha256, toPgBytea } from "../_shared/crypto.ts";
import { redeemCode } from "../_shared/codes.ts";
import { errorResponse, HttpError, json, preflight, readJson, reqString } from "../_shared/http.ts";

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  try {
    const body = await readJson(req, 2_000);
    const code = reqString(body, "code", 6, 40);
    const name = body["name"] === undefined ? "workstation" : reqString(body, "name", 1, 80);
    const admin = adminClient();

    // Public and guessable in principle, so slow guessing down per caller address.
    const ip = (req.headers.get("x-forwarded-for") ?? "unknown").split(",")[0].trim();
    const { data: allowed, error: rlErr } = await admin.rpc("rate_limit_hit", {
      p_key: `pair:${ip}`,
      p_max: 10,
      p_window: "15 minutes",
    });
    if (rlErr) throw new Error(`rate limiter failed: ${rlErr.message}`);
    if (!allowed) throw new HttpError(429, "too_many_attempts", "too many attempts, try again later");

    const redeemed = await redeemCode(admin, { kind: "pairing", code });

    const token = randomToken(32);
    const { data: ws, error } = await admin
      .from("workstations")
      .insert({
        team_id: redeemed.team_id,
        name,
        token_hash: toPgBytea(await sha256(token)),
        paired_by: redeemed.created_by,
      })
      .select("id, team_id")
      .single();
    if (error) throw new Error(`could not create the workstation: ${error.message}`);

    await audit(admin, { teamId: ws.team_id, actorId: redeemed.created_by, action: "workstation_paired", targetId: ws.id, detail: { name } });
    return json({ workstation_id: ws.id, team_id: ws.team_id, token });
  } catch (e) {
    return errorResponse(e);
  }
});
