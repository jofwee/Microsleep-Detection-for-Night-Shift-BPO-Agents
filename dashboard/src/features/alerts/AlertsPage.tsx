import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertChip, Empty, ErrorNote, PageHeader, Section, Segmented, useAgentNames, useNow } from "../../components/ui";
import { ago, dateTime } from "../../lib/format";
import { useAckAlert, useAgents, useAlerts } from "../../lib/queries";

export function AlertsPage() {
  const [view, setView] = useState<"open" | "all">("open");
  const now = useNow(15_000);
  const alerts = useAlerts(200);
  const agents = useAgents();
  const nameOf = useAgentNames(agents.data);
  const ack = useAckAlert();

  const all = alerts.data ?? [];
  const shown = view === "open" ? all.filter((a) => !a.acknowledged_at) : all;
  const open = all.filter((a) => !a.acknowledged_at).length;

  return (
    <>
      <PageHeader
        title="alerts"
        kicker="things that need a person"
        right={<Segmented value={view} options={[{ value: "open", label: `open ${open}` }, { value: "all", label: "all" }]} onChange={setView} />}
      />
      {(alerts.error || ack.error) && <ErrorNote error={alerts.error ?? ack.error} />}

      <Section n={1} title={view === "open" ? "waiting for you" : "everything"}>
        {shown.length === 0 ? (
          <Empty>{view === "open" ? "No open alerts." : "No alerts yet."}</Empty>
        ) : (
          <ul className="card divide-y">
            {shown.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-3">
                    <AlertChip kind={a.kind} />
                    <Link to={`/agents/${a.agent_id}`} className="link text-sm">{nameOf(a.agent_id)}</Link>
                  </div>
                  <p className="text-sm text-g500">{a.message}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="label" title={dateTime(a.created_at)}>{ago(a.created_at, now)}</span>
                  {a.acknowledged_at ? (
                    <span className="label">acknowledged {ago(a.acknowledged_at, now)}</span>
                  ) : (
                    <button className="btn" disabled={ack.isPending} onClick={() => ack.mutate(a.id)}>acknowledge</button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}
