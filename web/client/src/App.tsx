import { useEffect, useState } from "react";
import {
  BrowserRouter as Router,
  Routes,
  Route,
  useLocation,
} from "react-router-dom";
import Sidebar from "./components/Sidebar";
import EdgeDashboard from "./views/EdgeDashboard";
import DataIngestionView from "./views/DataIngestionView";
import IntelligenceWorkspaceView from "./views/IntelligenceWorkspaceView";
import RiskDeskView from "./views/RiskDeskView";
import ExecutionLogView from "./views/ExecutionLogView";
import Settings from "./views/Settings";
import { ToastProvider } from "./components/ToastProvider";
import CommandPalette from "./components/CommandPalette";
import ErrorBoundary from "./components/ErrorBoundary";

const pageTitles: Record<string, string> = {
  "/": "CourtSideEdge Terminal",
  "/tier-1": "Tier 1: Data Ingestion",
  "/tier-2": "Tier 2: Intelligence Workspace",
  "/tier-3": "Tier 3: Risk Desk",
  "/tier-4": "Tier 4: Execution Log",
  "/settings": "System Settings",
};

function useCurrentTime() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return time;
}

function getPageTitle(pathname: string): string {
  if (pageTitles[pathname]) return pageTitles[pathname];
  const match = Object.keys(pageTitles).find(
    (key) => key !== "/" && pathname.startsWith(key)
  );
  return match ? pageTitles[match] : "CourtSideEdge";
}

function useAgentCount() {
  const [count, setCount] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;

    async function fetchAgents() {
      try {
        const res = await fetch("/api/agents/health");
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;

        if (Array.isArray(data)) {
          const online = data.filter(
            (a: { status?: string }) => a.status === "online"
          ).length;
          setCount(online);
        } else if (typeof data.online === "number") {
          setCount(data.online);
        } else if (typeof data.count === "number") {
          setCount(data.count);
        }
      } catch {
        // silently fail — header will show 0
      }
    }

    fetchAgents();
    return () => {
      cancelled = true;
    };
  }, []);

  return count;
}

function AppShell() {
  const location = useLocation();
  const time = useCurrentTime();
  const agentCount = useAgentCount();
  const title = getPageTitle(location.pathname);
  const formattedTime = time.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  return (
    <div className="flex min-h-screen bg-cs-black font-sans text-white">
      <Sidebar />
      <CommandPalette />

      <div className="ml-0 flex flex-1 flex-col md:ml-[72px]">
        {/* Header */}
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-cs-border/30 bg-cs-black/80 px-4 backdrop-blur-lg md:px-6">
          {/* Left: leave room for mobile hamburger */}
          <div className="flex items-center gap-3 pl-10 md:pl-0">
            <h1 className="text-sm font-semibold tracking-wide text-white">
              {title}
            </h1>
            <span className="hidden h-4 w-px bg-cs-border/40 sm:block" />
            <span className="hidden text-xs text-cs-muted sm:block">
              v6.0.0-agentic
            </span>
          </div>

          {/* Right */}
          <div className="flex items-center gap-3 sm:gap-5">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-pulse-slow rounded-full bg-emerald-400 opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
              </span>
              <span className="text-xs font-medium text-cs-muted">
                <span className="text-emerald-400">{agentCount}</span>{" "}
                <span className="hidden sm:inline">Agents </span>Online
              </span>
            </div>
            <span className="hidden h-4 w-px bg-cs-border/40 sm:block" />
            <span className="hidden font-mono text-xs tabular-nums text-cs-muted sm:block">
              {formattedTime}
            </span>
          </div>
        </header>

        {/* Main content area */}
        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<EdgeDashboard />} />
            <Route path="/tier-1" element={<DataIngestionView />} />
            <Route path="/tier-2" element={<IntelligenceWorkspaceView />} />
            <Route path="/tier-3" element={<RiskDeskView />} />
            <Route path="/tier-4" element={<ExecutionLogView />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <Router>
      <ToastProvider>
        <ErrorBoundary>
          <AppShell />
        </ErrorBoundary>
      </ToastProvider>
    </Router>
  );
}
