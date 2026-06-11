import { useEffect, useState } from "react";
import {
  BrowserRouter as Router,
  Routes,
  Route,
  useLocation,
} from "react-router-dom";
import Sidebar from "./components/Sidebar";
import MarketDivergence from "./views/MarketDivergence";
import AlphaSandbox from "./views/AlphaSandbox";
import BankrollDiagnostics from "./views/BankrollDiagnostics";
import IntelligenceFeed from "./views/IntelligenceFeed";
import BetTracker from "./views/BetTracker";
import StatsCenter from "./views/StatsCenter";
import Settings from "./views/Settings";
import { ToastProvider } from "./components/ToastProvider";
import CommandPalette from "./components/CommandPalette";
import ErrorBoundary from "./components/ErrorBoundary";

const pageTitles: Record<string, string> = {
  "/": "Market Divergence",
  "/sandbox": "Alpha Sandbox",
  "/diagnostics": "Bankroll Diagnostics",
  "/intelligence": "Intelligence Feed",
  "/bets": "Bet Terminal",
  "/stats": "Stats Center",
  "/settings": "Settings",
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
      } catch (err) {
        // graceful fallback — header will show 0
        console.error("Failed to fetch agent health:", err);
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
              v5.0.0
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
            <Route path="/" element={<MarketDivergence />} />
            <Route path="/sandbox" element={<AlphaSandbox />} />
            <Route path="/diagnostics" element={<BankrollDiagnostics />} />
            <Route path="/intelligence" element={<IntelligenceFeed />} />
            <Route path="/bets" element={<BetTracker />} />
            <Route path="/stats" element={<StatsCenter />} />
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
