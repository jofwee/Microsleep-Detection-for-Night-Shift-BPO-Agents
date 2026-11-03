import { Link } from "react-router-dom";
import { Chip, Empty, ErrorNote, FatigueChip, PageHeader, Section, StatusChip, useNow } from "../../components/ui";
import { ago } from "../../lib/format";
import { useAgents, useAgentStatus, useEvents } from "../../lib/queries";

export function AgentsPage() {
  const now = useNow(15_000);
  const agents = useAgents();
  const statuses = useAgentStatus();
  const events = useEvents(24);
  const byId = new Map((statuses.data ?? []).map((s) => [s.agent_id, s]));

  const count = (id: string, type: string) => (events.data ?? []).filter((e) => e.agent_id === id && e.type === type).length;

  return (
    <>
      <PageHeader title="agents" kicker="one page per person" />
      {(agents.error || statuses.error) && <ErrorNote error={agents.error ?? statuses.error} />}
      <Section n={1} title="your team">
        {(agents.data ?? []).length === 0 ? (
          <Empty>No agents yet. Create accounts on the team page.</Empty>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr>{["agent", "status", "monitor", "microsleeps 24 h", "yawns 24 h", "face enrolled", "last signal"].map((h) => <th key={h} className="label p-3 font-normal">{h}</th>)}</tr>
              </thead>
              <tbody>
                {(agents.data ?? []).map((a) => {
                  const s = byId.get(a.id);
                  return (
                    <tr key={a.id} className="table-row">
                      <td className="p-3"><Link to={`/agents/${a.id}`} className="link font-medium">{a.full_name}</Link></td>
                      <td className="p-3">{!a.active ? <Chip>deactivated</Chip> : <StatusChip status={s?.status ?? "OFFLINE"} />}</td>
                      <td className="p-3">{s && s.status !== "OFFLINE" && s.status !== "NOT_READY" ? <FatigueChip state={s.fatigue_state} /> : <span className="text-g400">–</span>}</td>
                      <td className="p-3 font-mono">{count(a.id, "MICROSLEEP")}</td>
                      <td className="p-3 font-mono">{count(a.id, "YAWN")}</td>
                      <td className="p-3">{a.consent_at ? <Chip>yes</Chip> : <Chip tone="ink">not yet</Chip>}</td>
                      <td className="p-3 label">{ago(s?.last_heartbeat_at, now)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}
