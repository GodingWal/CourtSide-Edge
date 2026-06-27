import { useState, useCallback } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  Cpu,
  PieChart,
  Database,
  Receipt,
  Settings,
  Menu,
  X,
  Zap,
} from "lucide-react";

const navItems = [
  { path: "/", label: "Agentic Terminal", icon: Zap },
  { path: "/tier-1", label: "Data Ingestion", icon: Database },
  { path: "/tier-2", label: "Intelligence", icon: Cpu },
  { path: "/tier-3", label: "Risk Desk", icon: PieChart },
  { path: "/tier-4", label: "Execution Log", icon: Receipt },
];

export default function Sidebar() {
  const location = useLocation();
  const [isOpen, setIsOpen] = useState(false);

  const closeSidebar = useCallback(() => setIsOpen(false), []);

  const renderNavLink = (
    { path, label, icon: Icon }: (typeof navItems)[0],
    onNavigate?: () => void
  ) => {
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
        onClick={onNavigate}
      >
        {isActive && (
          <span className="absolute -left-[14px] top-1/2 h-7 w-[3px] -translate-y-1/2 rounded-r-full bg-cs-neon-blue shadow-glow-blue-sm" />
        )}
        <div
          className={`flex h-11 w-11 items-center justify-center rounded-xl transition-all duration-200 ${
            isActive
              ? "bg-cs-neon-blue-glow text-cs-neon-blue-bright shadow-glow-blue-sm"
              : "text-cs-muted hover:bg-cs-dark hover:text-white"
          }`}
        >
          <Icon
            className="h-[20px] w-[20px]"
            strokeWidth={isActive ? 2.2 : 1.8}
          />
        </div>
      </Link>
    );
  };

  const isSettingsActive = location.pathname.startsWith("/settings");

  const sidebarContent = (onNavigate?: () => void) => (
    <>
      {/* Logo */}
      <Link
        to="/"
        className="group mb-8 flex flex-col items-center gap-1.5"
        onClick={onNavigate}
      >
        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-cs-neon-blue to-cs-neon-purple shadow-glow-blue-sm transition-shadow duration-300 group-hover:shadow-glow-purple">
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

      {/* Primary nav */}
      <nav className="flex flex-1 flex-col items-center gap-1">
        {navItems.map((item) => renderNavLink(item, onNavigate))}
      </nav>

      {/* Bottom section */}
      <div className="flex flex-col items-center gap-3">
        {/* Live indicator */}
        <div className="flex flex-col items-center gap-1">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-pulse-slow rounded-full bg-cs-emerald opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-cs-emerald-bright shadow-glow-emerald-sm" />
          </span>
          <span className="text-[9px] font-bold uppercase tracking-widest text-cs-emerald">
            Live
          </span>
        </div>

        {/* Settings link */}
        <Link
          to="/settings"
          title="Settings"
          className={`flex h-11 w-11 items-center justify-center rounded-xl transition-all duration-200 ${
            isSettingsActive
              ? "bg-cs-neon-purple-glow text-cs-neon-purple shadow-glow-purple-sm"
              : "text-cs-muted hover:bg-cs-dark hover:text-white"
          }`}
          onClick={onNavigate}
        >
          <Settings className="h-[18px] w-[18px]" strokeWidth={1.8} />
        </Link>
      </div>
    </>
  );

  return (
    <>
      {/* Mobile hamburger button */}
      <button
        onClick={() => setIsOpen(true)}
        className="fixed left-3 top-3.5 z-50 flex h-8 w-8 items-center justify-center rounded-lg bg-cs-dark/80 text-cs-muted backdrop-blur-sm transition-colors hover:bg-cs-dark hover:text-white md:hidden"
        aria-label="Open navigation"
      >
        <Menu className="h-5 w-5" strokeWidth={1.8} />
      </button>

      {/* Mobile overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm md:hidden"
          onClick={closeSidebar}
        />
      )}

      {/* Mobile sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex h-screen w-[72px] flex-col items-center border-r border-cs-border/20 bg-cs-black py-5 transition-transform duration-300 ease-in-out md:hidden ${
          isOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {/* Close button */}
        <button
          onClick={closeSidebar}
          className="absolute right-1.5 top-2 flex h-7 w-7 items-center justify-center rounded-lg text-cs-muted transition-colors hover:bg-cs-dark hover:text-white"
          aria-label="Close navigation"
        >
          <X className="h-4 w-4" strokeWidth={2} />
        </button>
        {sidebarContent(closeSidebar)}
      </aside>

      {/* Desktop sidebar — always visible */}
      <aside className="fixed left-0 top-0 z-50 hidden h-screen w-[72px] flex-col items-center border-r border-cs-border/20 bg-cs-black py-5 md:flex">
        {sidebarContent()}
      </aside>
    </>
  );
}
