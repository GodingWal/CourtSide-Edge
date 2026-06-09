import { Link, useLocation } from 'react-router-dom';
import { Activity, ShieldAlert, Cpu, Database, PieChart, Wrench } from 'lucide-react';

export default function Sidebar() {
  const location = useLocation();

  const links = [
    { name: 'Market Divergence', path: '/', icon: Activity },
    { name: 'Alpha Sandbox', path: '/sandbox', icon: Cpu },
    { name: 'Prop Builder', path: '/prop-builder', icon: Wrench },
    { name: 'Bankroll & CLV', path: '/diagnostics', icon: PieChart },
    { name: 'Intelligence Feed', path: '/intelligence', icon: Database },
  ];

  return (
    <div className="w-64 bg-zinc-950 border-r border-zinc-800 min-h-screen p-4 flex flex-col shrink-0">
      <div className="mb-8 flex flex-col items-start justify-center px-2 py-4 border-b border-zinc-800/50">
        <div className="flex items-center gap-2 mb-1">
          <ShieldAlert className="text-red-500 w-8 h-8" />
          <h1 className="text-xl font-black text-white tracking-tight uppercase">CourtSide<span className="text-red-500">Edge</span></h1>
        </div>
        <p className="text-xs font-semibold text-zinc-500 uppercase tracking-widest ml-10">Terminal V4</p>
      </div>

      <nav className="flex-1 space-y-2">
        {links.map(link => {
          const Icon = link.icon;
          const isActive = location.pathname === link.path;
          return (
            <Link 
              key={link.path}
              to={link.path}
              className={`flex items-center gap-3 px-3 py-3 rounded-xl transition-all font-medium ${
                isActive ? 'bg-red-500/10 text-red-500 shadow-[inset_0_0_15px_rgba(239,68,68,0.1)] border border-red-500/20' : 'text-zinc-400 hover:text-white hover:bg-zinc-900'
              }`}
            >
              <Icon className="w-5 h-5" />
              <span className="font-semibold text-sm">{link.name}</span>
            </Link>
          );
        })}
      </nav>
      
      <div className="mt-auto pt-4 border-t border-zinc-800">
         <div className="px-3 flex items-center gap-2">
           <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse shadow-[0_0_8px_rgba(239,68,68,0.8)]"></div>
           <span className="text-xs font-bold text-zinc-400 uppercase tracking-wider">System Online</span>
         </div>
      </div>
    </div>
  );
}
