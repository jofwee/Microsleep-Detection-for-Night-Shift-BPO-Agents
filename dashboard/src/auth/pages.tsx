import { useState, type FormEvent, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { useAuth } from "./AuthProvider";
import { passwordProblems } from "./password";

function Frame({ children }: { children: ReactNode }) {
  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      <div className="halftone absolute right-0 top-0 h-[420px] w-[620px]" aria-hidden />
      <div className="relative w-full max-w-sm">{children}</div>
    </main>
  );
}

export function LoginPage() {
  const { session, profile, signIn, loading } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!loading && session && profile) return <Navigate to={(location.state as { from?: string } | null)?.from ?? "/"} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(await signIn(email, password));
    setBusy(false);
  };

  return (
    <Frame>
      <div className="rise" style={{ ["--i" as string]: 0 }}>
        <p className="label mb-3">team leaders</p>
        <h1 className="page-title">call desk</h1>
        <p className="mt-3 text-sm text-g500">Live view of your team, the call queue and the fatigue monitor.</p>
      </div>
      <form onSubmit={submit} className="card rise mt-8 space-y-4 p-5" style={{ ["--i" as string]: 1 }}>
        <label className="block">
          <span className="label">email</span>
          <input className="input mt-1" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
        </label>
        <label className="block">
          <span className="label">password</span>
          <input className="input mt-1" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p role="alert" className="text-sm text-alarm">{error}</p>}
        <button className="btn btn-primary w-full" disabled={busy || !email || !password}>
          {busy ? "signing in…" : "sign in"}
        </button>
      </form>
    </Frame>
  );
}

export function ChangePasswordPage() {
  const { refreshProfile, signOut } = useAuth();
  const [one, setOne] = useState("");
  const [two, setTwo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problems = passwordProblems(one);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (problems.length) return setError(`The password needs ${problems.join(", ")}.`);
    if (one !== two) return setError("The two passwords do not match.");
    setBusy(true);
    setError(null);
    const { error: upErr } = await supabase.auth.updateUser({ password: one });
    if (upErr) {
      setBusy(false);
      return setError(upErr.message);
    }
    const { error: ackErr } = await supabase.rpc("ack_password_changed");
    setBusy(false);
    if (ackErr) return setError(ackErr.message);
    await refreshProfile();
  };

  return (
    <Frame>
      <p className="label mb-3">first sign-in</p>
      <h1 className="page-title">new password</h1>
      <p className="mt-3 text-sm text-g500">You are using a temporary password. Choose your own to continue.</p>
      <form onSubmit={submit} className="card mt-8 space-y-4 p-5">
        <label className="block">
          <span className="label">new password</span>
          <input className="input mt-1" type="password" autoComplete="new-password" value={one} onChange={(e) => setOne(e.target.value)} />
        </label>
        <label className="block">
          <span className="label">repeat it</span>
          <input className="input mt-1" type="password" autoComplete="new-password" value={two} onChange={(e) => setTwo(e.target.value)} />
        </label>
        <p className="label">{problems.length ? `still needs: ${problems.join(" · ")}` : "meets the rules"}</p>
        {error && <p role="alert" className="text-sm text-alarm">{error}</p>}
        <div className="flex gap-2">
          <button className="btn btn-primary" disabled={busy}>save password</button>
          <button type="button" className="btn" onClick={() => void signOut()}>sign out</button>
        </div>
      </form>
    </Frame>
  );
}

/** Only an active team leader gets past this. Agents sign in on the desktop app, not here. */
export function RequireTeamLeader({ children }: { children: ReactNode }) {
  const { session, profile, loading, signOut } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <Frame>
        <p className="label">loading…</p>
      </Frame>
    );
  }
  if (!session) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (!profile || !profile.active || profile.role !== "team_leader") {
    return (
      <Frame>
        <p className="label mb-3">not allowed</p>
        <h1 className="page-title">team leaders only</h1>
        <p className="mt-3 text-sm text-g500">
          This dashboard is for team leaders. Agents sign in on the call desk app on their PC.
        </p>
        <button className="btn mt-6" onClick={() => void signOut()}>sign out</button>
      </Frame>
    );
  }
  if (profile.must_change_password) return <ChangePasswordPage />;
  return <>{children}</>;
}
