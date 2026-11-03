import { useState } from "react";
import { Link } from "react-router-dom";
import { ChartCard, FATIGUE_SERIES, RowBars, StackedBars } from "../../components/charts";
import { Empty, ErrorNote, PageHeader, Section, Segmented, Stat, StatGrid } from "../../components/ui";
import { clock, LANGUAGE_LABEL, number, percent, toCsv, words } from "../../lib/format";
import { useAgents, useCalls, useEvents, useMetrics } from "../../lib/queries";
import { callStats, countByType, eventsByHour, fatigueBy, handleSeconds, perAgent, SERVICE_LEVEL_SECONDS, waitSeconds } from "../../lib/stats";

const RANGES = [{ value: 24, label: "24 h" }, { value: 168, label: "7 d" }, { value: 720, label: "30 d" }];
const CALL_LIMIT = 500;

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export function ReportsPage() {
  const [hours, setHours] = useState(168);
  const [agentId, setAgentId] = useState("");
  const agents = useAgents();
  const events = useEvents(hours, agentId || undefined);
  const calls = useCalls(hours, CALL_LIMIT);
  const metrics = useMetrics(hours, agentId || undefined);

  const callRows = (calls.data ?? []).filter((c) => !agentId || c.agent_id === agentId);
  const ev = events.data ?? [];
  const stats = callStats(callRows);
  const counts = countByType(ev);
  const perclos = (metrics.data ?? []).filter((m) => m.perclos != null).map((m) => m.perclos as number);
  const avgPerclos = perclos.length ? perclos.reduce((a, b) => a + b, 0) / perclos.length : null;
  const hourly = eventsByHour(ev);
  const present = FATIGUE_SERIES.filter((k) => hourly.some((h) => (h[k] ?? 0) > 0));
  const people = perAgent((agents.data ?? []).filter((a) => !agentId || a.id === agentId), callRows, ev, metrics.data ?? []);
  const truncated = (calls.data ?? []).length >= CALL_LIMIT;

  const groups = (key: "language" | "scenario_id" | "mood") =>
    fatigueBy(callRows, ev, key).filter((g) => g.microsleepsPerHour != null).map((g) => ({
      label: key === "language" ? LANGUAGE_LABEL[g.key] ?? g.key : words(g.key),
      value: g.microsleepsPerHour as number,
      note: `${g.calls} calls · ${number(g.minutes, 0)} min of talk · ${g.microsleeps} microsleeps`,
    }));

  const exportEvents = () =>
    download(`events-${hours}h.csv`, toCsv(["time", "agent", "type", "duration_ms", "severity", "call_id"], ev.map((e) => [e.ts, (agents.data ?? []).find((a) => a.id === e.agent_id)?.full_name ?? e.agent_id, e.type, e.duration_ms, e.severity, e.call_id])));
  const exportCalls = () =>
    download(`calls-${hours}h.csv`, toCsv(["queued_at", "scenario", "language", "mood", "agent", "status", "wait_s", "talk_s", "disposition", "end_reason"],
      callRows.map((c) => [c.queued_at, c.scenario_id, c.language, c.mood, (agents.data ?? []).find((a) => a.id === c.agent_id)?.full_name ?? "", c.status, waitSeconds(c), handleSeconds(c), c.disposition, c.end_reason])));

  return (
    <>
      <PageHeader title="reports" kicker="how the team is doing" right={<Segmented value={hours} options={RANGES} onChange={setHours} />} />

      <div className="rise mb-8 flex flex-wrap items-end gap-3" style={{ ["--i" as string]: 1 }}>
        <label className="block">
          <span className="label">agent</span>
          <select className="input mt-1" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            <option value="">whole team</option>
            {(agents.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.full_name}</option>)}
          </select>
        </label>
        <div className="ml-auto flex gap-2">
          <button className="btn" onClick={exportEvents} disabled={ev.length === 0}>export events (csv)</button>
          <button className="btn" onClick={exportCalls} disabled={callRows.length === 0}>export calls (csv)</button>
        </div>
      </div>
      {(events.error || calls.error || metrics.error) && <ErrorNote error={events.error ?? calls.error ?? metrics.error} />}
      {truncated && <p className="label mb-6">showing the latest {CALL_LIMIT} calls of this period</p>}

      <Section n={1} title="summary">
        <StatGrid cols={5}>
          <Stat label="calls" value={stats.total} hint={`${stats.answered} answered`} />
          <Stat label={`answered in ${SERVICE_LEVEL_SECONDS} s`} value={percent(stats.serviceLevel)} />
          <Stat label="microsleeps" value={counts.MICROSLEEP ?? 0} tone={(counts.MICROSLEEP ?? 0) > 0 ? "alarm" : undefined} />
          <Stat label="yawns" value={counts.YAWN ?? 0} />
          <Stat label="eye closure" value={percent(avgPerclos, 1)} hint="average PERCLOS" />
        </StatGrid>
      </Section>

      <Section n={2} title="fatigue through the day" index={2}>
        {hourly.length === 0 ? (
          <Empty>No fatigue events in this period.</Empty>
        ) : (
          <ChartCard
            title="Events by hour of the day"
            subtitle="All days in the period added together, by the hour they happened"
            legend={present}
            busy={events.isFetching}
            table={{ head: ["hour", ...present.map((k) => k.toLowerCase().replace("_", " "))], rows: hourly.map((h) => [h.hour, ...present.map((k) => h[k] ?? 0)]) }}
          >
            <StackedBars data={hourly as unknown as Record<string, number | string>[]} keys={present} />
          </ChartCard>
        )}
      </Section>

      <Section n={3} title="fatigue and the kind of call" index={3}>
        {stats.answered === 0 ? (
          <Empty>No answered calls in this period.</Empty>
        ) : (
          <div className="grid gap-4 lg:grid-cols-3">
            {([["language", "By language"], ["scenario_id", "By problem"], ["mood", "By caller mood"]] as const).map(([key, title]) => {
              const rows = groups(key);
              return (
                <div key={key} className="card p-5">
                  <p className="text-sm font-medium">{title}</p>
                  <p className="mb-4 mt-0.5 text-xs text-g500">Microsleeps per hour of talk</p>
                  {rows.length ? <RowBars rows={rows} /> : <p className="text-xs text-g500">Not enough talk time yet.</p>}
                </div>
              );
            })}
          </div>
        )}
        <p className="label mt-3">hover a row for the number of calls behind it. Few calls means a rough number.</p>
      </Section>

      <Section n={4} title="by agent" index={4}>
        {people.length === 0 ? (
          <Empty>No agents.</Empty>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead><tr>{["agent", "calls", "talk time", "microsleeps", "yawns", "eye closure"].map((h) => <th key={h} className="label p-3 font-normal">{h}</th>)}</tr></thead>
              <tbody>
                {people.map((p) => (
                  <tr key={p.id} className="table-row">
                    <td className="p-3"><Link to={`/agents/${p.id}`} className="link">{p.name}</Link></td>
                    <td className="p-3 font-mono">{p.calls}</td>
                    <td className="p-3 font-mono">{clock(p.talkMinutes * 60)}</td>
                    <td className={`p-3 font-mono ${p.microsleeps ? "text-alarm" : ""}`}>{p.microsleeps}</td>
                    <td className="p-3 font-mono">{p.yawns}</td>
                    <td className="p-3 font-mono">{percent(p.avgPerclos, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}
