import { useEffect, useState, type ReactNode } from "react";
import type { AgentState, AlertKind, EventType, FatigueState } from "../lib/types";

/** A clock that ticks, so "waiting 0:42" keeps counting without a refetch. */
export function useNow(intervalMs = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export type Tone = "plain" | "ink" | "alarm";

export function Chip({ children, tone = "plain", title }: { children: ReactNode; tone?: Tone; title?: string }) {
  const cls = tone === "ink" ? "chip chip-ink" : tone === "alarm" ? "chip chip-alarm" : "chip";
  return (
    <span className={cls} title={title}>
      {children}
    </span>
  );
}

// The one red: MICROSLEEP and IDENTITY_MISMATCH (and the alert for repeated microsleeps). Nothing else is red.
export const ALARM_EVENTS: EventType[] = ["MICROSLEEP", "IDENTITY_MISMATCH"];
export const ALARM_ALERTS: AlertKind[] = ["IDENTITY_MISMATCH", "REPEATED_MICROSLEEP"];

const STATUS_LABEL: Record<AgentState, string> = {
  OFFLINE: "offline", NOT_READY: "not ready", AVAILABLE: "available", RINGING: "ringing", ON_CALL: "on call", WRAP_UP: "wrap-up", SYSTEM_HOLD: "on hold",
};

export function StatusChip({ status }: { status: AgentState }) {
  const tone: Tone = status === "ON_CALL" || status === "RINGING" || status === "SYSTEM_HOLD" ? "ink" : "plain";
  return (
    <Chip tone={tone}>
      {status === "RINGING" && <span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-current" />}
      {STATUS_LABEL[status]}
    </Chip>
  );
}

export function FatigueChip({ state }: { state: FatigueState }) {
  if (state === "MICROSLEEP") return <Chip tone="alarm">microsleep</Chip>;
  if (state === "DROWSY") return <span className="chip" style={{ color: "var(--ink)", borderColor: "var(--ink)" }}>drowsy</span>;
  return <Chip>alert</Chip>;
}

export function EventChip({ type }: { type: EventType }) {
  return <Chip tone={ALARM_EVENTS.includes(type) ? "alarm" : "plain"}>{type.toLowerCase().replaceAll("_", " ")}</Chip>;
}

export function AlertChip({ kind }: { kind: AlertKind }) {
  return <Chip tone={ALARM_ALERTS.includes(kind) ? "alarm" : "ink"}>{kind.toLowerCase().replaceAll("_", " ")}</Chip>;
}

export function Dot({ on, title }: { on: boolean; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-block h-1.5 w-1.5 rounded-full ${on ? "pulse-dot bg-ink" : "bg-g300"}`}
      aria-label={title}
    />
  );
}

export function PageHeader({ title, kicker, right }: { title: string; kicker?: string; right?: ReactNode }) {
  return (
    <header className="rise mb-10 flex flex-wrap items-end justify-between gap-4" style={{ ["--i" as string]: 0 }}>
      <div>
        {kicker && <p className="label mb-3">{kicker}</p>}
        <h1 className="page-title">{title}</h1>
      </div>
      {right && <div className="flex items-center gap-2">{right}</div>}
    </header>
  );
}

/** "01 — blog": a numbered, em-dashed section header in the display font. */
export function SectionHeader({ n, title, right }: { n: number; title: string; right?: ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline justify-between gap-3">
      <h2 className="section-label">
        {String(n).padStart(2, "0")} — {title}
      </h2>
      {right}
    </div>
  );
}

export function Section({ n, title, right, children, index = 1 }: { n: number; title: string; right?: ReactNode; children: ReactNode; index?: number }) {
  return (
    <section className="rise mb-14" style={{ ["--i" as string]: index }}>
      <SectionHeader n={n} title={title} right={right} />
      {children}
    </section>
  );
}

/** Stat cells separated by hairlines (both axes) instead of gaps: the dividers are the design. */
export function StatGrid({ children, cols = 4 }: { children: ReactNode; cols?: 2 | 3 | 4 | 5 }) {
  const c = { 2: "sm:grid-cols-2", 3: "sm:grid-cols-3", 4: "sm:grid-cols-4", 5: "sm:grid-cols-5" }[cols];
  return (
    <div className={`grid grid-cols-2 overflow-hidden rounded-card border bg-bg ${c}`}>
      {children}
    </div>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "alarm" }) {
  return (
    <div className="border-b border-r p-4 [&:nth-child(2n)]:border-r-0 sm:[&:nth-child(2n)]:border-r sm:[&:last-child]:border-r-0">
      <p className="label">{label}</p>
      <p className={`display mt-3 text-[1.9rem] ${tone === "alarm" ? "text-alarm" : ""}`}>{value}</p>
      {hint && <p className="mt-2 text-xs text-g500">{hint}</p>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-card border border-dashed p-8 text-center text-sm text-g500">{children}</p>;
}

export function Loading() {
  return <p className="label py-6">loading…</p>;
}

export function ErrorNote({ error }: { error: unknown }) {
  return <p role="alert" className="rounded-mid border border-alarm bg-alarm-soft p-3 text-sm text-alarm">{(error as Error)?.message ?? "something went wrong"}</p>;
}

/** A small tab/segment switch in the mono micro-label register. */
export function Segmented<T extends string | number>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex overflow-hidden rounded-input border" role="group">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          onClick={() => onChange(o.value)}
          aria-pressed={o.value === value}
          className={`px-3 py-1.5 font-mono text-[10px] uppercase tracking-[1px] transition-colors ${
            o.value === value ? "bg-ink text-bg" : "text-g500 hover:bg-g100"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function useAgentNames(agents: { id: string; full_name: string }[] | undefined): (id: string | null | undefined) => string {
  const map = new Map((agents ?? []).map((a) => [a.id, a.full_name]));
  return (id) => (id ? map.get(id) ?? "unknown agent" : "–");
}
