// Small formatting helpers. Pure, so they are tested.

export function seconds(from: string | Date | null | undefined, to: Date = new Date()): number | null {
  if (!from) return null;
  const ms = to.getTime() - new Date(from).getTime();
  return Number.isFinite(ms) ? Math.max(0, Math.round(ms / 1000)) : null;
}

/** 75 -> "1:15", 3725 -> "1:02:05", null -> "–" */
export function clock(totalSeconds: number | null | undefined): string {
  if (totalSeconds == null || !Number.isFinite(totalSeconds)) return "–";
  const s = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`;
}

/** "just now", "12 s ago", "5 min ago", "3 h ago", "2 d ago" */
export function ago(from: string | Date | null | undefined, now: Date = new Date()): string {
  const s = seconds(from, now);
  if (s == null) return "never";
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function percent(value: number | null | undefined, digits = 0): string {
  return value == null || !Number.isFinite(value) ? "–" : `${(value * 100).toFixed(digits)}%`;
}

export function number(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? "–" : value.toFixed(digits);
}

export function time(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

/** "internet_down" -> "internet down" */
export function words(id: string | null | undefined): string {
  return (id ?? "").replaceAll("_", " ").toLowerCase();
}

export const LANGUAGE_LABEL: Record<string, string> = { en: "English", tl: "Tagalog", taglish: "Taglish" };

/** A CSV cell: quoted when it holds a comma, quote or line break. */
export function csvCell(value: unknown): string {
  const s = value == null ? "" : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

export function toCsv(header: string[], rows: unknown[][]): string {
  return [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\n");
}
