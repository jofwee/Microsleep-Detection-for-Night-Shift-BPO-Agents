import { Link } from "react-router-dom";
import { Chip, Dot, Empty, ErrorNote, FatigueChip, PageHeader, Section, Stat, StatGrid, StatusChip, useNow } from "../../components/ui";
import { useAckAlert, useAgents, useAgentStatus, useAlerts, useCalls, useRuns } from "../../lib/queries";
import { ago, clock, seconds } from "../../lib/format";
import { agentCounts, callStats, heartbeatAge, STALE_HEARTBEAT_SECONDS } from "../../lib/stats";
import type { AgentStatus, Profile } from "../../lib/types";
import { AlertChip } from "../../components/ui";

const WORKING = new Set(["AVAILABLE", "RINGING", "ON_CALL", "WRAP_UP", "SYSTEM_HOLD"]);

export function AgentCard({ agent, status, now, index }: { agent: Profile; status: AgentStatus | undefined; now: Date; index: number }) {
  const state = status?.status ?? "OFFLINE";
  const working = WORKING.has(state);
  const age = status ? heartbeatAge(status, now) : null;
  const noSignal = working && (age == null || age > STALE_HEARTBEAT_SECONDS);
  const alarm = working && status && (status.fatigue_state === "MICROSLEEP" || !status.identity_ok);

  return (
    <li className="rise" style={{ ["--i" as string]: Math.min(index, 5) }}>
      <Link to={`/agents/${agent.id}`} className={`card card-lift block p-4 ${alarm ? "card-alarm" : ""}`}>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate font-medium">{agent.full_name}</p>
            <p className="label mt-1">
              {status ? `${clock(seconds(status.status_since, now))} in this status` : "has not signed in yet"}
            </p>
          </div>
          {!agent.active ? <Chip>deactivated</Chip> : <StatusChip status={state} />}
        </div>

        {working && status && (
          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2">
            <FatigueChip state={status.fatigue_state} />
            {!status.identity_ok && <Chip tone="alarm">identity not confirmed</Chip>}
            <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[1px] text-g500">
              <Dot on={status.monitor_state === "LIVE" && !noSignal} title={noSignal ? "no signal" : "camera monitor"} />
              {noSignal ? "no signal" : status.monitor_state === "LIVE" ? "camera on" : "camera off"}
            </span>
          </div>
        )}
        {state === "SYSTEM_HOLD" && status?.reason && <p className="mt-3 text-xs text-g500">held: {status.reason}</p>}
        {state === "NOT_READY" && status?.reason && <p className="mt-3 text-xs text-g500">{status.reason}</p>}
        {status?.current_call_id && (
          <p className="mt-3 text-xs">
            <span className="text-g500">on call · </span>
            <span className="link">{status.current_call_id.slice(0, 8)}</span>
          </p>
        )}
        <p className="label mt-4">last signal {working ? ago(status?.last_heartbeat_at, now) : "–"}</p>
      </Link>
    </li>
  );
}

export function LivePage() {
  const now = useNow(1000);
  const agents = useAgents();
  const statuses = useAgentStatus();
  const calls = useCalls(8, 500);
  const alerts = useAlerts(30);
  const runs = useRuns();
  const ack = useAckAlert();

  const byId = new Map((statuses.data ?? []).map((s) => [s.agent_id, s]));
  const counts = agentCounts(statuses.data ?? []);
  const stats = callStats(calls.data ?? [], now);
  const running = (runs.data ?? []).find((r) => r.status === "RUNNING");
  const open = (alerts.data ?? []).filter((a) => !a.acknowledged_at).slice(0, 5);
  const names = new Map((agents.data ?? []).map((a) => [a.id, a.full_name]));
  const error = agents.error ?? statuses.error ?? calls.error;

  return (
    <>
      <PageHeader
        title="live"
        kicker="your team, right now"
        right={
          running ? (
            <Link to="/simulation" className="chip chip-ink">
              <span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-current" /> simulation running
            </Link>
          ) : (
            <Link to="/simulation" className="btn">start a simulation</Link>
          )
        }
      />
      {error && <ErrorNote error={error} />}

      <Section n={1} title="now">
        <StatGrid cols={5}>
          <Stat label="available" value={counts.AVAILABLE} />
          <Stat label="on a call" value={counts.ON_CALL + counts.RINGING} hint={counts.RINGING ? `${counts.RINGING} ringing` : undefined} />
          <Stat label="wrap-up" value={counts.WRAP_UP} />
          <Stat label="on hold" value={counts.SYSTEM_HOLD} hint="stopped by the monitor" />
          <Stat label="waiting callers" value={stats.queued + stats.offered} hint={stats.longestWaiting != null ? `longest ${clock(stats.longestWaiting)}` : "none waiting"} />
        </StatGrid>
      </Section>

      <Section n={2} title="agents" index={2}>
        {agents.isLoading ? (
          <p className="label">loading…</p>
        ) : (agents.data ?? []).length === 0 ? (
          <Empty>No agents yet. Create accounts on the team page.</Empty>
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {(agents.data ?? []).map((a, i) => (
              <AgentCard key={a.id} agent={a} status={byId.get(a.id)} now={now} index={i} />
            ))}
          </ul>
        )}
      </Section>

      <Section n={3} title="open alerts" index={3} right={<Link to="/alerts" className="label link">all alerts</Link>}>
        {open.length === 0 ? (
          <Empty>Nothing needs your attention.</Empty>
        ) : (
          <ul className="card divide-y">
            {open.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div className="flex flex-wrap items-center gap-3">
                  <AlertChip kind={a.kind} />
                  <span className="text-sm">{names.get(a.agent_id) ?? "an agent"}</span>
                  <span className="text-sm text-g500">{a.message}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="label">{ago(a.created_at, now)}</span>
                  <button className="btn" disabled={ack.isPending} onClick={() => ack.mutate(a.id)}>acknowledge</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}
