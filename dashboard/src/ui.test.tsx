import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { passwordProblems } from "./auth/password";
import { Confirm, Modal } from "./components/Confirm";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SERIES_FILL } from "./components/charts";
import { AlertChip, EventChip, FatigueChip, StatusChip, Stat } from "./components/ui";
import type { AlertKind, EventType } from "./lib/types";

// no network in tests: the real client is never created
vi.mock("./lib/supabase", () => ({ supabase: { auth: {}, rpc: vi.fn(), from: vi.fn() } }));

const auth = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock("./auth/AuthProvider", () => ({ useAuth: () => auth.value }));

describe("password rules", () => {
  it("asks for 12 characters, upper, lower and a digit", () => {
    expect(passwordProblems("Abcdefghij12")).toEqual([]);
    expect(passwordProblems("short")).toContain("at least 12 characters");
    expect(passwordProblems("abcdefghijkl1")).toContain("an upper-case letter");
    expect(passwordProblems("ABCDEFGHIJKL1")).toContain("a lower-case letter");
    expect(passwordProblems("Abcdefghijkl")).toContain("a digit");
  });
});

describe("the one red", () => {
  const isAlarm = (el: HTMLElement) => el.className.includes("chip-alarm");

  it("is used for microsleep and identity mismatch, and for nothing else", () => {
    const events: EventType[] = ["MICROSLEEP", "LONG_BLINK", "YAWN", "HEAD_NOD", "HEAD_TILT", "DROWSY", "ABSENT", "IDENTITY_MISMATCH", "MULTIPLE_FACES", "SPOOF_SUSPECTED"];
    for (const type of events) {
      const { container, unmount } = render(<EventChip type={type} />);
      expect(isAlarm(container.firstElementChild as HTMLElement), type).toBe(type === "MICROSLEEP" || type === "IDENTITY_MISMATCH");
      unmount();
    }
    const alerts: AlertKind[] = ["IDENTITY_MISMATCH", "SPOOF_SUSPECTED", "REPEATED_MICROSLEEP", "FACE_LOCKOUT"];
    for (const kind of alerts) {
      const { container, unmount } = render(<AlertChip kind={kind} />);
      expect(isAlarm(container.firstElementChild as HTMLElement), kind).toBe(kind === "IDENTITY_MISMATCH" || kind === "REPEATED_MICROSLEEP");
      unmount();
    }
  });

  it("marks only a microsleep as a red fatigue state", () => {
    const { container: a } = render(<FatigueChip state="MICROSLEEP" />);
    const { container: b } = render(<FatigueChip state="DROWSY" />);
    const { container: c } = render(<FatigueChip state="ALERT" />);
    expect(isAlarm(a.firstElementChild as HTMLElement)).toBe(true);
    expect(isAlarm(b.firstElementChild as HTMLElement)).toBe(false);
    expect(isAlarm(c.firstElementChild as HTMLElement)).toBe(false);
  });

  it("is not used by any agent status", () => {
    for (const s of ["OFFLINE", "NOT_READY", "AVAILABLE", "RINGING", "ON_CALL", "WRAP_UP", "SYSTEM_HOLD"] as const) {
      const { container, unmount } = render(<StatusChip status={s} />);
      expect(isAlarm(container.firstElementChild as HTMLElement), s).toBe(false);
      unmount();
    }
  });

  it("is the colour of one chart series only", () => {
    const red = Object.entries(SERIES_FILL).filter(([, v]) => v.includes("alarm")).map(([k]) => k);
    expect(red).toEqual(["MICROSLEEP"]);
  });

  it("shows a stat in red only when asked to", () => {
    const { container } = render(<Stat label="microsleeps" value={3} tone="alarm" />);
    expect(container.querySelector(".text-alarm")).not.toBeNull();
    const { container: plain } = render(<Stat label="yawns" value={3} />);
    expect(plain.querySelector(".text-alarm")).toBeNull();
  });
});

describe("dialogs", () => {
  it("close on Escape and on a click outside, but not on a click inside", () => {
    const onClose = vi.fn();
    render(<Modal title="Hello" onClose={onClose}><p>inside</p></Modal>);
    fireEvent.click(screen.getByText("inside"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("confirm asks first and only acts when told to", () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(<Confirm title="Delete?" confirmLabel="delete" danger onConfirm={onConfirm} onClose={onClose}>This cannot be undone.</Confirm>);
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "cancel" }));
    expect(onClose).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("confirm cannot be pressed twice while it is working", () => {
    render(<Confirm title="x" confirmLabel="go" busy onConfirm={() => {}} onClose={() => {}}>x</Confirm>);
    expect(screen.getByRole("button", { name: "working…" })).toBeDisabled();
  });
});

describe("a crashing page", () => {
  it("shows what went wrong instead of a blank screen", () => {
    const Boom = () => {
      throw new Error("chart exploded");
    };
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    expect(screen.getByRole("alert")).toHaveTextContent("chart exploded");
    expect(screen.getByRole("button", { name: "try again" })).toBeInTheDocument();
    spy.mockRestore();
  });
});

describe("who gets in", () => {
  const renderGate = async () => {
    const { RequireTeamLeader } = await import("./auth/pages");
    return render(
      <MemoryRouter initialEntries={["/secret"]}>
        <Routes>
          <Route path="/login" element={<p>the sign-in page</p>} />
          <Route path="/secret" element={<RequireTeamLeader><p>the dashboard</p></RequireTeamLeader>} />
        </Routes>
      </MemoryRouter>,
    );
  };
  const leader = { id: "u", role: "team_leader", active: true, must_change_password: false, full_name: "Dana", team_id: "t" };

  it("waits while the session loads", async () => {
    auth.value = { loading: true, session: null, profile: null };
    await renderGate();
    expect(screen.getByText("loading…")).toBeInTheDocument();
  });

  it("sends a signed-out visitor to the sign-in page", async () => {
    auth.value = { loading: false, session: null, profile: null };
    await renderGate();
    expect(screen.getByText("the sign-in page")).toBeInTheDocument();
  });

  it("turns an agent away", async () => {
    auth.value = { loading: false, session: {}, profile: { ...leader, role: "agent" }, signOut: vi.fn() };
    await renderGate();
    expect(screen.getByText("team leaders only")).toBeInTheDocument();
    expect(screen.queryByText("the dashboard")).toBeNull();
  });

  it("turns away a deactivated leader and an account without a profile", async () => {
    auth.value = { loading: false, session: {}, profile: { ...leader, active: false }, signOut: vi.fn() };
    const first = await renderGate();
    expect(screen.getByText("team leaders only")).toBeInTheDocument();
    first.unmount();
    auth.value = { loading: false, session: {}, profile: null, signOut: vi.fn() };
    await renderGate();
    expect(screen.getByText("team leaders only")).toBeInTheDocument();
  });

  it("makes a leader on a temporary password choose a new one first", async () => {
    auth.value = { loading: false, session: {}, profile: { ...leader, must_change_password: true }, signOut: vi.fn(), refreshProfile: vi.fn() };
    await renderGate();
    expect(screen.getByText("new password", { selector: "h1" })).toBeInTheDocument();
    expect(screen.queryByText("the dashboard")).toBeNull();
  });

  it("lets a leader in", async () => {
    auth.value = { loading: false, session: {}, profile: leader };
    await renderGate();
    expect(screen.getByText("the dashboard")).toBeInTheDocument();
  });
});

describe("sign-in form", () => {
  it("sends the typed email and password and shows the refusal", async () => {
    const signIn = vi.fn().mockResolvedValue("Wrong email or password.");
    auth.value = { loading: false, session: null, profile: null, signIn };
    const { LoginPage } = await import("./auth/pages");
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("email"), { target: { value: "dana@example.com" } });
    fireEvent.change(screen.getByLabelText("password"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "sign in" }));
    expect(signIn).toHaveBeenCalledWith("dana@example.com", "nope");
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password.");
  });
});
