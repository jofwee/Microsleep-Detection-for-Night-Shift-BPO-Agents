// The admin (secret-key) client. It bypasses row level security, so it only ever runs on the
// server, and only after the caller has been checked (see auth.ts).
import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2";

export type Admin = SupabaseClient;

function secretKey(): string {
  // This platform injects the new-style keys as a JSON map; older projects have the legacy key.
  const map = Deno.env.get("SUPABASE_SECRET_KEYS");
  if (map) {
    try {
      const keys = JSON.parse(map) as Record<string, string>;
      const key = keys.default ?? Object.values(keys)[0];
      if (key) return key;
    } catch { /* fall through to the legacy key */ }
  }
  const legacy = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (legacy) return legacy;
  throw new Error("no secret key available in the function environment");
}

export function adminClient(): Admin {
  const url = Deno.env.get("SUPABASE_URL");
  if (!url) throw new Error("SUPABASE_URL is not set");
  return createClient(url, secretKey(), { auth: { persistSession: false, autoRefreshToken: false } });
}
