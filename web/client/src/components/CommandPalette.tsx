import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Search,
  Activity,
  Cpu,
  PieChart,
  Database,
  Download,
  Bell,
} from 'lucide-react';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Command {
  id: string;
  label: string;
  icon: React.ElementType;
  shortcut?: string;
  action: () => void;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  /* ---- Commands -------------------------------------------------- */

  const commands: Command[] = useMemo(
    () => [
      {
        id: 'market',
        label: 'Market',
        icon: Activity,
        shortcut: '1',
        action: () => navigate('/'),
      },
      {
        id: 'alpha',
        label: 'Alpha',
        icon: Cpu,
        shortcut: '2',
        action: () => navigate('/sandbox'),
      },
      {
        id: 'bankroll',
        label: 'Bankroll',
        icon: PieChart,
        shortcut: '3',
        action: () => navigate('/diagnostics'),
      },
      {
        id: 'intel',
        label: 'Intel',
        icon: Database,
        shortcut: '4',
        action: () => navigate('/intelligence'),
      },
      {
        id: 'stats',
        label: 'Stats Center',
        icon: Activity,
        shortcut: '5',
        action: () => navigate('/stats'),
      },
      {
        id: 'export',
        label: 'Export Bets CSV',
        icon: Download,
        action: () => {
          /* TODO: wire up export */
          console.log('[CommandPalette] Export Bets CSV');
        },
      },
      {
        id: 'notifications',
        label: 'Toggle Notifications',
        icon: Bell,
        action: () => {
          /* TODO: wire up notification toggle */
          console.log('[CommandPalette] Toggle Notifications');
        },
      },
    ],
    [navigate]
  );

  /* ---- Filtered list --------------------------------------------- */

  const filtered = useMemo(() => {
    if (!query.trim()) return commands;
    const q = query.toLowerCase();
    return commands.filter((c) => c.label.toLowerCase().includes(q));
  }, [query, commands]);

  // Reset selection when filter changes
  useEffect(() => {
    setSelectedIndex(0);
  }, [filtered.length]);

  /* ---- Execute command ------------------------------------------- */

  const execute = useCallback(
    (cmd: Command) => {
      cmd.action();
      setOpen(false);
      setQuery('');
    },
    []
  );

  /* ---- Global keyboard listeners --------------------------------- */

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl/Cmd + K to toggle palette
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setOpen((prev) => {
          if (!prev) setQuery('');
          return !prev;
        });
        return;
      }

      // Number shortcuts 1-5 (only when palette is NOT open and no input focused)
      if (
        !open &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey &&
        !(
          document.activeElement instanceof HTMLInputElement ||
          document.activeElement instanceof HTMLTextAreaElement ||
          document.activeElement instanceof HTMLSelectElement
        )
      ) {
        const num = parseInt(e.key, 10);
        if (num >= 1 && num <= 4) {
          const match = commands.find((c) => c.shortcut === String(num));
          if (match) {
            e.preventDefault();
            match.action();
          }
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, commands]);

  /* ---- Modal keyboard navigation --------------------------------- */

  const handleModalKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        setOpen(false);
        setQuery('');
        return;
      }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((i) => (i + 1) % filtered.length);
        return;
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((i) => (i - 1 + filtered.length) % filtered.length);
        return;
      }

      if (e.key === 'Enter') {
        e.preventDefault();
        if (filtered[selectedIndex]) {
          execute(filtered[selectedIndex]);
        }
      }
    },
    [filtered, selectedIndex, execute]
  );

  /* ---- Focus input when opened ----------------------------------- */

  useEffect(() => {
    if (open) {
      // Small delay to ensure the element is mounted
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  /* ---- Render ---------------------------------------------------- */

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="palette-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
          onClick={() => {
            setOpen(false);
            setQuery('');
          }}
          onKeyDown={handleModalKeyDown}
        >
          {/* Modal content */}
          <motion.div
            key="palette-content"
            initial={{ opacity: 0, y: -12, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -12, scale: 0.97 }}
            transition={{ type: 'spring', stiffness: 500, damping: 35 }}
            className="cs-card mx-auto mt-[20vh] max-w-lg overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Search input */}
            <div className="flex items-center gap-3 border-b border-cs-border/50 px-4 py-3">
              <Search className="h-4 w-4 shrink-0 text-cs-muted" />
              <input
                ref={inputRef}
                type="text"
                placeholder="Type a command…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="cs-input flex-1 border-none bg-transparent px-0 py-0 text-sm focus:ring-0"
                onKeyDown={handleModalKeyDown}
              />
              <kbd className="cs-badge hidden text-[10px] sm:inline-flex">
                ESC
              </kbd>
            </div>

            {/* Command list */}
            <div className="max-h-72 overflow-y-auto py-2">
              {filtered.length === 0 && (
                <p className="px-4 py-6 text-center text-sm text-cs-muted">
                  No commands found.
                </p>
              )}

              {filtered.map((cmd, idx) => {
                const Icon = cmd.icon;
                const isActive = idx === selectedIndex;

                return (
                  <button
                    key={cmd.id}
                    className={`flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                      isActive
                        ? 'bg-cs-dark text-white'
                        : 'text-cs-muted hover:bg-cs-dark hover:text-white'
                    }`}
                    onMouseEnter={() => setSelectedIndex(idx)}
                    onClick={() => execute(cmd)}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span className="flex-1 text-sm font-medium">
                      {cmd.label}
                    </span>
                    {cmd.shortcut && (
                      <kbd className="cs-badge text-[10px]">
                        {cmd.shortcut}
                      </kbd>
                    )}
                  </button>
                );
              })}
            </div>

            {/* Footer hint */}
            <div className="flex items-center gap-4 border-t border-cs-border/50 px-4 py-2 text-[10px] text-cs-muted">
              <span>
                <kbd className="cs-badge mr-1 text-[10px]">↑↓</kbd> navigate
              </span>
              <span>
                <kbd className="cs-badge mr-1 text-[10px]">↵</kbd> select
              </span>
              <span>
                <kbd className="cs-badge mr-1 text-[10px]">esc</kbd> close
              </span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export default CommandPalette;
