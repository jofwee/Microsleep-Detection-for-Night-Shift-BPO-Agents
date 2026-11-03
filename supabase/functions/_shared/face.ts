// Face-embedding maths and the decisions built on it. Pure functions: easy to test.
import { HttpError } from "./http.ts";

export const DIM = 512;

export interface Template {
  embeddings: number[][];
  mean: number[];
}

export const dot = (a: number[], b: number[]): number => {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
};

export function normalise(v: number[]): number[] {
  const n = Math.sqrt(dot(v, v));
  return n === 0 ? v : v.map((x) => x / n);
}

/** One embedding from the client: 512 finite numbers with unit length (within 2 %). */
export function parseEmbedding(v: unknown, label = "embedding"): number[] {
  if (!Array.isArray(v) || v.length !== DIM) {
    throw new HttpError(400, "invalid_embedding", `${label} must be ${DIM} numbers`);
  }
  let sum = 0;
  for (const x of v) {
    if (typeof x !== "number" || !Number.isFinite(x)) {
      throw new HttpError(400, "invalid_embedding", `${label} contains a non-number`);
    }
    sum += x * x;
  }
  const norm = Math.sqrt(sum);
  if (norm < 0.98 || norm > 1.02) {
    throw new HttpError(400, "invalid_embedding", `${label} must be L2-normalised`);
  }
  return (v as number[]).map((x) => x / norm);
}

export function parseEmbeddings(v: unknown, min: number, max: number): number[][] {
  if (!Array.isArray(v) || v.length < min || v.length > max) {
    throw new HttpError(400, "invalid_embedding", `expected between ${min} and ${max} embeddings`);
  }
  return v.map((e, i) => parseEmbedding(e, `embedding ${i}`));
}

export function meanTemplate(embs: number[][]): number[] {
  const m = new Array<number>(DIM).fill(0);
  for (const e of embs) for (let i = 0; i < DIM; i++) m[i] += e[i];
  return normalise(m);
}

/** Cosine to this agent: the best of the stored embeddings and the mean (same as the client's EnrolledProfile.score). */
export function scoreOne(t: Template, e: number[]): number {
  let best = dot(t.mean, e);
  for (const x of t.embeddings) best = Math.max(best, dot(x, e));
  return best;
}

export function median(xs: number[]): number {
  if (xs.length === 0) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** Median of the best `topK` frame scores: one lucky frame cannot pass, one bad frame cannot fail. */
export function matchScore(t: Template, embs: number[][], topK = 3): number {
  const scores = embs.map((e) => scoreOne(t, e)).sort((a, b) => b - a);
  return median(scores.slice(0, topK));
}

/** In one person's capture almost no two frames look like different people. */
export function enrollmentConsistent(embs: number[][], pairMin = 0.25, maxBadFraction = 0.05): boolean {
  let bad = 0;
  let total = 0;
  for (let i = 0; i < embs.length; i++) {
    for (let j = i + 1; j < embs.length; j++) {
      total++;
      if (dot(embs[i], embs[j]) < pairMin) bad++;
    }
  }
  return total > 0 && bad / total <= maxBadFraction;
}
