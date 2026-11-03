// Tunable settings, read from Edge Function secrets with safe defaults.
// Set with:  supabase secrets set FACE_TAU=0.45   (secret names cannot start with SUPABASE_)
import { importAesKey } from "./crypto.ts";

function num(name: string, fallback: number): number {
  const raw = Deno.env.get(name);
  const n = raw === undefined || raw === "" ? NaN : Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

export function settings() {
  return {
    tau: num("FACE_TAU", 0.45), // cosine threshold; tuned later with an ROC (FAR / FRR / EER)
    livenessMin: num("LIVENESS_MIN", 0.5), // median passive liveness score
    challengeSeconds: num("CHALLENGE_SECONDS", 60),
    verificationHours: num("VERIFICATION_HOURS", 12),
    codeMinutes: num("CODE_MINUTES", 15),
    maxFails: num("FACE_MAX_FAILS", 5),
    lockMinutes: num("FACE_LOCK_MINUTES", 15),
  };
}

let cachedKey: Promise<CryptoKey> | null = null;

/** The AES-256-GCM key for face templates (FACE_TEMPLATE_KEY, 32 random bytes, base64). */
export function templateKey(): Promise<CryptoKey> {
  const b64 = Deno.env.get("FACE_TEMPLATE_KEY");
  if (!b64) throw new Error("FACE_TEMPLATE_KEY is not set");
  return (cachedKey ??= importAesKey(b64));
}
