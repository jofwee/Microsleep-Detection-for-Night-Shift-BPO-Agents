// The numbers on the dashboard. Pure functions over rows, so they are tested without a server.
import type { AgentState, AgentStatus, Call, EventRow, EventType, MetricRow, StatusLogRow } from "./types";

export const SERVICE_LEVEL_SECONDS = 20; // a call is "answered in time" when the agent picks up within this long

const secs = (a: string | null, b: string | null): number | null => {
  if (!a || !b) return null;
  const ms = new Date(b).getTime() - new Date(a).getTime();
  return Number.isFinite(ms) ? Math.max(0, ms / 1000) : null;
};

/** How long the caller waited: until an agent answered, or until they gave up. */
export function waitSeconds(call: Call): number | null {
  return secs(call.queued_at, call.answered_at ?? (call.status === "ABANDONED" ? call.ended_at : null));
}

/** Talk time: from answer to the end of the conversation. */
export function handleSeconds(call: Call): number | null {
  return secs(call.answered_at, call.ended_at);
}

export interface CallStats {
  total: number;
  answered: number;
  abandoned: number;
  abandonmentRate: number | null; // abandoned / (answered + abandoned)
  serviceLevel: number | null; // answered within the threshold / (answered + abandoned)
  avgWait: number | null;
  avgHandle: number | null;
  queued: number;
  offered: number;
  active: number;
  wrapUp: number;
  longestWaiting: number | null; // seconds the oldest waiting caller has been in line
}

const mean = (xs: number[]): number | null => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

export function callStats(calls: Call[], now: Date = new Date(), threshold = SERVICE_LEVEL_SECONDS): CallStats {
  const answered = calls.filter((c) => c.answered_at);
  const abandoned = calls.filter((c) => c.status === "ABANDONED" && !c.answered_at);
  const settled = answered.length + abandoned.length;
  const inTime = answered.filter((c) => (waitSeconds(c) ?? Infinity) <= threshold).length;
  const waiting = calls.filter((c) => c.status === "QUEUED" || c.status === "OFFERED");
  const waits = waiting.map((c) => secs(c.queued_at, now.toISOString()) ?? 0);
  return {
    total: calls.length,
    answered: answered.length,
    abandoned: abandoned.length,
    abandonmentRate: settled ? abandoned.length / settled : null,
    serviceLevel: settled ? inTime / settled : null,
    avgWait: mean(answered.map((c) => waitSeconds(c)).filter((x): x is number => x != null)),
    avgHandle: mean(calls.map(handleSeconds).filter((x): x is number => x != null)),
    queued: calls.filter((c) => c.status === "QUEUED").length,
    offered: calls.filter((c) => c.status === "OFFERED").length,
    active: calls.filter((c) => c.status === "ACTIVE").length,
    wrapUp: calls.filter((c) => c.status === "WRAP_UP").length,
    longestWaiting: waits.length ? Math.max(...waits) : null,
  };
}

export function agentCounts(statuses: AgentStatus[]): Record<AgentState, number> {
  const out: Record<AgentState, number> = {
    OFFLINE: 0, NOT_READY: 0, AVAILABLE: 0, RINGING: 0, ON_CALL: 0, WRAP_UP: 0, SYSTEM_HOLD: 0,
  };
  for (const s of statuses) out[s.status] += 1;
  return out;
}

/** An agent whose heartbeat is older than this is shown as "no signal" (the server drops them at 60 s). */
export const STALE_HEARTBEAT_SECONDS = 15;

export function heartbeatAge(s: AgentStatus, now: Date = new Date()): number | null {
  return secs(s.last_heartbeat_at, now.toISOString());
}

// --- events -----------------------------------------------------------------------------------------

export const FATIGUE_TYPES: EventType[] = ["MICROSLEEP", "LONG_BLINK", "YAWN", "HEAD_NOD", "HEAD_TILT", "DROWSY"];

export type HourBucket = { hour: string } & Partial<Record<EventType, number>>;

/** Events per hour of the day (local time), each type counted separately. Hours without events are kept so the chart has no gaps. */
export function eventsByHour(events: EventRow[], types: EventType[] = FATIGUE_TYPES): HourBucket[] {
  const buckets = new Map<number, HourBucket>();
  for (const e of events) {
    if (!types.includes(e.type)) continue;
    const h = new Date(e.ts).getHours();
    const b = buckets.get(h) ?? ({ hour: `${String(h).padStart(2, "0")}:00` } as HourBucket);
    b[e.type] = (b[e.type] ?? 0) + 1;
    buckets.set(h, b);
  }
  if (!buckets.size) return [];
  const hours = [...buckets.keys()];
  const out: HourBucket[] = [];
  for (let h = Math.min(...hours); h <= Math.max(...hours); h++) {
    out.push(buckets.get(h) ?? { hour: `${String(h).padStart(2, "0")}:00` });
  }
  return out;
}

export function countByType(events: EventRow[]): Partial<Record<EventType, number>> {
  const out: Partial<Record<EventType, number>> = {};
  for (const e of events) out[e.type] = (out[e.type] ?? 0) + 1;
  return out;
}

export interface FatigueGroup {
  key: string;
  calls: number;
  minutes: number; // talk time in the group
  microsleeps: number;
  yawns: number;
  microsleepsPerHour: number | null; // per hour of talk time
}

/** Fatigue events against what the agent was doing: grouped by language, scenario or mood of the call they happened in. */
export function fatigueBy(calls: Call[], events: EventRow[], key: "language" | "scenario_id" | "mood"): FatigueGroup[] {
  const byCall = new Map(calls.map((c) => [c.id, c]));
  const groups = new Map<string, FatigueGroup>();
  const ensure = (k: string) =>
    groups.get(k) ?? groups.set(k, { key: k, calls: 0, minutes: 0, microsleeps: 0, yawns: 0, microsleepsPerHour: null }).get(k)!;
  for (const c of calls) {
    const g = ensure(String(c[key]));
    g.calls += 1;
    g.minutes += (handleSeconds(c) ?? 0) / 60;
  }
  for (const e of events) {
    const c = e.call_id ? byCall.get(e.call_id) : undefined;
    if (!c) continue;
    const g = ensure(String(c[key]));
    if (e.type === "MICROSLEEP") g.microsleeps += 1;
    if (e.type === "YAWN") g.yawns += 1;
  }
  for (const g of groups.values()) g.microsleepsPerHour = g.minutes >= 1 ? g.microsleeps / (g.minutes / 60) : null;
  return [...groups.values()].sort((a, b) => b.calls - a.calls);
}

// --- metrics ----------------------------------------------------------------------------------------

export interface MetricPoint {
  minute: string;
  perclos: number | null;
  blink_rate: number | null;
  mean_ear: number | null;
}

export function metricSeries(rows: MetricRow[]): MetricPoint[] {
  return [...rows]
    .sort((a, b) => a.minute.localeCompare(b.minute))
    .map((r) => ({ minute: r.minute, perclos: r.perclos, blink_rate: r.blink_rate, mean_ear: r.mean_ear }));
}

// --- time in status ---------------------------------------------------------------------------------

/** Seconds spent in each status within [from, to]. Open rows (no to_ts) run until `to`. */
export function statusSeconds(log: StatusLogRow[], from: Date, to: Date): Partial<Record<AgentState, number>> {
  const out: Partial<Record<AgentState, number>> = {};
  for (const r of log) {
    const start = Math.max(new Date(r.from_ts).getTime(), from.getTime());
    const end = Math.min(r.to_ts ? new Date(r.to_ts).getTime() : to.getTime(), to.getTime());
    if (end > start) out[r.status] = (out[r.status] ?? 0) + (end - start) / 1000;
  }
  return out;
}

/** The share of working time (anything but OFFLINE and NOT_READY) the agent spent in each working status. */
export function workShare(seconds: Partial<Record<AgentState, number>>): Partial<Record<AgentState, number>> {
  const work: AgentState[] = ["AVAILABLE", "RINGING", "ON_CALL", "WRAP_UP", "SYSTEM_HOLD"];
  const total = work.reduce((a, s) => a + (seconds[s] ?? 0), 0);
  const out: Partial<Record<AgentState, number>> = {};
  if (total <= 0) return out;
  for (const s of work) out[s] = (seconds[s] ?? 0) / total;
  return out;
}

// --- calls per hour ---------------------------------------------------------------------------------

export interface CallHour {
  hour: string;
  completed: number;
  abandoned: number;
  inProgress: number;
}

/** Calls that arrived in each hour of the day (local time), by how they ended. Quiet hours in between are kept. */
export function callsByHour(calls: Call[]): CallHour[] {
  const buckets = new Map<number, CallHour>();
  const label = (h: number) => `${String(h).padStart(2, "0")}:00`;
  for (const c of calls) {
    const h = new Date(c.queued_at).getHours();
    const b = buckets.get(h) ?? { hour: label(h), completed: 0, abandoned: 0, inProgress: 0 };
    if (c.status === "COMPLETED") b.completed += 1;
    else if (c.status === "ABANDONED") b.abandoned += 1;
    else b.inProgress += 1;
    buckets.set(h, b);
  }
  if (!buckets.size) return [];
  const hours = [...buckets.keys()];
  const out: CallHour[] = [];
  for (let h = Math.min(...hours); h <= Math.max(...hours); h++) {
    out.push(buckets.get(h) ?? { hour: label(h), completed: 0, abandoned: 0, inProgress: 0 });
  }
  return out;
}

// --- per agent --------------------------------------------------------------------------------------

export interface AgentSummary {
  id: string;
  name: string;
  calls: number;
  talkMinutes: number;
  microsleeps: number;
  yawns: number;
  avgPerclos: number | null;
}

export function perAgent(agents: { id: string; full_name: string }[], calls: Call[], events: EventRow[], metrics: MetricRow[]): AgentSummary[] {
  return agents.map((a) => {
    const mine = calls.filter((c) => c.agent_id === a.id);
    const perclos = metrics.filter((m) => m.agent_id === a.id && m.perclos != null).map((m) => m.perclos as number);
    return {
      id: a.id,
      name: a.full_name,
      calls: mine.length,
      talkMinutes: mine.reduce((sum, c) => sum + (handleSeconds(c) ?? 0), 0) / 60,
      microsleeps: events.filter((e) => e.agent_id === a.id && e.type === "MICROSLEEP").length,
      yawns: events.filter((e) => e.agent_id === a.id && e.type === "YAWN").length,
      avgPerclos: perclos.length ? perclos.reduce((x, y) => x + y, 0) / perclos.length : null,
    };
  });
}
