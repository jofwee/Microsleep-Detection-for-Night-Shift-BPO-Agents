// HTTP helpers shared by every Edge Function.

export const corsHeaders: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-workstation-token",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

/** An error the caller is allowed to see: a status, a stable code, and optional extra fields. */
export class HttpError extends Error {
  constructor(
    public status: number,
    public code: string,
    message?: string,
    public extra: Record<string, unknown> = {},
  ) {
    super(message ?? code);
  }
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

export function preflight(req: Request): Response | null {
  return req.method === "OPTIONS" ? new Response("ok", { headers: corsHeaders }) : null;
}

/** Known errors go to the caller; anything unexpected is logged and reported as a plain 500. */
export function errorResponse(e: unknown): Response {
  if (e instanceof HttpError) return json({ error: e.code, message: e.message, ...e.extra }, e.status);
  console.error("unhandled error:", e instanceof Error ? (e.stack ?? e.message) : String(e));
  return json({ error: "internal_error" }, 500);
}

export async function readJson(req: Request, maxBytes = 300_000): Promise<Record<string, unknown>> {
  if (req.method !== "POST") throw new HttpError(405, "method_not_allowed");
  const text = await req.text();
  if (text.length > maxBytes) throw new HttpError(413, "payload_too_large");
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new HttpError(400, "invalid_json");
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new HttpError(400, "invalid_json", "body must be a JSON object");
  }
  return value as Record<string, unknown>;
}

export function reqString(body: Record<string, unknown>, key: string, min = 1, max = 200): string {
  const v = body[key];
  if (typeof v !== "string" || v.length < min || v.length > max) {
    throw new HttpError(400, "invalid_field", `${key} must be a string of ${min}-${max} characters`);
  }
  return v;
}

export function reqNumber(body: Record<string, unknown>, key: string, min: number, max: number): number {
  const v = body[key];
  if (typeof v !== "number" || !Number.isFinite(v) || v < min || v > max) {
    throw new HttpError(400, "invalid_field", `${key} must be a number between ${min} and ${max}`);
  }
  return v;
}

export function optNumber(body: Record<string, unknown>, key: string, min: number, max: number): number | null {
  return body[key] === undefined || body[key] === null ? null : reqNumber(body, key, min, max);
}
