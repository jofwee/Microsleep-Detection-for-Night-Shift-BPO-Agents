import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Chip, Empty, ErrorNote, PageHeader, Section, Stat, StatGrid, useNow } from "../../components/ui";
import { clock, dateTime, percent, seconds } from "../../lib/format";
import { useCalls, useRuns, useStartRun, useStopRun } from "../../lib/queries";
import { DEFAULT_RUN, mixPercent, parseSeed, runProblems } from "../../lib/run";
import { callStats } from "../../lib/stats";
import type { RunConfig, SimulationRun } from "../../lib/types";

function NumberField({ label, value, onChange, step, hint }: { label: string; value: number; onChange: (n: number) => void; step?: number; hint?: string }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input className="input mt-1" type="number" step={step ?? 1} value={Number.isFinite(value) ? value : ""} onChange={(e) => onChange(e.target.value === "" ? Number.NaN : Number(e.target.value))} />
      {hint && <span className="mt-1 block text-xs text-g500">{hint}</span>}
    </label>
  );
}

function StartForm({ initial, seedHint }: { initial: RunConfig; seedHint: string }) {
  const [cfg, setCfg] = useState<RunConfig>(initial);
  const [seedText, setSeedText] = useState(seedHint);
  const start = useStartRun();
  const problems = runProblems(cfg);
  const seed = parseSeed(seedText);
  const mix = mixPercent(cfg.language_mix);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (problems.length || seed === "invalid") return;
    start.mutate({ config: cfg, seed });
  };
  const setMix = (k: keyof RunConfig["language_mix"], v: number) => setCfg({ ...cfg, language_mix: { ...cfg.language_mix, [k]: v } });

  return (
    <form onSubmit={submit} className="card space-y-5 p-5">
      <div className="grid gap-4 sm:grid-cols-3">
        <NumberField label="calls per minute" value={cfg.arrival_per_min} step={0.5} onChange={(n) => setCfg({ ...cfg, arrival_per_min: n })} hint="0.5 to 30" />
        <NumberField label="calls at once" value={cfg.max_concurrent} onChange={(n) => setCfg({ ...cfg, max_concurrent: n })} hint="at most, 1 to 50" />
        <NumberField label="caller patience (s)" value={cfg.patience_s} onChange={(n) => setCfg({ ...cfg, patience_s: n })} hint="then they hang up" />
      </div>
      <div>
        <p className="label">language mix (weights) · {mix.en}% English · {mix.taglish}% Taglish · {mix.tl}% Tagalog</p>
        <div className="mt-1 grid gap-4 sm:grid-cols-3">
          <NumberField label="english" value={cfg.language_mix.en} onChange={(n) => setMix("en", n)} />
          <NumberField label="taglish" value={cfg.language_mix.taglish} onChange={(n) => setMix("taglish", n)} />
          <NumberField label="tagalog" value={cfg.language_mix.tl} onChange={(n) => setMix("tl", n)} />
        </div>
      </div>
      <label className="block max-w-xs">
        <span className="label">seed (optional)</span>
        <input className="input mt-1" inputMode="numeric" value={seedText} onChange={(e) => setSeedText(e.target.value)} placeholder="random" />
        <span className="mt-1 block text-xs text-g500">The same seed brings the same callers in the same order, for repeatable experiments.</span>
      </label>
      {problems.length > 0 && <ul className="space-y-1 text-sm text-alarm">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
      {seed === "invalid" && <p className="text-sm text-alarm">The seed must be a whole number.</p>}
      {start.error && <ErrorNote error={start.error} />}
      <button className="btn btn-primary" disabled={start.isPending || problems.length > 0 || seed === "invalid"}>{start.isPending ? "starting…" : "start simulation"}</button>
    </form>
  );
}

function RunningCard({ run }: { run: SimulationRun }) {
  const now = useNow(1000);
  const calls = useCalls(72, 500);
  const stop = useStopRun();
  const mine = (calls.data ?? []).filter((c) => c.run_id === run.id);
  const s = callStats(mine, now);
  const mix = mixPercent(run.config.language_mix);

  return (
    <div className="card p-5">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Chip tone="ink"><span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-current" /> running</Chip>
          <span className="label">started {dateTime(run.started_at)} · {clock(seconds(run.started_at, now))} ago · seed {run.seed}</span>
        </div>
        <button className="btn btn-alarm" disabled={stop.isPending} onClick={() => stop.mutate(run.id)}>{stop.isPending ? "stopping…" : "stop simulation"}</button>
      </div>
      <StatGrid cols={4}>
        <Stat label="calls created" value={run.calls_created} />
        <Stat label="in line" value={s.queued + s.offered} />
        <Stat label="on a call" value={s.active + s.wrapUp} />
        <Stat label="abandoned" value={s.abandoned} />
      </StatGrid>
      <p className="mt-4 text-xs text-g500">
        {run.config.arrival_per_min} calls a minute, up to {run.config.max_concurrent} at once, callers wait {run.config.patience_s} s · {mix.en}% English, {mix.taglish}% Taglish, {mix.tl}% Tagalog
      </p>
      {stop.error && <div className="mt-3"><ErrorNote error={stop.error} /></div>}
    </div>
  );
}

export function SimulationPage() {
  const runs = useRuns();
  const calls = useCalls(72, 500);
  const running = (runs.data ?? []).find((r) => r.status === "RUNNING");
  const past = (runs.data ?? []).filter((r) => r.status === "STOPPED");
  const [reuse, setReuse] = useState<SimulationRun | null>(null);

  return (
    <>
      <PageHeader title="simulation" kicker="AI callers for your agents" />
      {runs.error && <ErrorNote error={runs.error} />}

      <Section n={1} title={running ? "running now" : "start a run"}>
        {running ? (
          <RunningCard run={running} />
        ) : (
          <StartForm key={reuse?.id ?? "new"} initial={reuse?.config ?? DEFAULT_RUN} seedHint={reuse ? String(reuse.seed) : ""} />
        )}
        {!running && <p className="label mt-3">callers reach agents who are available, with the camera on and a valid face check</p>}
      </Section>

      <Section n={2} title="earlier runs" index={2}>
        {past.length === 0 ? (
          <Empty>No runs yet.</Empty>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr>{["started", "length", "calls", "answered in 20 s", "abandoned", "settings", ""].map((h) => <th key={h} className="label p-3 font-normal">{h}</th>)}</tr>
              </thead>
              <tbody>
                {past.map((r) => {
                  const st = callStats((calls.data ?? []).filter((c) => c.run_id === r.id));
                  const known = (calls.data ?? []).some((c) => c.run_id === r.id);
                  return (
                    <tr key={r.id} className="table-row">
                      <td className="p-3 font-mono text-xs">{dateTime(r.started_at)}</td>
                      <td className="p-3 font-mono">{clock(seconds(r.started_at, r.ended_at ? new Date(r.ended_at) : new Date()))}</td>
                      <td className="p-3 font-mono">{r.calls_created}</td>
                      <td className="p-3 font-mono">{known ? percent(st.serviceLevel) : "–"}</td>
                      <td className="p-3 font-mono">{known ? percent(st.abandonmentRate) : "–"}</td>
                      <td className="p-3 text-xs text-g500">{r.config.arrival_per_min}/min · ≤{r.config.max_concurrent} · seed {r.seed}</td>
                      <td className="p-3 text-right">
                        <button className="btn !py-1" onClick={() => { setReuse(r); window.scrollTo({ top: 0, behavior: "smooth" }); }}>repeat</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="label mt-3">"repeat" fills the form with that run's settings and seed. <Link to="/calls" className="link">see the calls</Link></p>
      </Section>
    </>
  );
}
