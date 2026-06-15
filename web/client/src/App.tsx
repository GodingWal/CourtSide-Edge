import { useEffect, useState } from "react";
import {
  BrowserRouter as Router,
  Routes,
  Route,
  useLocation,
} from "react-router-dom";
import Sidebar from "./components/Sidebar";
import SystemCortexBar from "./components/SystemCortexBar";
import MarketDivergence from "./views/MarketDivergence";
import AlphaSandbox from "./views/AlphaSandbox";
import SystemStatus from "./views/SystemStatus";
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
  "/system": "System Status",
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

/* Pages that show the SystemCortexBar */
const cortexBarPages = ["/", "/system", "/diagnostics", "/bets", "/stats"];

function AppShell() {
  const location = useLocation();
  const time = useCurrentTime();
  const title = getPageTitle(location.pathname);
  const formattedTime = time.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  const showCortexBar = cortexBarPages.includes(location.pathname);

  return (
    <div className="flex min-h-screen bg-cs-black font-sans text-white">
      <Sidebar />
      <CommandPalette />

      <div className="ml-0 flex flex-1 flex-col md:ml-[72px]">
        {/* Header */}
        <header className="sticky top-0 z-40 flex h-14 items-center justify-between border-b border-cs-border/30 bg-cs-black/80 px-4 backdrop-blur-lg md:px-6">
          {/* Left */}
          <div className="flex items-center gap-3 pl-10 md:pl-0">
            <h1 className="text-sm font-semibold tracking-wide text-white">
              {title}
            </h1>
            <span className="hidden h-4 w-px bg-cs-border/40 sm:block" />
            <span className="hidden text-[10px] text-cs-muted font-mono sm:block uppercase tracking-wider">
              v5.5
            </span>
          </div>

          {/* Right */}
          <div className="flex items-center gap-4">
            {/* Live indicator */}
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-pulse-slow rounded-full bg-cs-success opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-cs-success" />
              </span>
              <span className="hidden text-[10px] font-bold text-cs-success uppercase tracking-wider sm:block">
                Live
              </span>
            </div>
            <span className="hidden h-4 w-px bg-cs-border/40 sm:block" />
            <span className="hidden font-mono text-[10px] tabular-nums text-cs-muted sm:block">
              {formattedTime}
            </span>
          </div>
        </header>

        {/* System Cortex Bar — sticky below header on key pages */}
        {showCortexBar && <SystemCortexBar />}

        {/* Main content area */}
        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<MarketDivergence />} />
            <Route path="/system" element={<SystemStatus />} />
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
