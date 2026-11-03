import { Link, useParams } from "react-router-dom";
import { Empty, ErrorNote, EventChip, Loading, PageHeader, Section, Stat, StatGrid, useAgentNames } from "../../components/ui";
import { clock, dateTime, LANGUAGE_LABEL, seconds, time, words } from "../../lib/format";
import { useAgents, useCall, useCallEvents, useCallTurns } from "../../lib/queries";
import { handleSeconds, waitSeconds } from "../../lib/stats";
import { callOutcome } from "./CallsPage";

export function CallDetailPage() {
  const { id } = useParams();
  const call = useCall(id);
  const turns = useCallTurns(id);
  const events = useCallEvents(id);
  const agents = useAgents();
  const nameOf = useAgentNames(agents.data);

  if (call.isLoading) return <Loading />;
  const c = call.data;
  if (!c) return <Empty>That call was not found (or it belongs to another team).</Empty>;
  const start = c.answered_at;

  return (
    <>
      <PageHeader
        title={words(c.scenario_id)}
        kicker={`call ${c.id.slice(0, 8)} · ${dateTime(c.queued_at)}`}
        right={<Link to="/calls" className="btn">all calls</Link>}
      />
      {call.error && <ErrorNote error={call.error} />}

      <Section n={1} title="summary">
        <StatGrid cols={4}>
          <Stat label="agent" value={<span className="text-xl">{nameOf(c.agent_id)}</span>} />
          <Stat label="language" value={<span className="text-xl">{LANGUAGE_LABEL[c.language]}</span>} hint={`caller mood: ${c.mood}`} />
          <Stat label="wait / talk" value={<span className="text-xl">{clock(waitSeconds(c))} / {clock(handleSeconds(c))}</span>} hint={c.offer_attempts > 1 ? `offered ${c.offer_attempts} times` : undefined} />
          <Stat label="result" value={<span className="text-xl">{callOutcome(c)}</span>} hint={c.end_reason?.replaceAll("_", " ")} />
        </StatGrid>
        {c.notes && (
          <p className="card mt-4 p-4 text-sm"><span className="label mr-2">agent's notes</span>{c.notes}</p>
        )}
      </Section>

      <Section n={2} title="transcript" index={2} right={<span className="label">{(turns.data ?? []).length} turns</span>}>
        {turns.error && <ErrorNote error={turns.error} />}
        {(turns.data ?? []).length === 0 ? (
          <Empty>No conversation yet.</Empty>
        ) : (
          <ol className="space-y-3">
            {(turns.data ?? []).map((t) => (
              <li key={t.id} className={`flex ${t.speaker === "agent" ? "justify-end" : ""}`}>
                <div className={`max-w-[85%] rounded-card border p-4 ${t.speaker === "agent" ? "bg-ink text-bg" : "bg-g50"}`}>
                  <p className={`label mb-1 ${t.speaker === "agent" ? "!text-bg opacity-60" : ""}`}>
                    {t.speaker === "agent" ? "agent" : "caller"} · {time(t.created_at)}
                  </p>
                  <p className="text-sm leading-relaxed">{t.text}</p>
                  {(t.stt_ms || t.llm_ms || t.tts_ms) && (
                    <p className={`mt-2 font-mono text-[10px] ${t.speaker === "agent" ? "opacity-60" : "text-g400"}`}>
                      {[t.stt_ms && `speech to text ${t.stt_ms} ms`, t.llm_ms && `caller ${t.llm_ms} ms`, t.tts_ms && `voice ${t.tts_ms} ms`].filter(Boolean).join(" · ")}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section n={3} title="fatigue events during the call" index={3}>
        {(events.data ?? []).length === 0 ? (
          <Empty>The monitor saw nothing unusual during this call.</Empty>
        ) : (
          <ul className="card divide-y">
            {(events.data ?? []).map((e) => (
              <li key={e.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div className="flex items-center gap-3">
                  <EventChip type={e.type} />
                  {e.duration_ms > 0 && <span className="font-mono text-xs text-g500">{(e.duration_ms / 1000).toFixed(1)} s</span>}
                </div>
                <span className="label">
                  {start ? `${clock(seconds(start, new Date(e.ts)))} into the call · ` : ""}{time(e.ts)}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="label mt-3">events are saved by the agent's app, so they can arrive a little after the call</p>
      </Section>
    </>
  );
}
