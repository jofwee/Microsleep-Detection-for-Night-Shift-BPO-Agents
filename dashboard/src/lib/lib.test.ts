import { describe, expect, it } from "vitest";
import { ago, clock, csvCell, percent, seconds, toCsv, words } from "./format";
import {
  agentCounts, callStats, countByType, eventsByHour, fatigueBy, handleSeconds, metricSeries, statusSeconds, waitSeconds, workShare,
} from "./stats";
import type { AgentStatus, Call, EventRow, MetricRow, StatusLogRow } from "./types";

const T0 = new Date("2026-11-04T10:00:00Z");
const at = (s: number) => new Date(T0.getTime() + s * 1000).toISOString();

function call(over: Partial<Call>): Call {
  return {
    id: "c", run_id: null, team_id: "t", scenario_id: "internet_down", language: "en", mood: "calm", status: "COMPLETED",
    agent_id: "a", queued_at: at(0), offered_at: at(1), answered_at: at(10), ended_at: at(70), wrap_up_ended_at: at(80),
    offer_attempts: 1, patience_s: 90, disposition: "resolved", end_reason: "caller_satisfied", notes: null, ...over,
  };
}

describe("format", () => {
  it("formats clocks", () => {
    expect(clock(75)).toBe("1:15");
    expect(clock(3725)).toBe("1:02:05");
    expect(clock(0)).toBe("0:00");
    expect(clock(null)).toBe("–");
  });
  it("says how long ago", () => {
    const now = new Date("2026-11-04T12:00:00Z");
    expect(ago("2026-11-04T11:59:58Z", now)).toBe("just now");
    expect(ago("2026-11-04T11:59:30Z", now)).toBe("30 s ago");
    expect(ago("2026-11-04T11:55:00Z", now)).toBe("5 min ago");
    expect(ago("2026-11-04T09:00:00Z", now)).toBe("3 h ago");
    expect(ago("2026-11-02T12:00:00Z", now)).toBe("2 d ago");
    expect(ago(null, now)).toBe("never");
  });
  it("never goes negative when the clock is a little behind", () => {
    expect(seconds("2026-11-04T12:00:05Z", new Date("2026-11-04T12:00:00Z"))).toBe(0);
  });
  it("formats percentages and words", () => {
    expect(percent(0.256)).toBe("26%");
    expect(percent(0.256, 1)).toBe("25.6%");
    expect(percent(null)).toBe("–");
    expect(words("internet_down")).toBe("internet down");
  });
  it("quotes csv cells that need it", () => {
    expect(csvCell("plain")).toBe("plain");
    expect(csvCell("a,b")).toBe('"a,b"');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell(null)).toBe("");
    expect(toCsv(["a", "b"], [[1, "x,y"]])).toBe('a,b\n1,"x,y"');
  });
});

describe("call stats", () => {
  it("measures wait and handle time", () => {
    const c = call({});
    expect(waitSeconds(c)).toBe(10);
    expect(handleSeconds(c)).toBe(60);
    const abandoned = call({ status: "ABANDONED", answered_at: null, ended_at: at(45) });
    expect(waitSeconds(abandoned)).toBe(45);
    expect(handleSeconds(abandoned)).toBeNull();
  });

  it("computes service level and abandonment from settled calls only", () => {
    const calls = [
      call({ id: "1" }), // answered after 10 s: in time
      call({ id: "2", answered_at: at(30), ended_at: at(90) }), // 30 s: too slow
      call({ id: "3", status: "ABANDONED", answered_at: null, ended_at: at(90) }),
      call({ id: "4", status: "QUEUED", answered_at: null, ended_at: null, queued_at: at(100) }), // still waiting
    ];
    const s = callStats(calls, new Date(at(160)));
    expect(s.answered).toBe(2);
    expect(s.abandoned).toBe(1);
    expect(s.serviceLevel).toBeCloseTo(1 / 3);
    expect(s.abandonmentRate).toBeCloseTo(1 / 3);
    expect(s.avgWait).toBe(20);
    expect(s.queued).toBe(1);
    expect(s.longestWaiting).toBe(60);
  });

  it("has no rates until a call has settled", () => {
    const s = callStats([call({ status: "QUEUED", answered_at: null, ended_at: null })], new Date(at(5)));
    expect(s.serviceLevel).toBeNull();
    expect(s.abandonmentRate).toBeNull();
    expect(s.avgWait).toBeNull();
  });

  it("counts calls by stage", () => {
    const s = callStats([
      call({ status: "OFFERED", answered_at: null, ended_at: null }),
      call({ status: "ACTIVE", ended_at: null }),
      call({ status: "WRAP_UP" }),
    ]);
    expect([s.offered, s.active, s.wrapUp]).toEqual([1, 1, 1]);
  });
});

describe("agents", () => {
  it("counts every status, including those nobody has", () => {
    const row = (status: AgentStatus["status"]) => ({ status }) as AgentStatus;
    const c = agentCounts([row("AVAILABLE"), row("AVAILABLE"), row("ON_CALL")]);
    expect(c.AVAILABLE).toBe(2);
    expect(c.ON_CALL).toBe(1);
    expect(c.SYSTEM_HOLD).toBe(0);
  });
});

describe("events", () => {
  const ev = (type: EventRow["type"], ts: string, call_id: string | null = null): EventRow => ({
    id: 1, session_id: "s", agent_id: "a", type, ts, duration_ms: 0, value: null, severity: "info", call_id,
  });

  it("buckets by hour and keeps the quiet hours in between", () => {
    const h = (hour: number) => { const d = new Date(2026, 10, 4, hour, 30); return d.toISOString(); };
    const out = eventsByHour([ev("YAWN", h(9)), ev("YAWN", h(9)), ev("MICROSLEEP", h(12)), ev("ABSENT", h(10))]);
    expect(out.map((b) => b.hour)).toEqual(["09:00", "10:00", "11:00", "12:00"]);
    expect(out[0].YAWN).toBe(2);
    expect(out[3].MICROSLEEP).toBe(1);
    expect(out[1].ABSENT).toBeUndefined(); // not a fatigue type
    expect(eventsByHour([])).toEqual([]);
  });

  it("counts events by type", () => {
    expect(countByType([ev("YAWN", at(0)), ev("YAWN", at(1)), ev("DROWSY", at(2))])).toEqual({ YAWN: 2, DROWSY: 1 });
  });

  it("relates fatigue to what the agent was doing", () => {
    const calls = [
      call({ id: "en1", language: "en", answered_at: at(0), ended_at: at(1800) }), // 30 min
      call({ id: "tg1", language: "taglish", answered_at: at(0), ended_at: at(1800) }),
      call({ id: "tg2", language: "taglish", answered_at: at(0), ended_at: at(1800) }),
    ];
    const events = [ev("MICROSLEEP", at(5), "tg1"), ev("MICROSLEEP", at(6), "tg2"), ev("YAWN", at(7), "tg1"), ev("MICROSLEEP", at(8), null)];
    const groups = fatigueBy(calls, events, "language");
    const taglish = groups.find((g) => g.key === "taglish")!;
    expect(taglish.calls).toBe(2);
    expect(taglish.microsleeps).toBe(2);
    expect(taglish.yawns).toBe(1);
    expect(taglish.microsleepsPerHour).toBeCloseTo(2);
    expect(groups.find((g) => g.key === "en")!.microsleeps).toBe(0);
    expect(groups[0].key).toBe("taglish"); // most calls first; the event without a call is ignored
  });

  it("does not divide by almost nothing", () => {
    const g = fatigueBy([call({ answered_at: at(0), ended_at: at(20) })], [ev("MICROSLEEP", at(1), "c")], "language")[0];
    expect(g.microsleepsPerHour).toBeNull();
  });
});

describe("metrics and time in status", () => {
  it("orders the metric series by time", () => {
    const row = (minute: string, perclos: number): MetricRow => ({ session_id: "s", minute, agent_id: "a", blink_rate: 15, perclos, mean_ear: 0.3, yawns: 0, nods: 0, status: null });
    expect(metricSeries([row(at(120), 0.2), row(at(0), 0.1)]).map((p) => p.perclos)).toEqual([0.1, 0.2]);
  });

  it("adds up time in each status inside the window and closes open rows at the end", () => {
    const log: StatusLogRow[] = [
      { id: 1, agent_id: "a", status: "AVAILABLE", reason: null, from_ts: at(-100), to_ts: at(60) },
      { id: 2, agent_id: "a", status: "ON_CALL", reason: null, from_ts: at(60), to_ts: at(180) },
      { id: 3, agent_id: "a", status: "WRAP_UP", reason: null, from_ts: at(180), to_ts: null },
    ];
    const s = statusSeconds(log, T0, new Date(at(240)));
    expect(s.AVAILABLE).toBe(60); // the part before the window does not count
    expect(s.ON_CALL).toBe(120);
    expect(s.WRAP_UP).toBe(60);
    const share = workShare(s);
    expect(share.ON_CALL).toBeCloseTo(0.5);
    expect(workShare({})).toEqual({});
  });
});

describe("calls per hour", () => {
  it("counts how calls ended and keeps quiet hours", async () => {
    const { callsByHour } = await import("./stats");
    const h = (hour: number) => new Date(2026, 10, 4, hour, 15).toISOString();
    const out = callsByHour([
      call({ queued_at: h(9) }),
      call({ queued_at: h(9), status: "ABANDONED", answered_at: null }),
      call({ queued_at: h(11), status: "ACTIVE" }),
    ]);
    expect(out.map((b) => b.hour)).toEqual(["09:00", "10:00", "11:00"]);
    expect(out[0]).toMatchObject({ completed: 1, abandoned: 1, inProgress: 0 });
    expect(out[1]).toMatchObject({ completed: 0, abandoned: 0, inProgress: 0 });
    expect(out[2].inProgress).toBe(1);
    expect(callsByHour([])).toEqual([]);
  });
});

describe("simulation form", () => {
  it("accepts the defaults and refuses what the database would refuse", async () => {
    const { DEFAULT_RUN, runProblems, parseSeed, mixPercent } = await import("./run");
    expect(runProblems(DEFAULT_RUN)).toEqual([]);
    expect(runProblems({ ...DEFAULT_RUN, arrival_per_min: 60 })).toHaveLength(1);
    expect(runProblems({ ...DEFAULT_RUN, arrival_per_min: 0.4 })).toHaveLength(1);
    expect(runProblems({ ...DEFAULT_RUN, max_concurrent: 2.5 })).toHaveLength(1);
    expect(runProblems({ ...DEFAULT_RUN, max_concurrent: 51 })).toHaveLength(1);
    expect(runProblems({ ...DEFAULT_RUN, patience_s: 5 })).toHaveLength(1);
    expect(runProblems({ ...DEFAULT_RUN, language_mix: { en: 0, taglish: 0, tl: 0 } })).toHaveLength(1);
    expect(runProblems({ ...DEFAULT_RUN, language_mix: { en: -1, taglish: 5, tl: 5 } })).toHaveLength(1);
    expect(runProblems({ ...DEFAULT_RUN, arrival_per_min: Number.NaN })).toHaveLength(1);
    expect(parseSeed("")).toBeNull();
    expect(parseSeed(" 7 ")).toBe(7);
    expect(parseSeed("seven")).toBe("invalid");
    expect(parseSeed("-3")).toBe("invalid");
    expect(mixPercent({ en: 40, taglish: 40, tl: 20 })).toEqual({ en: 40, taglish: 40, tl: 20 });
    expect(mixPercent({ en: 1, taglish: 1, tl: 0 })).toEqual({ en: 50, taglish: 50, tl: 0 });
    expect(mixPercent({ en: 0, taglish: 0, tl: 0 })).toEqual({ en: 0, taglish: 0, tl: 0 });
  });
});

describe("per agent", () => {
  it("summarises each agent separately", async () => {
    const { perAgent } = await import("./stats");
    const metric = (agent_id: string, perclos: number | null): MetricRow => ({ session_id: "s", minute: at(0), agent_id, blink_rate: 15, perclos, mean_ear: 0.3, yawns: 0, nods: 0, status: null });
    const ev = (agent_id: string, type: EventRow["type"]): EventRow => ({ id: 1, session_id: "s", agent_id, type, ts: at(0), duration_ms: 0, value: null, severity: "info", call_id: null });
    const out = perAgent(
      [{ id: "a", full_name: "Ana" }, { id: "b", full_name: "Ben" }],
      [call({ agent_id: "a", answered_at: at(0), ended_at: at(600) }), call({ agent_id: "a", answered_at: at(0), ended_at: at(1200) })],
      [ev("a", "MICROSLEEP"), ev("a", "YAWN"), ev("a", "YAWN"), ev("b", "YAWN")],
      [metric("a", 0.1), metric("a", 0.3), metric("a", null), metric("b", 0.5)],
    );
    expect(out[0]).toMatchObject({ name: "Ana", calls: 2, microsleeps: 1, yawns: 2 });
    expect(out[0].talkMinutes).toBeCloseTo(30);
    expect(out[0].avgPerclos).toBeCloseTo(0.2);
    expect(out[1]).toMatchObject({ name: "Ben", calls: 0, talkMinutes: 0, microsleeps: 0, yawns: 1 });
    expect(out[1].avgPerclos).toBe(0.5);
    expect(perAgent([{ id: "z", full_name: "Zed" }], [], [], [])[0].avgPerclos).toBeNull();
  });
});
