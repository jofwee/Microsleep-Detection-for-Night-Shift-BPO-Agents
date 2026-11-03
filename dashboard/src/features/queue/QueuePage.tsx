import { useState } from "react";
import { Link } from "react-router-dom";
import { ChartCard, StackedBars } from "../../components/charts";
import { Chip, Empty, ErrorNote, PageHeader, Section, Segmented, Stat, StatGrid, useAgentNames, useNow } from "../../components/ui";
import { clock, LANGUAGE_LABEL, percent, seconds, words } from "../../lib/format";
import { useAgents, useCalls } from "../../lib/queries";
import { callsByHour, callStats, SERVICE_LEVEL_SECONDS } from "../../lib/stats";

const RANGES = [{ value: 1, label: "1 h" }, { value: 8, label: "8 h" }, { value: 24, label: "24 h" }];

export function QueuePage() {
  const [hours, setHours] = useState(8);
  const now = useNow(1000);
  const calls = useCalls(hours, 500);
  const agents = useAgents();
  const nameOf = useAgentNames(agents.data);

  const all = calls.data ?? [];
  const stats = callStats(all, now);
  const waiting = all.filter((c) => c.status === "QUEUED" || c.status === "OFFERED").sort((a, b) => a.queued_at.localeCompare(b.queued_at));
  const hourly = callsByHour(all);

  return (
    <>
      <PageHeader title="queue" kicker="callers and service level" right={<Segmented value={hours} options={RANGES} onChange={setHours} />} />
      {calls.error && <ErrorNote error={calls.error} />}

      <Section n={1} title={`last ${hours} h`}>
        <StatGrid cols={4}>
          <Stat label={`answered in ${SERVICE_LEVEL_SECONDS} s`} value={percent(stats.serviceLevel)} hint="service level" />
          <Stat label="abandoned" value={percent(stats.abandonmentRate)} hint={`${stats.abandoned} of ${stats.answered + stats.abandoned} calls`} />
          <Stat label="average wait" value={clock(stats.avgWait)} hint="until an agent answered" />
          <Stat label="average talk time" value={clock(stats.avgHandle)} hint={`${stats.answered} answered`} />
        </StatGrid>
      </Section>

      <Section n={2} title="waiting now" index={2} right={<span className="label">{waiting.length} in line</span>}>
        {waiting.length === 0 ? (
          <Empty>Nobody is waiting.</Empty>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr>
                  {["waiting", "stage", "language", "problem", "mood", "offered to"].map((h) => <th key={h} className="label p-3 font-normal">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {waiting.map((c) => {
                  const w = seconds(c.queued_at, now) ?? 0;
                  const urgent = w > c.patience_s * 0.7;
                  return (
                    <tr key={c.id} className="table-row">
                      <td className="p-3 font-mono">
                        <Link to={`/calls/${c.id}`} className="link">{clock(w)}</Link>
                        {urgent && <span className="label ml-2">nearly gives up</span>}
                      </td>
                      <td className="p-3"><Chip tone={c.status === "OFFERED" ? "ink" : "plain"}>{c.status.toLowerCase()}</Chip></td>
                      <td className="p-3">{LANGUAGE_LABEL[c.language]}</td>
                      <td className="p-3 text-g500">{words(c.scenario_id)}</td>
                      <td className="p-3 text-g500">{c.mood}</td>
                      <td className="p-3">{c.status === "OFFERED" ? nameOf(c.agent_id) : "–"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section n={3} title="calls per hour" index={3}>
        {hourly.length === 0 ? (
          <Empty>No calls in this period.</Empty>
        ) : (
          <ChartCard
            title="Arrivals by hour"
            subtitle="How the calls that arrived in each hour ended"
            legend={["completed", "abandoned", "inProgress"]}
            busy={calls.isFetching}
            table={{ head: ["hour", "completed", "abandoned", "in progress"], rows: hourly.map((h) => [h.hour, h.completed, h.abandoned, h.inProgress]) }}
          >
            <StackedBars data={hourly as unknown as Record<string, number | string>[]} keys={["completed", "abandoned", "inProgress"]} />
          </ChartCard>
        )}
      </Section>
    </>
  );
}
