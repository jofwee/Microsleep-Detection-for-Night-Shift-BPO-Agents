// Hashing, random codes, and AES-256-GCM for face templates. Web Crypto only, no dependencies.

const enc = new TextEncoder();
const dec = new TextDecoder();

export function toHex(b: Uint8Array): string {
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

export function fromHex(h: string): Uint8Array<ArrayBuffer> {
  if (h.length % 2 !== 0 || /[^0-9a-fA-F]/.test(h)) throw new Error("invalid hex");
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.slice(i * 2, i * 2 + 2), 16);
  return out;
}

/** Postgres bytea in PostgREST's hex form, e.g. "\\x0a0b". */
export const toPgBytea = (b: Uint8Array): string => "\\x" + toHex(b);

export function fromPgBytea(s: string): Uint8Array<ArrayBuffer> {
  if (!s.startsWith("\\x")) throw new Error("not a hex bytea");
  return fromHex(s.slice(2));
}

export function b64urlEncode(b: Uint8Array): string {
  let s = "";
  for (const x of b) s += String.fromCharCode(x);
  return btoa(s).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

export function b64urlDecode(s: string): Uint8Array<ArrayBuffer> {
  const padded = s.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - (s.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

export function b64Decode(s: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}

export async function sha256(input: string | Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
  const data = typeof input === "string" ? enc.encode(input) : input;
  return new Uint8Array(await crypto.subtle.digest("SHA-256", data));
}

export function randomBytes(n: number): Uint8Array<ArrayBuffer> {
  return crypto.getRandomValues(new Uint8Array(n));
}

export function randomToken(n = 32): string {
  return b64urlEncode(randomBytes(n));
}

/** Uniform integer in [0, n) by rejection sampling (no modulo bias). */
export function randomInt(n: number): number {
  if (n < 1 || n > 256) throw new Error("randomInt supports 1..256");
  const limit = 256 - (256 % n);
  for (;;) {
    const x = randomBytes(1)[0];
    if (x < limit) return x % n;
  }
}

// No 0/O/1/I/L: codes get read aloud and typed by hand.
const CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"; // 31 symbols
export const CODE_LENGTH = 10;

export function randomCode(len = CODE_LENGTH): string {
  let s = "";
  for (let i = 0; i < len; i++) s += CODE_ALPHABET[randomInt(CODE_ALPHABET.length)];
  return s;
}

export const formatCode = (code: string): string => `${code.slice(0, 5)}-${code.slice(5)}`;

/** What the person typed -> what is stored: upper case, no spaces or dashes. */
export const normalizeCode = (input: string): string => input.toUpperCase().replace(/[^A-Z0-9]/g, "");

const PW_UPPER = "ABCDEFGHJKMNPQRSTUVWXYZ";
const PW_LOWER = "abcdefghijkmnpqrstuvwxyz";
const PW_DIGIT = "23456789";

/** Temporary password: 16 characters with at least one upper, lower and digit. */
export function randomPassword(len = 16): string {
  const all = PW_UPPER + PW_LOWER + PW_DIGIT;
  const chars = [
    PW_UPPER[randomInt(PW_UPPER.length)],
    PW_LOWER[randomInt(PW_LOWER.length)],
    PW_DIGIT[randomInt(PW_DIGIT.length)],
  ];
  while (chars.length < len) chars.push(all[randomInt(all.length)]);
  for (let i = chars.length - 1; i > 0; i--) { // Fisher-Yates shuffle
    const j = randomInt(i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}

export function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

// --- AES-256-GCM -------------------------------------------------------------------------
export async function importAesKey(b64: string): Promise<CryptoKey> {
  const raw = b64Decode(b64);
  if (raw.length !== 32) throw new Error("FACE_TEMPLATE_KEY must be 32 bytes, base64 encoded");
  return await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

/** iv (12 bytes) followed by ciphertext+tag. `aad` binds the blob to its owner, so a template
 *  copied onto another agent's row fails to decrypt. */
export async function encryptJson(key: CryptoKey, value: unknown, aad: string): Promise<Uint8Array<ArrayBuffer>> {
  const iv = randomBytes(12);
  const ct = new Uint8Array(
    await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: enc.encode(aad) }, key, enc.encode(JSON.stringify(value))),
  );
  const out = new Uint8Array(iv.length + ct.length);
  out.set(iv);
  out.set(ct, iv.length);
  return out;
}

export async function decryptJson<T>(key: CryptoKey, data: Uint8Array, aad: string): Promise<T> {
  if (data.length < 13 + 16) throw new Error("ciphertext too short");
  const pt = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: data.slice(0, 12), additionalData: enc.encode(aad) },
    key,
    data.slice(12),
  );
  return JSON.parse(dec.decode(pt)) as T;
}
