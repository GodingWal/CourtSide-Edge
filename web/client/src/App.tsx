import { useEffect, useState } from "react";
import { BrowserRouter as Router, Routes, Route, useLocation } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import MarketDivergence from "./views/MarketDivergence";
import AlphaSandbox from "./views/AlphaSandbox";
import PropBuilder from "./views/PropBuilder";
import BankrollDiagnostics from "./views/BankrollDiagnostics";
import IntelligenceFeed from "./views/IntelligenceFeed";

const pageTitles: Record<string, string> = {
  "/": "Market Divergence",
  "/sandbox": "Alpha Sandbox",
  "/prop-builder": "Prop Builder",
  "/diagnostics": "Bankroll Diagnostics",
  "/intelligence": "Intelligence Feed",
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

  return (
    <div className="flex min-h-screen bg-cs-black font-sans text-white">
      <Sidebar />

      <div className="ml-[72px] flex flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-cs-border/30 bg-cs-black/80 px-6 backdrop-blur-lg">
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-semibold tracking-wide text-white">
              {title}
            </h1>
            <span className="h-4 w-px bg-cs-border/40" />
            <span className="text-xs text-cs-muted">v2.4.1</span>
          </div>

          <div className="flex items-center gap-5">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-pulse-slow rounded-full bg-emerald-400 opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
              </span>
              <span className="text-xs font-medium text-cs-muted">
                <span className="text-emerald-400">13</span> Agents Online
              </span>
            </div>

            <span className="h-4 w-px bg-cs-border/40" />

            <span className="font-mono text-xs tabular-nums text-cs-muted">
              {formattedTime}
            </span>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<MarketDivergence />} />
            <Route path="/sandbox" element={<AlphaSandbox />} />
            <Route path="/prop-builder" element={<PropBuilder />} />
            <Route path="/diagnostics" element={<BankrollDiagnostics />} />
            <Route path="/intelligence" element={<IntelligenceFeed />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <Router>
      <AppShell />
    </Router>
  );
}
