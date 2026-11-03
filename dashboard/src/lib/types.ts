// Row shapes for the tables a team leader can read (see supabase/migrations). Hand-written and kept small:
// only the columns the dashboard uses.

export type AgentState = "OFFLINE" | "NOT_READY" | "AVAILABLE" | "RINGING" | "ON_CALL" | "WRAP_UP" | "SYSTEM_HOLD";
export type FatigueState = "ALERT" | "DROWSY" | "MICROSLEEP";
export type CallStatus = "QUEUED" | "OFFERED" | "ACTIVE" | "WRAP_UP" | "COMPLETED" | "ABANDONED";
export type CallLanguage = "en" | "tl" | "taglish";
export type EventType =
  | "MICROSLEEP"
  | "LONG_BLINK"
  | "YAWN"
  | "HEAD_NOD"
  | "HEAD_TILT"
  | "DROWSY"
  | "ABSENT"
  | "IDENTITY_MISMATCH"
  | "MULTIPLE_FACES"
  | "SPOOF_SUSPECTED";
export type Severity = "info" | "warning" | "critical";
export type AlertKind = "IDENTITY_MISMATCH" | "SPOOF_SUSPECTED" | "REPEATED_MICROSLEEP" | "FACE_LOCKOUT";
export type Disposition = "resolved" | "unresolved" | "escalated";

export interface Profile {
  id: string;
  role: "agent" | "team_leader";
  team_id: string;
  full_name: string;
  active: boolean;
  must_change_password: boolean;
  consent_at: string | null;
  created_at: string;
}

export interface AgentStatus {
  agent_id: string;
  team_id: string;
  status: AgentState;
  reason: string | null;
  status_since: string;
  monitor_state: "OFF" | "LIVE";
  identity_ok: boolean;
  fatigue_state: FatigueState;
  last_heartbeat_at: string | null;
  current_call_id: string | null;
  missed_offers: number;
  hold_pending: string | null;
}

export interface StatusLogRow {
  id: number;
  agent_id: string;
  status: AgentState;
  reason: string | null;
  from_ts: string;
  to_ts: string | null;
}

export interface Call {
  id: string;
  run_id: string | null;
  team_id: string;
  scenario_id: string;
  language: CallLanguage;
  mood: string;
  status: CallStatus;
  agent_id: string | null;
  queued_at: string;
  offered_at: string | null;
  answered_at: string | null;
  ended_at: string | null;
  wrap_up_ended_at: string | null;
  offer_attempts: number;
  patience_s: number;
  disposition: Disposition | null;
  end_reason: string | null;
  notes: string | null;
}

export interface CallTurn {
  id: number;
  call_id: string;
  seq: number;
  speaker: "agent" | "caller";
  text: string;
  lang: string | null;
  created_at: string;
  stt_ms: number | null;
  llm_ms: number | null;
  tts_ms: number | null;
}

export interface EventRow {
  id: number;
  session_id: string;
  agent_id: string;
  type: EventType;
  ts: string;
  duration_ms: number;
  value: number | null;
  severity: Severity;
  call_id: string | null;
}

export interface MetricRow {
  session_id: string;
  minute: string;
  agent_id: string;
  blink_rate: number | null;
  perclos: number | null;
  mean_ear: number | null;
  yawns: number;
  nods: number;
  status: string | null;
}

export interface AlertRow {
  id: string;
  team_id: string;
  agent_id: string;
  kind: AlertKind;
  message: string;
  created_at: string;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
}

export interface RunConfig {
  arrival_per_min: number;
  max_concurrent: number;
  patience_s: number;
  language_mix: { en: number; taglish: number; tl: number };
}

export interface SimulationRun {
  id: string;
  seed: number;
  config: RunConfig;
  status: "RUNNING" | "STOPPED";
  started_at: string;
  ended_at: string | null;
  calls_created: number;
}

export interface FaceVerification {
  id: string;
  agent_id: string;
  score: number;
  liveness_ok: boolean;
  verified_at: string;
  expires_at: string;
  revoked_at: string | null;
  revoked_reason: string | null;
}

export interface AuditRow {
  id: number;
  actor_id: string | null;
  action: string;
  target_id: string | null;
  ts: string;
  detail: Record<string, unknown>;
}

export interface Workstation {
  id: string;
  name: string;
  created_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
}

export interface SessionRow {
  id: string;
  agent_id: string;
  started_at: string;
  ended_at: string | null;
}
