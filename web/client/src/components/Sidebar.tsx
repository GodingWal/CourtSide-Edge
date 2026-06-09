import { Link, useLocation } from 'react-router-dom';
import { Activity, ShieldAlert, Cpu, Database, PieChart } from 'lucide-react';

export default function Sidebar() {
  const location = useLocation();

  const links = [
    { name: 'Market Divergence', path: '/', icon: Activity },
    { name: 'Alpha Sandbox', path: '/sandbox', icon: Cpu },
    { name: 'Bankroll & CLV', path: '/diagnostics', icon: PieChart },
    { name: 'Intelligence Feed', path: '/intelligence', icon: Database },
  ];

  return (
    <div className="w-64 bg-slate-950 border-r border-slate-800 min-h-screen p-4 flex flex-col shrink-0">
      <div className="mb-8 flex items-center gap-2 px-2">
        <ShieldAlert className="text-emerald-400 w-8 h-8" />
        <h1 className="text-xl font-bold text-white tracking-widest">EDGE<span className="text-emerald-400">UI</span></h1>
      </div>

      <nav className="flex-1 space-y-2">
        {links.map(link => {
          const Icon = link.icon;
          const isActive = location.pathname === link.path;
          return (
            <Link 
              key={link.path}
              to={link.path}
              className={`flex items-center gap-3 px-3 py-3 rounded-xl transition-all ${
                isActive ? 'bg-emerald-500/10 text-emerald-400 shadow-[inset_0_0_15px_rgba(52,211,153,0.1)]' : 'text-slate-400 hover:text-white hover:bg-slate-900'
              }`}
            >
              <Icon className="w-5 h-5" />
              <span className="font-semibold text-sm">{link.name}</span>
            </Link>
          );
        })}
      </nav>
      
      <div className="mt-auto pt-4 border-t border-slate-800">
         <div className="px-3 flex items-center gap-2">
           <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></div>
           <span className="text-xs font-semibold text-slate-500">SYSTEM ONLINE</span>
         </div>
      </div>
    </div>
  );
}
