import { Link, useLocation } from "react-router-dom";
import {
  Activity,
  Cpu,
  Wrench,
  PieChart,
  Database,
  Settings,
} from "lucide-react";

const navItems = [
  { path: "/", label: "Market Divergence", icon: Activity },
  { path: "/sandbox", label: "Alpha Sandbox", icon: Cpu },
  { path: "/prop-builder", label: "Prop Builder", icon: Wrench },
  { path: "/diagnostics", label: "Bankroll", icon: PieChart },
  { path: "/intelligence", label: "Intelligence", icon: Database },
];

export default function Sidebar() {
  const location = useLocation();

  return (
    <aside className="fixed left-0 top-0 z-50 flex h-screen w-[72px] flex-col items-center border-r border-cs-border/20 bg-cs-black py-5">
      {/* Logo */}
      <Link to="/" className="group mb-8 flex flex-col items-center gap-1.5">
        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-cs-red to-cs-red-bright shadow-glow-red-sm transition-shadow duration-300 group-hover:shadow-glow-red">
          <span className="text-base font-black tracking-tight text-white">
            CE
          </span>
        </div>
        <span className="text-[8px] font-semibold uppercase tracking-[0.25em] text-cs-muted">
          Court
          <br />
          Side
        </span>
      </Link>

      {/* Nav Items */}
      <nav className="flex flex-1 flex-col items-center gap-1">
        {navItems.map(({ path, label, icon: Icon }) => {
          const isActive =
            path === "/"
              ? location.pathname === "/"
              : location.pathname.startsWith(path);

          return (
            <Link
              key={path}
              to={path}
              title={label}
              className="group relative"
            >
              {/* Active left accent bar */}
              {isActive && (
                <span className="absolute -left-[14px] top-1/2 h-7 w-[3px] -translate-y-1/2 rounded-r-full bg-cs-red shadow-glow-red-sm" />
              )}

              <div
                className={`flex h-11 w-11 items-center justify-center rounded-xl transition-all duration-200 ${
                  isActive
                    ? "bg-cs-red/15 text-cs-red shadow-glow-red-sm"
                    : "text-cs-muted hover:bg-cs-dark hover:text-white"
                }`}
              >
                <Icon className="h-[20px] w-[20px]" strokeWidth={isActive ? 2.2 : 1.8} />
              </div>
            </Link>
          );
        })}
      </nav>

      {/* Bottom section */}
      <div className="flex flex-col items-center gap-3">
        {/* Live indicator */}
        <div className="flex flex-col items-center gap-1">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-pulse-slow rounded-full bg-cs-red opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-cs-red-bright shadow-glow-red-sm" />
          </span>
          <span className="text-[9px] font-bold uppercase tracking-widest text-cs-red">
            Live
          </span>
        </div>

        {/* Settings */}
        <button
          title="Settings"
          className="flex h-11 w-11 items-center justify-center rounded-xl text-cs-muted transition-all duration-200 hover:bg-cs-dark hover:text-white"
        >
          <Settings className="h-[18px] w-[18px]" strokeWidth={1.8} />
        </button>
      </div>
    </aside>
  );
}
