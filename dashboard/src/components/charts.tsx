// Charts, following the dataviz rules for this monochrome design: thin marks (bars <= 24px, 2px lines), 4px
// rounded data ends, a 2px surface gap between stacked segments, hairline recessive grid, a legend whenever
// there are two or more series, a tooltip on hover, a table view, and never two axes on one plot.
// Colours are ink and the gray ramp (theme tokens, so dark mode follows); the single exception is the red of MICROSLEEP.
import { useState, type ReactNode } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { EventType } from "../lib/types";

/** Series colours by entity (never by rank), so filtering a series out never repaints the others. */
export const SERIES_FILL: Record<string, string> = {
  MICROSLEEP: "var(--alarm)",
  YAWN: "var(--ink)",
  HEAD_NOD: "var(--g600)",
  HEAD_TILT: "var(--g400)",
  LONG_BLINK: "var(--g300)",
  DROWSY: "var(--g500)",
  completed: "var(--ink)",
  abandoned: "var(--g400)",
  inProgress: "var(--g200)",
};

export const SERIES_LABEL: Record<string, string> = {
  MICROSLEEP: "microsleep", YAWN: "yawn", HEAD_NOD: "head nod", HEAD_TILT: "head tilt", LONG_BLINK: "long blink", DROWSY: "drowsy",
  completed: "completed", abandoned: "abandoned", inProgress: "in progress",
};

const AXIS = { fontSize: 10, fontFamily: "var(--font-mono)", fill: "var(--g400)" } as const;

function Legend({ keys }: { keys: string[] }) {
  return (
    <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1" aria-label="legend">
      {keys.map((k) => (
        <li key={k} className="flex items-center gap-1.5 text-xs text-g500">
          <span className="inline-block h-2 w-2 rounded-[2px]" style={{ background: SERIES_FILL[k] }} />
          {SERIES_LABEL[k] ?? k}
        </li>
      ))}
    </ul>
  );
}

interface TipProps {
  active?: boolean;
  payload?: { name?: string; dataKey?: string; value?: number | string; color?: string }[];
  label?: string;
  format?: (key: string, value: number | string) => string;
  names?: Record<string, string>;
}

/** One tooltip, every series: the value leads and the name follows. */
function Tip({ active, payload, label, format, names }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-mid border bg-bg px-3 py-2 text-xs" style={{ boxShadow: "var(--shadow-hover)" }}>
      <p className="label mb-1">{label}</p>
      {payload.map((p) => {
        const key = String(p.dataKey);
        return (
          <p key={key} className="flex items-center gap-2 py-0.5">
            <span className="inline-block h-0.5 w-3" style={{ background: p.color }} />
            <span className="font-mono text-sm font-semibold">{format ? format(key, p.value ?? "") : String(p.value)}</span>
            <span className="text-g500">{names?.[key] ?? SERIES_LABEL[key] ?? key}</span>
          </p>
        );
      })}
    </div>
  );
}

/** A chart card: title, subtitle, a chart/table switch (the table is the accessible view of the same numbers). */
export function ChartCard({ title, subtitle, legend, table, children, busy }: {
  title: string; subtitle?: string; legend?: string[]; table?: { head: string[]; rows: (string | number)[][] }; children: ReactNode; busy?: boolean;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <figure className="card p-5">
      <figcaption className="mb-4 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-medium">{title}</p>
          {subtitle && <p className="mt-0.5 text-xs text-g500">{subtitle}</p>}
        </div>
        {table && (
          <button className="label hover:text-ink" onClick={() => setView(view === "chart" ? "table" : "chart")} aria-pressed={view === "table"}>
            {view === "chart" ? "view table" : "view chart"}
          </button>
        )}
      </figcaption>
      {legend && legend.length > 1 && view === "chart" && <Legend keys={legend} />}
      <div className={`transition-opacity ${busy ? "opacity-50" : ""}`}>
        {view === "table" && table ? (
          <div className="max-h-64 overflow-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr>{table.head.map((h) => <th key={h} className="label pb-2 pr-3 font-normal">{h}</th>)}</tr>
              </thead>
              <tbody>
                {table.rows.map((r, i) => (
                  <tr key={i} className="table-row">{r.map((c, j) => <td key={j} className="py-1.5 pr-3 font-mono">{c}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          children
        )}
      </div>
    </figure>
  );
}

export function StackedBars({ data, keys, xKey = "hour", height = 220 }: { data: Record<string, number | string | undefined>[]; keys: string[]; xKey?: string; height?: number }) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 6, right: 4, bottom: 0, left: -18 }}>
          <CartesianGrid vertical={false} stroke="var(--g200)" />
          <XAxis dataKey={xKey} tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--g200)" }} />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} allowDecimals={false} />
          <Tooltip cursor={{ fill: "var(--g100)", opacity: 0.6 }} content={<Tip />} />
          {keys.map((k, i) => (
            <Bar
              key={k}
              dataKey={k}
              stackId="a"
              fill={SERIES_FILL[k] ?? "var(--ink)"}
              stroke="var(--bg)"
              strokeWidth={2} /* the 2px surface gap between stacked segments */
              maxBarSize={24}
              radius={i === keys.length - 1 ? [4, 4, 0, 0] : 0}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** One series as a 2px line with a 10% wash. One axis only: other measures get their own chart. */
export function LineSeries({ data, dataKey, xKey = "minute", height = 160, name, format, domain }: {
  data: Record<string, number | string | null>[]; dataKey: string; xKey?: string; height?: number; name: string;
  format?: (v: number) => string; domain?: [number | "auto", number | "auto"];
}) {
  const fmt = format ?? ((v: number) => String(Math.round(v * 100) / 100));
  const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 20, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--g200)" />
          <XAxis dataKey={xKey} tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--g200)" }} tickFormatter={time} minTickGap={36} />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} domain={domain ?? ["auto", "auto"]} tickFormatter={(v: number) => fmt(v)} width={52} />
          <Tooltip
            cursor={{ stroke: "var(--g300)", strokeWidth: 1 }}
            content={<Tip format={(_k, v) => fmt(Number(v))} names={{ [dataKey]: name }} />}
            labelFormatter={(l) => time(String(l))}
          />
          <Area type="monotone" dataKey={dataKey} stroke="var(--ink)" strokeWidth={2} fill="var(--ink)" fillOpacity={0.1} dot={false} activeDot={{ r: 4, fill: "var(--ink)", stroke: "var(--bg)", strokeWidth: 2 }} isAnimationActive={false} connectNulls />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal bars for one measure across named rows, built in HTML (single colour, value at the tip). */
export function RowBars({ rows, unit = "" }: { rows: { label: string; value: number; note?: string }[]; unit?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return null;
  return (
    <ul className="space-y-3">
      {rows.map((r) => (
        <li key={r.label} className="grid grid-cols-[9rem_1fr_auto] items-center gap-3 text-xs" title={r.note}>
          <span className="truncate text-g500">{r.label}</span>
          <span className="h-2 rounded-r-[4px] bg-g100">
            <span className="block h-2 rounded-r-[4px] bg-ink" style={{ width: `${Math.max(2, (r.value / max) * 100)}%` }} />
          </span>
          <span className="font-mono text-sm font-semibold">{Number.isInteger(r.value) ? r.value : r.value.toFixed(1)}{unit}</span>
        </li>
      ))}
    </ul>
  );
}

/** One whole split into parts (time in each status): a thin segmented bar with 2px gaps and a legend. */
export function ShareBar({ parts }: { parts: { key: string; label: string; share: number; fill?: string }[] }) {
  const shown = parts.filter((p) => p.share > 0);
  if (!shown.length) return null;
  return (
    <div>
      <div className="flex h-3 overflow-hidden rounded-[6px]" role="img" aria-label={shown.map((p) => `${p.label} ${Math.round(p.share * 100)}%`).join(", ")}>
        {shown.map((p, i) => (
          <span key={p.key} title={`${p.label} ${Math.round(p.share * 100)}%`} style={{ width: `${p.share * 100}%`, background: p.fill ?? "var(--ink)", marginLeft: i ? 2 : 0 }} />
        ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
        {shown.map((p) => (
          <li key={p.key} className="flex items-center gap-1.5 text-xs text-g500">
            <span className="inline-block h-2 w-2 rounded-[2px]" style={{ background: p.fill ?? "var(--ink)" }} />
            {p.label} <span className="font-mono text-ink">{Math.round(p.share * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export const FATIGUE_SERIES: EventType[] = ["MICROSLEEP", "YAWN", "HEAD_NOD", "HEAD_TILT", "LONG_BLINK", "DROWSY"];
