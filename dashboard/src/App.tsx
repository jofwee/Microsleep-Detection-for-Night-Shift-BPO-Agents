import { lazy, Suspense } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { LoginPage, RequireTeamLeader } from "./auth/pages";
import { Shell } from "./components/Shell";
import { AgentsPage } from "./features/agents/AgentsPage";
import { AlertsPage } from "./features/alerts/AlertsPage";
import { CallDetailPage } from "./features/calls/CallDetailPage";
import { CallsPage } from "./features/calls/CallsPage";
import { LivePage } from "./features/live/LivePage";
import { SimulationPage } from "./features/simulation/SimulationPage";
import { TeamPage } from "./features/team/TeamPage";

const AgentDetailPage = lazy(() => import("./features/agents/AgentDetailPage").then((m) => ({ default: m.AgentDetailPage })));

const QueuePage = lazy(() => import("./features/queue/QueuePage").then((m) => ({ default: m.QueuePage })));
const ReportsPage = lazy(() => import("./features/reports/ReportsPage").then((m) => ({ default: m.ReportsPage })));
const client = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      retry: 1,
      refetchOnWindowFocus: true,
      placeholderData: (previous: unknown) => previous, // keep the frame while a range reloads
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={client}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              element={
                <RequireTeamLeader>
                  <Shell />
                </RequireTeamLeader>
              }
            >
              <Route index element={<LivePage />} />
              <Route path="queue" element={<Suspense fallback={<p className="label">loading…</p>}><QueuePage /></Suspense>} />
              <Route path="calls" element={<CallsPage />} />
              <Route path="calls/:id" element={<CallDetailPage />} />
              <Route path="simulation" element={<SimulationPage />} />
              <Route path="alerts" element={<AlertsPage />} />
              <Route path="agents" element={<AgentsPage />} />
              <Route path="agents/:id" element={<Suspense fallback={<p className="label">loading…</p>}><AgentDetailPage /></Suspense>} />
              <Route path="reports" element={<Suspense fallback={<p className="label">loading…</p>}><ReportsPage /></Suspense>} />
              <Route path="team" element={<TeamPage />} />
              <Route path="*" element={<p className="label">page not found</p>} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
