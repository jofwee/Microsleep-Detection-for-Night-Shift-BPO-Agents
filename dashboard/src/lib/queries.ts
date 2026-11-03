// Everything the dashboard reads and does, as TanStack Query hooks. Row level security does the filtering:
// a team leader only ever gets their own team's rows, so no query here names a team.
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supabase } from "./supabase";
import type {
  AgentStatus, AlertRow, AuditRow, Call, CallTurn, EventRow, FaceVerification, MetricRow, Profile, RunConfig, SessionRow,
  SimulationRun, StatusLogRow, Workstation,
} from "./types";

const sinceIso = (hours: number) => new Date(Date.now() - hours * 3600_000).toISOString();

async function rows<T>(q: PromiseLike<{ data: T[] | null; error: { message: string } | null }>): Promise<T[]> {
  const { data, error } = await q;
  if (error) throw new Error(error.message);
  return data ?? [];
}

// --- reads ------------------------------------------------------------------------------------------

export function useAgents() {
  return useQuery({
    queryKey: ["agents"],
    queryFn: () => rows<Profile>(supabase.from("profiles").select("*").eq("role", "agent").order("full_name")),
  });
}

export function useAgentStatus() {
  return useQuery({
    queryKey: ["agent_status"],
    queryFn: () => rows<AgentStatus>(supabase.from("agent_status").select("*")),
  });
}

export function useCalls(hours = 24, limit = 500) {
  return useQuery({
    queryKey: ["calls", hours, limit],
    queryFn: () => rows<Call>(supabase.from("calls").select("*").gte("queued_at", sinceIso(hours)).order("queued_at", { ascending: false }).limit(limit)),
  });
}

export function useCall(id: string | undefined) {
  return useQuery({
    enabled: !!id,
    queryKey: ["call", id],
    queryFn: async () => (await rows<Call>(supabase.from("calls").select("*").eq("id", id!)))[0] ?? null,
  });
}

export function useCallTurns(callId: string | undefined) {
  return useQuery({
    enabled: !!callId,
    queryKey: ["turns", callId],
    queryFn: () => rows<CallTurn>(supabase.from("call_turns").select("*").eq("call_id", callId!).order("seq")),
  });
}

export function useCallEvents(callId: string | undefined) {
  return useQuery({
    enabled: !!callId,
    queryKey: ["call-events", callId],
    queryFn: () => rows<EventRow>(supabase.from("events").select("*").eq("call_id", callId!).order("ts")),
  });
}

export function useEvents(hours: number, agentId?: string) {
  return useQuery({
    queryKey: ["events", hours, agentId ?? null],
    queryFn: () => {
      let q = supabase.from("events").select("*").gte("ts", sinceIso(hours)).order("ts", { ascending: false }).limit(5000);
      if (agentId) q = q.eq("agent_id", agentId);
      return rows<EventRow>(q);
    },
  });
}

export function useMetrics(hours: number, agentId?: string) {
  return useQuery({
    queryKey: ["metrics", hours, agentId ?? null],
    queryFn: () => {
      let q = supabase.from("metrics_minute").select("*").gte("minute", sinceIso(hours)).order("minute").limit(5000);
      if (agentId) q = q.eq("agent_id", agentId);
      return rows<MetricRow>(q);
    },
  });
}

export function useAlerts(limit = 100) {
  return useQuery({
    queryKey: ["alerts", limit],
    queryFn: () => rows<AlertRow>(supabase.from("alerts").select("*").order("created_at", { ascending: false }).limit(limit)),
  });
}

export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => rows<SimulationRun>(supabase.from("simulation_runs").select("*").order("started_at", { ascending: false }).limit(30)),
  });
}

export function useStatusLog(agentId: string | undefined, hours: number) {
  return useQuery({
    enabled: !!agentId,
    queryKey: ["status_log", agentId, hours],
    queryFn: () =>
      rows<StatusLogRow>(supabase.from("agent_status_log").select("*").eq("agent_id", agentId!).or(`to_ts.gte.${sinceIso(hours)},to_ts.is.null`).order("from_ts")),
  });
}

export function useFaceChecks(agentId: string | undefined) {
  return useQuery({
    enabled: !!agentId,
    queryKey: ["face_checks", agentId],
    queryFn: () => rows<FaceVerification>(supabase.from("face_verifications").select("*").eq("agent_id", agentId!).order("verified_at", { ascending: false }).limit(20)),
  });
}

export function useSessions(agentId: string | undefined) {
  return useQuery({
    enabled: !!agentId,
    queryKey: ["sessions", agentId],
    queryFn: () => rows<SessionRow>(supabase.from("sessions").select("*").eq("agent_id", agentId!).order("started_at", { ascending: false }).limit(20)),
  });
}

export function useAudit(limit = 100) {
  return useQuery({
    queryKey: ["audit", limit],
    queryFn: () => rows<AuditRow>(supabase.from("audit_log").select("*").order("ts", { ascending: false }).limit(limit)),
  });
}

export function useWorkstations() {
  return useQuery({
    queryKey: ["workstations"],
    queryFn: () => rows<Workstation>(supabase.from("workstations").select("id,name,created_at,last_seen_at,revoked_at").order("created_at", { ascending: false })),
  });
}

// --- live updates -----------------------------------------------------------------------------------

const LIVE: Record<string, string[]> = {
  agent_status: ["agent_status"],
  calls: ["calls", "call"],
  call_turns: ["turns"],
  events: ["events", "call-events"],
  alerts: ["alerts"],
  simulation_runs: ["runs"],
};

/** Subscribes once to the tables that change while people work, and refreshes the matching queries. */
export function useLiveUpdates(enabled: boolean) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled) return;
    // One channel per table: a table that is not published for Realtime fails only its own channel, not the others.
    const channels = Object.entries(LIVE).map(([table, keys]) => {
      const channel = supabase.channel(`live-${table}`);
      channel.on("postgres_changes", { event: "*", schema: "public", table }, () => {
        for (const k of keys) void qc.invalidateQueries({ queryKey: [k] });
      });
      channel.subscribe((status, err) => {
        if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") console.warn(`live updates for ${table}: ${status}`, err?.message ?? "");
      });
      return channel;
    });
    return () => {
      for (const c of channels) void supabase.removeChannel(c);
    };
  }, [enabled, qc]);
}

// --- actions ----------------------------------------------------------------------------------------

export async function describeError(e: unknown): Promise<string> {
  const err = e as { message?: string; context?: Response };
  if (err?.context && typeof err.context.json === "function") {
    try {
      const body = await err.context.clone().json();
      return body.message || body.error || err.message || "something went wrong";
    } catch {
      /* fall through */
    }
  }
  return err?.message || "something went wrong";
}

async function manage<T>(action: string, payload: Record<string, unknown> = {}): Promise<T> {
  const { data, error } = await supabase.functions.invoke("manage-team", { body: { action, ...payload } });
  if (error) throw new Error(await describeError(error));
  return data as T;
}

export const teamActions = {
  createAgent: (email: string, full_name: string) => manage<{ agent_id: string; email: string; temp_password: string }>("create_agent", { email, full_name }),
  resetPassword: (agent_id: string) => manage<{ temp_password: string }>("reset_password", { agent_id }),
  setActive: (agent_id: string, active: boolean) => manage<{ active: boolean }>("set_active", { agent_id, active }),
  enrollmentCode: (agent_id: string) => manage<{ code: string; expires_at?: string }>("issue_enrollment_code", { agent_id }),
  pairingCode: () => manage<{ code: string; expires_at?: string }>("issue_pairing_code"),
  deleteBiometrics: (agent_id: string) => manage<{ deleted: boolean }>("delete_biometrics", { agent_id }),
};

function useInvalidating<TArgs, TOut>(fn: (a: TArgs) => Promise<TOut>, keys: string[]) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      for (const k of keys) void qc.invalidateQueries({ queryKey: [k] });
    },
  });
}

export const useAckAlert = () =>
  useInvalidating(async (id: string) => {
    const { error } = await supabase.rpc("ack_alert", { p_alert: id });
    if (error) throw new Error(error.message);
  }, ["alerts"]);

export const useStartRun = () =>
  useInvalidating(async (a: { config: RunConfig; seed: number | null }) => {
    const { data, error } = await supabase.rpc("start_run", { p_config: a.config, p_seed: a.seed });
    if (error) throw new Error(error.message);
    return data as string;
  }, ["runs", "calls"]);

export const useStopRun = () =>
  useInvalidating(async (id: string) => {
    const { error } = await supabase.rpc("stop_run", { p_run: id });
    if (error) throw new Error(error.message);
  }, ["runs", "calls", "agent_status"]);
