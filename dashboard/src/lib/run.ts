// The simulation form's rules, the same ones the database enforces in start_run (so mistakes show before the request).
import type { RunConfig } from "./types";

export const DEFAULT_RUN: RunConfig = {
  arrival_per_min: 2,
  max_concurrent: 3,
  patience_s: 90,
  language_mix: { en: 40, taglish: 40, tl: 20 },
};

export function runProblems(c: RunConfig): string[] {
  const out: string[] = [];
  const num = (n: unknown) => typeof n === "number" && Number.isFinite(n);
  if (!num(c.arrival_per_min) || c.arrival_per_min < 0.5 || c.arrival_per_min > 30) out.push("calls per minute must be between 0.5 and 30");
  if (!num(c.max_concurrent) || !Number.isInteger(c.max_concurrent) || c.max_concurrent < 1 || c.max_concurrent > 50) out.push("calls at once must be a whole number from 1 to 50");
  if (!num(c.patience_s) || !Number.isInteger(c.patience_s) || c.patience_s < 10 || c.patience_s > 600) out.push("patience must be a whole number of seconds from 10 to 600");
  const mix = Object.values(c.language_mix);
  if (mix.some((w) => !num(w) || w < 0) || mix.reduce((a, b) => a + b, 0) <= 0) out.push("the language mix needs weights of zero or more that add up to more than zero");
  return out;
}

/** "" -> null (a random seed is chosen), "7" -> 7, anything else -> "invalid". */
export function parseSeed(text: string): number | null | "invalid" {
  const t = text.trim();
  if (!t) return null;
  if (!/^\d{1,15}$/.test(t)) return "invalid";
  return Number(t);
}

/** Percentages of the mix, for showing "40 / 40 / 20". */
export function mixPercent(c: RunConfig["language_mix"]): { en: number; taglish: number; tl: number } {
  const total = c.en + c.taglish + c.tl;
  if (total <= 0) return { en: 0, taglish: 0, tl: 0 };
  return { en: Math.round((c.en / total) * 100), taglish: Math.round((c.taglish / total) * 100), tl: Math.round((c.tl / total) * 100) };
}
