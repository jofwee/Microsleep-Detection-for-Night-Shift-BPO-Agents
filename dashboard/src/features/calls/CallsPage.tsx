import { useState } from "react";
import { Link } from "react-router-dom";
import { Chip, Empty, ErrorNote, PageHeader, Section, Segmented, useAgentNames } from "../../components/ui";
import { clock, dateTime, LANGUAGE_LABEL, words } from "../../lib/format";
import { useAgents, useCalls } from "../../lib/queries";
import { handleSeconds, waitSeconds } from "../../lib/stats";
import type { Call } from "../../lib/types";

const RANGES = [{ value: 8, label: "8 h" }, { value: 24, label: "24 h" }, { value: 168, label: "7 d" }];

export function callOutcome(c: Call): string {
  if (c.status === "COMPLETED") return c.disposition ?? "completed";
  return c.status.toLowerCase().replace("_", "-");
}

export function CallsPage() {
  const [hours, setHours] = useState(24);
  const [status, setStatus] = useState("");
  const [language, setLanguage] = useState("");
  const [agent, setAgent] = useState("");
  const calls = useCalls(hours, 500);
  const agents = useAgents();
  const nameOf = useAgentNames(agents.data);

  const shown = (calls.data ?? []).filter((c) => (!status || c.status === status) && (!language || c.language === language) && (!agent || c.agent_id === agent));

  return (
    <>
      <PageHeader title="calls" kicker="every call, with its transcript" right={<Segmented value={hours} options={RANGES} onChange={setHours} />} />

      <div className="rise mb-8 flex flex-wrap items-end gap-3" style={{ ["--i" as string]: 1 }}>
        <label className="block">
          <span className="label">stage</span>
          <select className="input mt-1" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">all</option>
            {["QUEUED", "OFFERED", "ACTIVE", "WRAP_UP", "COMPLETED", "ABANDONED"].map((s) => <option key={s} value={s}>{s.toLowerCase().replace("_", "-")}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="label">language</span>
          <select className="input mt-1" value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="">all</option>
            {Object.entries(LANGUAGE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="label">agent</span>
          <select className="input mt-1" value={agent} onChange={(e) => setAgent(e.target.value)}>
            <option value="">all</option>
            {(agents.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.full_name}</option>)}
          </select>
        </label>
        <span className="label ml-auto">{shown.length} calls</span>
      </div>

      {calls.error && <ErrorNote error={calls.error} />}
      <Section n={1} title="call log" index={2}>
        {shown.length === 0 ? (
          <Empty>No calls match.</Empty>
        ) : (
          <div className={`card overflow-x-auto transition-opacity ${calls.isFetching ? "opacity-60" : ""}`}>
            <table className="w-full text-left text-sm">
              <thead>
                <tr>{["when", "problem", "language", "mood", "agent", "wait", "talk", "result"].map((h) => <th key={h} className="label p-3 font-normal">{h}</th>)}</tr>
              </thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.id} className="table-row">
                    <td className="p-3 font-mono text-xs"><Link to={`/calls/${c.id}`} className="link">{dateTime(c.queued_at)}</Link></td>
                    <td className="p-3">{words(c.scenario_id)}</td>
                    <td className="p-3 text-g500">{LANGUAGE_LABEL[c.language]}</td>
                    <td className="p-3 text-g500">{c.mood}</td>
                    <td className="p-3">{nameOf(c.agent_id)}</td>
                    <td className="p-3 font-mono">{clock(waitSeconds(c))}</td>
                    <td className="p-3 font-mono">{clock(handleSeconds(c))}</td>
                    <td className="p-3"><Chip tone={c.status === "ABANDONED" || c.disposition === "escalated" ? "ink" : "plain"}>{callOutcome(c)}</Chip></td>
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
