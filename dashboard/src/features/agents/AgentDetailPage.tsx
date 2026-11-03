import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChartCard, LineSeries, ShareBar } from "../../components/charts";
import { Chip, Empty, ErrorNote, EventChip, FatigueChip, Loading, PageHeader, Section, Segmented, Stat, StatGrid, StatusChip, useNow } from "../../components/ui";
import { ago, dateTime, number, percent, time } from "../../lib/format";
import { useAgents, useAgentStatus, useEvents, useFaceChecks, useMetrics, useSessions, useStatusLog } from "../../lib/queries";
import { countByType, metricSeries, statusSeconds, workShare } from "../../lib/stats";
import type { AgentState, FaceVerification } from "../../lib/types";

const RANGES = [{ value: 8, label: "8 h" }, { value: 24, label: "24 h" }, { value: 168, label: "7 d" }];

const STATUS_FILL: Partial<Record<AgentState, string>> = {
  AVAILABLE: "var(--g300)", RINGING: "var(--g400)", ON_CALL: "var(--ink)", WRAP_UP: "var(--g600)", SYSTEM_HOLD: "var(--g500)",
};
const STATUS_NAME: Record<string, string> = { AVAILABLE: "available", RINGING: "ringing", ON_CALL: "on call", WRAP_UP: "wrap-up", SYSTEM_HOLD: "on hold" };

export function faceCheckState(v: FaceVerification, now: Date): string {
  if (v.revoked_at) return `revoked: ${v.revoked_reason ?? "no reason given"}`;
  return new Date(v.expires_at) > now ? "valid" : "expired";
}

export function AgentDetailPage() {
  const { id } = useParams();
  const [hours, setHours] = useState(24);
  const now = useNow(15_000);
  const agents = useAgents();
  const statuses = useAgentStatus();
  const events = useEvents(hours, id);
  const metrics = useMetrics(hours, id);
  const log = useStatusLog(id, hours);
  const faces = useFaceChecks(id);
  const sessions = useSessions(id);

  if (agents.isLoading) return <Loading />;
  const agent = (agents.data ?? []).find((a) => a.id === id);
  if (!agent) return <Empty>That agent was not found (or is on another team).</Empty>;
  const status = (statuses.data ?? []).find((s) => s.agent_id === id);

  const ev = events.data ?? [];
  const counts = countByType(ev);
  const series = metricSeries(metrics.data ?? []);
  const withPerclos = series.filter((p) => p.perclos != null);
  const avgPerclos = withPerclos.length ? withPerclos.reduce((a, p) => a + (p.perclos ?? 0), 0) / withPerclos.length : null;
  const share = workShare(statusSeconds(log.data ?? [], new Date(now.getTime() - hours * 3600_000), now));
  const shareParts = (Object.keys(STATUS_NAME) as AgentState[]).map((k) => ({ key: k, label: STATUS_NAME[k], share: share[k] ?? 0, fill: STATUS_FILL[k] }));

  const lineTable = (key: "perclos" | "blink_rate" | "mean_ear") => ({
    head: ["minute", key.replace("_", " ")],
    rows: series.filter((p) => p[key] != null).map((p) => [time(p.minute), number(p[key] as number, key === "perclos" ? 3 : 2)]),
  });

  return (
    <>
      <PageHeader
        title={agent.full_name}
        kicker={agent.active ? "agent" : "agent · deactivated"}
        right={
          <>
            <Segmented value={hours} options={RANGES} onChange={setHours} />
            <Link to="/agents" className="btn">all agents</Link>
          </>
        }
      />
      {(events.error || metrics.error) && <ErrorNote error={events.error ?? metrics.error} />}

      <div className="rise -mt-4 mb-10 flex flex-wrap items-center gap-3" style={{ ["--i" as string]: 1 }}>
        <StatusChip status={status?.status ?? "OFFLINE"} />
        {status && status.status !== "OFFLINE" && status.status !== "NOT_READY" && <FatigueChip state={status.fatigue_state} />}
        <span className="label">last signal {ago(status?.last_heartbeat_at, now)}</span>
      </div>

      <Section n={1} title={`last ${hours} h`}>
        <StatGrid cols={4}>
          <Stat label="microsleeps" value={counts.MICROSLEEP ?? 0} tone={(counts.MICROSLEEP ?? 0) > 0 ? "alarm" : undefined} />
          <Stat label="yawns" value={counts.YAWN ?? 0} />
          <Stat label="head nods" value={(counts.HEAD_NOD ?? 0) + (counts.HEAD_TILT ?? 0)} hint="nods and tilts" />
          <Stat label="average eye closure" value={percent(avgPerclos, 1)} hint="PERCLOS, share of time eyes are closed" />
        </StatGrid>
      </Section>

      <Section n={2} title="where the time went" index={2}>
        {shareParts.every((p) => p.share === 0) ? <Empty>No working time in this period.</Empty> : <div className="card p-5"><ShareBar parts={shareParts} /></div>}
      </Section>

      <Section n={3} title="fatigue measures" index={3}>
        {series.length === 0 ? (
          <Empty>No per-minute data in this period. It appears while the agent works with the camera on.</Empty>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            <ChartCard title="Eye closure (PERCLOS)" subtitle="Share of each minute the eyes were mostly closed" table={lineTable("perclos")} busy={metrics.isFetching}>
              <LineSeries data={series as never} dataKey="perclos" name="eye closure" format={(v) => percent(v, 1)} domain={[0, "auto"]} />
            </ChartCard>
            <ChartCard title="Blink rate" subtitle="Blinks per minute" table={lineTable("blink_rate")} busy={metrics.isFetching}>
              <LineSeries data={series as never} dataKey="blink_rate" name="blinks per minute" format={(v) => number(v, 0)} domain={[0, "auto"]} />
            </ChartCard>
          </div>
        )}
      </Section>

      <Section n={4} title="recent events" index={4} right={<span className="label">{ev.length} in this period</span>}>
        {ev.length === 0 ? (
          <Empty>No events in this period.</Empty>
        ) : (
          <ul className="card divide-y">
            {ev.slice(0, 30).map((e) => (
              <li key={e.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div className="flex items-center gap-3">
                  <EventChip type={e.type} />
                  {e.duration_ms > 0 && <span className="font-mono text-xs text-g500">{(e.duration_ms / 1000).toFixed(1)} s</span>}
                  {e.call_id && <Link to={`/calls/${e.call_id}`} className="link text-xs">during a call</Link>}
                </div>
                <span className="label">{dateTime(e.ts)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section n={5} title="face checks and shifts" index={5}>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="card overflow-x-auto">
            <p className="label p-4 pb-0">sign-in face checks</p>
            {(faces.data ?? []).length === 0 ? (
              <p className="p-4 text-sm text-g500">No face check yet.</p>
            ) : (
              <table className="w-full text-left text-sm">
                <thead><tr>{["when", "match", "state"].map((h) => <th key={h} className="label p-3 font-normal">{h}</th>)}</tr></thead>
                <tbody>
                  {(faces.data ?? []).map((v) => (
                    <tr key={v.id} className="table-row">
                      <td className="p-3 font-mono text-xs">{dateTime(v.verified_at)}</td>
                      <td className="p-3 font-mono">{number(v.score, 2)}</td>
                      <td className="p-3 text-xs">{v.revoked_at ? <Chip tone="ink">{faceCheckState(v, now)}</Chip> : <span className="text-g500">{faceCheckState(v, now)}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="card overflow-x-auto">
            <p className="label p-4 pb-0">monitored shifts</p>
            {(sessions.data ?? []).length === 0 ? (
              <p className="p-4 text-sm text-g500">No shifts recorded.</p>
            ) : (
              <table className="w-full text-left text-sm">
                <thead><tr>{["started", "ended"].map((h) => <th key={h} className="label p-3 font-normal">{h}</th>)}</tr></thead>
                <tbody>
                  {(sessions.data ?? []).map((s) => (
                    <tr key={s.id} className="table-row">
                      <td className="p-3 font-mono text-xs">{dateTime(s.started_at)}</td>
                      <td className="p-3 font-mono text-xs">{s.ended_at ? dateTime(s.ended_at) : <span className="text-g500">still open</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </Section>
    </>
  );
}
