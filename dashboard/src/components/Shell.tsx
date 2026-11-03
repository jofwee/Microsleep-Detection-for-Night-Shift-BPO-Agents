import { NavLink, Outlet, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./ErrorBoundary";
import { useAuth } from "../auth/AuthProvider";
import { useAlerts, useLiveUpdates } from "../lib/queries";
import { useTheme, type ThemeChoice } from "../lib/theme";

const NAV = [
  { to: "/", label: "live", end: true },
  { to: "/queue", label: "queue" },
  { to: "/calls", label: "calls" },
  { to: "/simulation", label: "simulation" },
  { to: "/alerts", label: "alerts" },
  { to: "/agents", label: "agents" },
  { to: "/reports", label: "reports" },
  { to: "/team", label: "team" },
];

function ThemeSwitch() {
  const { choice, set } = useTheme();
  const opts: ThemeChoice[] = ["light", "dark", "system"];
  return (
    <div className="inline-flex overflow-hidden rounded-input border" role="group" aria-label="theme">
      {opts.map((o) => (
        <button
          key={o}
          onClick={() => set(o)}
          aria-pressed={choice === o}
          className={`px-2 py-1 font-mono text-[9px] uppercase tracking-[1px] transition-colors ${choice === o ? "bg-ink text-bg" : "text-g500 hover:bg-g100"}`}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

export function Shell() {
  const { profile, signOut } = useAuth();
  const location = useLocation();
  useLiveUpdates(true);
  const alerts = useAlerts(100);
  const unacknowledged = (alerts.data ?? []).filter((a) => !a.acknowledged_at).length;

  return (
    <div className="mx-auto max-w-[72rem] px-4 pb-24 sm:px-6">
      <nav aria-label="main" className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b py-4">
        <ul className="flex flex-wrap gap-x-5 gap-y-2">
          {NAV.map((n, i) => (
            <li key={n.to}>
              <NavLink
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  `font-mono text-[11px] uppercase tracking-[1px] transition-colors ${isActive ? "text-ink underline underline-offset-[6px]" : "text-g400 hover:text-ink"}`
                }
              >
                <span className="text-g300">{String(i + 1).padStart(2, "0")}</span> {n.label}
                {n.label === "alerts" && unacknowledged > 0 && (
                  <span className="ml-1.5 rounded-full bg-ink px-1.5 py-0.5 text-[9px] text-bg" aria-label={`${unacknowledged} unacknowledged`}>
                    {unacknowledged}
                  </span>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="flex items-center gap-3">
          <ThemeSwitch />
          <span className="label hidden sm:inline">{profile?.full_name}</span>
          <button className="btn !py-1 !text-[11px]" onClick={() => void signOut()}>sign out</button>
        </div>
      </nav>
      <main className="pt-12">
        <ErrorBoundary key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
