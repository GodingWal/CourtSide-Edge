import { useState, useEffect } from 'react';
import { Settings as SettingsIcon, Save, Bell, Monitor, Cpu, Volume2, ShieldAlert, Sparkles, Scale } from 'lucide-react';
import { useToast } from '../components/ToastProvider';
import { SkeletonCard } from '../components/Skeleton';
import { API_BASE } from '../lib/config';

interface AgentHealth {
  id: string;
  name: string;
  status: 'online' | 'offline';
  port: number | null;
}

export default function Settings() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [savingBankroll, setSavingBankroll] = useState(false);
  const [savingDisplay, setSavingDisplay] = useState(false);

  // Form states
  const [bankrollStarting, setBankrollStarting] = useState('10000');
  const [kellyFraction, setKellyFraction] = useState('0.25');
  const [autoHaltDrawdown, setAutoHaltDrawdown] = useState('15');

  const [notificationsEnabled, setNotificationsEnabled] = useState(true);
  const [alertSound, setAlertSound] = useState(true);
  const [themeDensity, setThemeDensity] = useState('Comfortable');

  // Agents status
  const [agents, setAgents] = useState<AgentHealth[]>([]);
  const [currentBankroll, setCurrentBankroll] = useState('');
  const [rotations, setRotations] = useState<any[]>([]);

  // Drift status
  const [driftStatus, setDriftStatus] = useState<any>({
    calibration: null,
    mae: null,
    bias: null,
    settled_bets_analyzed: 0
  });


  const fetchData = async () => {
    try {
      // Fetch settings
      const settingsRes = await fetch(`${API_BASE}/settings`);
      if (settingsRes.ok) {
        const settingsData = await settingsRes.json();
        // settingsData is array of { key, value } or key-value map.
        // Let's handle both structures.
        const settingsMap: Record<string, string> = {};
        if (Array.isArray(settingsData)) {
          settingsData.forEach((s: { key: string; value: string }) => {
            settingsMap[s.key] = s.value;
          });
        } else {
          Object.assign(settingsMap, settingsData);
        }

        if (settingsMap.bankroll_starting) setBankrollStarting(settingsMap.bankroll_starting);
        if (settingsMap.kelly_fraction) setKellyFraction(settingsMap.kelly_fraction);
        if (settingsMap.auto_halt_drawdown) setAutoHaltDrawdown(settingsMap.auto_halt_drawdown);
        if (settingsMap.notifications_enabled) {
          setNotificationsEnabled(settingsMap.notifications_enabled === 'true');
        }
        if (settingsMap.alert_sound) {
          setAlertSound(settingsMap.alert_sound === 'true');
        }
        if (settingsMap.theme_density) {
          setThemeDensity(settingsMap.theme_density);
        }
      }

      // Fetch agent health — show the truth; no fabricated fallback.
      const healthRes = await fetch(`${API_BASE}/agents/health`);
      if (healthRes.ok) {
        const healthData = await healthRes.json();
        setAgents(healthData);
      } else {
        setAgents([]);
      }
    } catch (err) {
      console.error('Failed to load settings data:', err);
      setAgents([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  useEffect(() => {
    const fetchDrift = async () => {
      try {
        const res = await fetch(`${API_BASE}/drift/status`);
        if (res.ok) {
          const data = await res.json();
          setDriftStatus(data);
        }
      } catch (err) {
        console.error("Failed to fetch drift metrics:", err);
      }
    };
    fetchDrift();
    const interval = setInterval(fetchDrift, 8000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const fetchRotations = async () => {
      try {
        const res = await fetch(`${API_BASE}/live/rotations`);
        if (res.ok) {
          const data = await res.json();
          setRotations(data);
        }
      } catch (err) {
        console.error("Failed to fetch rotations:", err);
      }
    };
    fetchRotations();
    const interval = setInterval(fetchRotations, 6000);
    return () => clearInterval(interval);
  }, []);

  const saveSetting = async (key: string, value: string) => {
    const res = await fetch(`${API_BASE}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value })
    });
    if (!res.ok) throw new Error(`Failed to save key: ${key}`);
  };

  const handleSaveBankroll = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingBankroll(true);
    try {
      await saveSetting('bankroll_starting', bankrollStarting);
      await saveSetting('kelly_fraction', kellyFraction);
      await saveSetting('auto_halt_drawdown', autoHaltDrawdown);

      // If a current bankroll was entered, record it as the live balance.
      if (currentBankroll !== '') {
        const res = await fetch(`${API_BASE}/bankroll`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ balance: parseFloat(currentBankroll) })
        });
        if (!res.ok) throw new Error('Failed to set current bankroll');
        setCurrentBankroll('');
      }
      
      toast({
        title: 'Settings Saved',
        description: 'Bankroll variables updated successfully.',
        variant: 'success'
      });
    } catch (err) {
      toast({
        title: 'Error Saving Settings',
        description: 'Could not write bankroll configuration to database.',
        variant: 'danger'
      });
    } finally {
      setSavingBankroll(false);
    }
  };

  const handleSaveDisplay = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingDisplay(true);
    try {
      await saveSetting('notifications_enabled', notificationsEnabled.toString());
      await saveSetting('alert_sound', alertSound.toString());
      await saveSetting('theme_density', themeDensity);

      toast({
        title: 'Display Saved',
        description: 'Display and notifications settings updated.',
        variant: 'success'
      });
    } catch (err) {
      toast({
        title: 'Error Saving Settings',
        description: 'Could not write settings to database.',
        variant: 'danger'
      });
    } finally {
      setSavingDisplay(false);
    }
  };

  if (loading) {
    return (
      <div className="p-4 md:p-8 space-y-6 max-w-[1440px] mx-auto w-full min-h-screen animate-fade-in">
        <div className="flex items-center gap-3">
          <SettingsIcon className="w-7 h-7 text-cs-red" />
          <h1 className="text-3xl font-extrabold text-white">System Settings</h1>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <SkeletonCard className="h-[350px]" />
          <SkeletonCard className="h-[350px]" />
        </div>
        <SkeletonCard className="h-[200px]" />
      </div>
    );
  }

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-[1440px] mx-auto w-full min-h-screen animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-extrabold text-white tracking-tight flex items-center gap-3">
          <SettingsIcon className="w-7 h-7 text-cs-red drop-shadow-[0_0_8px_rgba(239,68,68,0.6)]" />
          System Configuration
        </h1>
        <span className="text-xs text-cs-muted font-mono tracking-widest uppercase">
          Node Control &bull; v5.2.0
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Left Column: Bankroll Settings */}
        <div className="cs-card p-6 text-left">
          <h2 className="text-sm font-semibold tracking-wider uppercase text-white mb-5 flex items-center gap-2">
            <Monitor className="w-4 h-4 text-cs-red" /> Bankroll & Kelly Parameters
          </h2>

          <form onSubmit={handleSaveBankroll} className="space-y-4">
            {/* Starting Bankroll */}
            <div>
              <label className="cs-label">Starting Capital ($)</label>
              <input
                type="number"
                value={bankrollStarting}
                onChange={(e) => setBankrollStarting(e.target.value)}
                className="cs-input font-mono"
                required
              />
              <p className="text-[10px] text-cs-muted mt-1 leading-normal">
                Base allocation pool for bet sizing calculation and historical P&L drawdown.
              </p>
            </div>

            {/* Current Bankroll (writes a live balance point) */}
            <div>
              <label className="cs-label">Current Bankroll ($)</label>
              <input
                type="number"
                min="0"
                step="0.01"
                value={currentBankroll}
                onChange={(e) => setCurrentBankroll(e.target.value)}
                placeholder="Set the live balance shown on the dashboard"
                className="cs-input font-mono"
              />
              <p className="text-[10px] text-cs-muted mt-1 leading-normal">
                Records a new balance point — the dashboard's Total Bankroll updates immediately. Leave blank to keep unchanged.
              </p>
            </div>

            {/* Kelly Fraction */}
            <div>
              <label className="cs-label">Kelly Criterion Fraction</label>
              <input
                type="number"
                step="0.05"
                min="0"
                max="1"
                value={kellyFraction}
                onChange={(e) => setKellyFraction(e.target.value)}
                className="cs-input font-mono"
                required
              />
              <p className="text-[10px] text-cs-muted mt-1 leading-normal">
                Fraction of theoretical Kelly percentage to wager (e.g. 0.25 = Quarter Kelly). Controls volatility.
              </p>
            </div>

            {/* Auto Halt Drawdown */}
            <div>
              <label className="cs-label">Drawdown Circuit Breaker (%)</label>
              <input
                type="number"
                min="1"
                max="99"
                value={autoHaltDrawdown}
                onChange={(e) => setAutoHaltDrawdown(e.target.value)}
                className="cs-input font-mono"
                required
              />
              <p className="text-[10px] text-cs-muted mt-1 leading-normal flex items-start gap-1">
                <ShieldAlert className="w-3 h-3 text-cs-red-bright flex-shrink-0 mt-0.5" />
                <span>Automatically halts auto-bet placing system if net drawdown exceeds this threshold.</span>
              </p>
            </div>

            <button
              type="submit"
              disabled={savingBankroll}
              className="cs-btn-primary w-full flex items-center justify-center gap-2 mt-6"
            >
              <Save className="w-4 h-4" />
              {savingBankroll ? 'Saving Capital Configurations...' : 'Save Bankroll Parameters'}
            </button>
          </form>
        </div>

        {/* Right Column: Notifications & Display */}
        <div className="cs-card p-6 text-left">
          <h2 className="text-sm font-semibold tracking-wider uppercase text-white mb-5 flex items-center gap-2">
            <Bell className="w-4 h-4 text-cs-red" /> Notifications & Interface
          </h2>

          <form onSubmit={handleSaveDisplay} className="space-y-6">
            {/* Enable Notifications */}
            <div className="flex items-center justify-between p-3 rounded-xl bg-cs-black border border-cs-border/30">
              <div>
                <p className="text-xs font-semibold text-white">Browser Push Notifications</p>
                <p className="text-[10px] text-cs-muted leading-tight mt-0.5">Alerts when Agent 11 finds high-EV edges.</p>
              </div>
              <button
                type="button"
                onClick={() => setNotificationsEnabled(!notificationsEnabled)}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out outline-none ${notificationsEnabled ? 'bg-cs-red' : 'bg-cs-dark'}`}
              >
                <span className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${notificationsEnabled ? 'translate-x-5' : 'translate-x-0'}`} />
              </button>
            </div>

            {/* Alert Sounds */}
            <div className="flex items-center justify-between p-3 rounded-xl bg-cs-black border border-cs-border/30">
              <div>
                <p className="text-xs font-semibold text-white flex items-center gap-1.5">
                  <Volume2 className="w-3.5 h-3.5 text-cs-muted" /> Audio Alert Ping
                </p>
                <p className="text-[10px] text-cs-muted leading-tight mt-0.5">Plays a digital sonar sound when edges are published.</p>
              </div>
              <button
                type="button"
                onClick={() => setAlertSound(!alertSound)}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out outline-none ${alertSound ? 'bg-cs-red' : 'bg-cs-dark'}`}
              >
                <span className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${alertSound ? 'translate-x-5' : 'translate-x-0'}`} />
              </button>
            </div>

            {/* Theme Density */}
            <div>
              <label className="cs-label">Terminal Layout Density</label>
              <select
                value={themeDensity}
                onChange={(e) => setThemeDensity(e.target.value)}
                className="cs-input bg-cs-black py-2.5"
              >
                <option>Compact (High Information)</option>
                <option>Comfortable (Standard)</option>
                <option>Spacious (Relaxed)</option>
              </select>
              <p className="text-[10px] text-cs-muted mt-1 leading-normal">
                Adjusts padding and grid widths across all live telemetry terminals.
              </p>
            </div>

            <button
              type="submit"
              disabled={savingDisplay}
              className="cs-btn-primary w-full flex items-center justify-center gap-2 mt-4"
            >
              <Save className="w-4 h-4" />
              {savingDisplay ? 'Saving Display...' : 'Save Interface Settings'}
            </button>
          </form>
        </div>
      </div>

      {/* Agent 15: Auto-Calibration & Drift Console */}
      <div className="cs-card p-6 text-left animate-slide-up" style={{ animationDelay: '260ms' }}>
        <h2 className="text-sm font-semibold tracking-wider uppercase text-white mb-5 flex items-center gap-2">
          <Scale className="w-4 h-4 text-cs-red" /> Agent 15: Drift Calibration Swarm
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-5">
          <div className="p-4 bg-cs-black/60 border border-cs-border/40 rounded-xl">
            <p className="cs-stat-label">Model Bias (Mean Error)</p>
            <span className={`text-xl font-bold font-mono block mt-1.5 ${(driftStatus.bias ?? 0) < 0 ? 'text-cs-red-bright' : 'text-emerald-400'}`}>
              {driftStatus.bias === null || driftStatus.bias === undefined ? '—' : `${driftStatus.bias > 0 ? `+${driftStatus.bias}` : driftStatus.bias}${driftStatus.bias < 0 ? ' (Underprojecting)' : ' (Overprojecting)'}`}
            </span>
          </div>

          <div className="p-4 bg-cs-black/60 border border-cs-border/40 rounded-xl">
            <p className="cs-stat-label">Mean Absolute Error (MAE)</p>
            <span className="text-xl font-bold font-mono text-white block mt-1.5">
              {driftStatus.mae ?? '—'} pts/reb/ast
            </span>
          </div>

          <div className="p-4 bg-cs-black/60 border border-cs-border/40 rounded-xl">
            <p className="cs-stat-label">Settled Bets Analyzed</p>
            <span className="text-xl font-bold font-mono text-gradient-red block mt-1.5">
              {driftStatus.settled_bets_analyzed} nodes
            </span>
          </div>
        </div>

        <div className="bg-cs-black/40 border border-cs-border/40 rounded-xl p-4">
          <div className="text-xs font-semibold text-white mb-3 flex items-center gap-2">
            <Sparkles className="w-3.5 h-3.5 text-cs-red" /> Active Projection Offset Multipliers
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {!driftStatus?.calibration || Object.keys(driftStatus.calibration).length === 0 ? (
              <p className="text-xs text-cs-muted font-mono col-span-full text-center py-3">
                No calibration data yet — Agent 15 publishes after enough settled bets.
              </p>
            ) : (
            Object.entries(driftStatus.calibration).map(([stat, offset]: any) => (
              <div key={stat} className="bg-cs-dark/30 border border-cs-border/30 rounded-xl p-3 flex items-center justify-between">
                <span className="font-bold text-xs text-white">{stat} adjustment</span>
                <span className={`font-mono text-xs font-black ${offset >= 0 ? 'text-emerald-400' : 'text-cs-red-bright'}`}>
                  {offset >= 0 ? `+${offset}` : offset}
                </span>
              </div>
            ))
            )}
          </div>
        </div>
      </div>

      {/* Agent 21: Live Foul & Rotation Adjustments Dashboard */}
      <div className="cs-card p-6 text-left animate-slide-up" style={{ animationDelay: '280ms' }}>
        <h2 className="text-sm font-semibold tracking-wider uppercase text-white mb-5 flex items-center gap-2">
          <Scale className="w-4 h-4 text-cs-red" /> Agent 21: Live Foul &amp; Rotation Adjustments
        </h2>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
          {rotations.length === 0 ? (
            <p className="text-xs text-cs-muted font-mono py-4 col-span-3 text-center">Loading live rotation adjustments...</p>
          ) : (
            rotations.map((rot, idx) => (
              <div key={idx} className="bg-cs-black/60 border border-cs-border/40 rounded-xl p-4 flex flex-col justify-between hover:border-cs-border/70 transition-colors">
                <div className="flex justify-between items-start">
                  <div>
                    <h3 className="font-bold text-white text-sm">{rot.player}</h3>
                    <p className="text-[10px] text-cs-muted font-mono">{rot.period} &bull; {rot.fouls} Fouls</p>
                  </div>
                  <span className={`text-[9px] font-bold px-2 py-0.5 rounded font-mono ${rot.status === 'SEVERE_FOUL_TROUBLE' ? 'bg-cs-red/20 text-cs-red-bright border border-cs-red/30' : rot.status === 'FOUL_TROUBLE' ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30' : 'bg-cs-dark text-cs-muted border border-cs-border/20'}`}>
                    {rot.status.replace(/_/g, ' ')}
                  </span>
                </div>
                <div className="flex justify-between items-center mt-4 border-t border-cs-border/20 pt-3 font-mono">
                  <span className="text-[10px] text-cs-muted">PROJECTION ADJ.</span>
                  <span className={`text-xs font-black ${rot.adjustment.startsWith('-') ? 'text-cs-red-bright' : 'text-emerald-400'}`}>
                    {rot.adjustment}
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Bottom Section: Agent Grid */}
      <div className="cs-card p-6 text-left">
        <h2 className="text-sm font-semibold tracking-wider uppercase text-white mb-5 flex items-center gap-2">
          <Cpu className="w-4 h-4 text-cs-red" /> CourtSideEdge Agent Grid ({agents.length} Nodes)
        </h2>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {agents.map((agent) => (
            <div
              key={agent.id}
              className="p-4 rounded-xl border border-cs-border/30 bg-cs-black/50 hover:border-cs-border/70 transition-all flex items-center justify-between"
            >
              <div>
                <p className="text-xs font-semibold text-white">{agent.name}</p>
                <p className="text-[9px] font-mono text-cs-muted mt-0.5">
                  ID: {agent.id} {agent.port ? `| Port: ${agent.port}` : ''}
                </p>
              </div>
              
              <div className="flex items-center gap-1.5">
                <span className={`h-2 w-2 rounded-full ${agent.status === 'online' ? 'bg-emerald-400 animate-pulse' : 'bg-cs-red'}`} />
                <span className={`text-[10px] font-bold uppercase tracking-wider ${agent.status === 'online' ? 'text-emerald-400' : 'text-cs-red-bright'}`}>
                  {agent.status}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
