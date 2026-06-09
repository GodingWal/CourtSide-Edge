import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type ToastVariant = 'default' | 'success' | 'danger' | 'warning';

interface ToastPayload {
  title: string;
  description?: string;
  variant?: ToastVariant;
}

interface Toast extends ToastPayload {
  id: string;
  variant: ToastVariant;
  createdAt: number;
}

interface ToastContextValue {
  toast: (payload: ToastPayload) => void;
}

/* ------------------------------------------------------------------ */
/*  Context                                                            */
/* ------------------------------------------------------------------ */

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error('useToast must be used within a <ToastProvider>');
  }
  return ctx;
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const MAX_TOASTS = 5;
const AUTO_DISMISS_MS = 5000;

const BORDER_COLORS: Record<ToastVariant, string> = {
  default: 'border-l-cs-border',
  success: 'border-l-emerald-500',
  danger: 'border-l-cs-red-bright',
  warning: 'border-l-amber-500',
};

const PROGRESS_COLORS: Record<ToastVariant, string> = {
  default: 'bg-cs-border',
  success: 'bg-emerald-500',
  danger: 'bg-cs-red-bright',
  warning: 'bg-amber-500',
};

/* ------------------------------------------------------------------ */
/*  Single Toast                                                       */
/* ------------------------------------------------------------------ */

function ToastItem({
  toast: t,
  onDismiss,
}: {
  toast: Toast;
  onDismiss: (id: string) => void;
}) {
  const [progress, setProgress] = useState(100);
  const startRef = useRef(t.createdAt);

  useEffect(() => {
    let raf: number;

    const tick = () => {
      const elapsed = Date.now() - startRef.current;
      const remaining = Math.max(0, 100 - (elapsed / AUTO_DISMISS_MS) * 100);
      setProgress(remaining);

      if (remaining <= 0) {
        onDismiss(t.id);
        return;
      }
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [t.id, onDismiss]);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 20, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, x: 80, scale: 0.95 }}
      transition={{ type: 'spring', stiffness: 400, damping: 30 }}
      className={`cs-card relative overflow-hidden border-l-4 ${BORDER_COLORS[t.variant]} w-80 p-4 shadow-card`}
    >
      {/* Close button */}
      <button
        onClick={() => onDismiss(t.id)}
        className="absolute right-2 top-2 rounded p-1 text-cs-muted transition-colors hover:bg-cs-dark hover:text-white"
        aria-label="Dismiss toast"
      >
        <X className="h-3.5 w-3.5" />
      </button>

      {/* Content */}
      <div className="pr-5">
        <p className="font-inter text-sm font-semibold text-white">
          {t.title}
        </p>
        {t.description && (
          <p className="mt-1 text-xs leading-relaxed text-cs-muted">
            {t.description}
          </p>
        )}
      </div>

      {/* Progress bar */}
      <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-cs-dark/60">
        <div
          className={`h-full ${PROGRESS_COLORS[t.variant]} transition-none`}
          style={{ width: `${progress}%` }}
        />
      </div>
    </motion.div>
  );
}

/* ------------------------------------------------------------------ */
/*  Provider                                                           */
/* ------------------------------------------------------------------ */

let toastCounter = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback((payload: ToastPayload) => {
    const id = `toast-${++toastCounter}-${Date.now()}`;
    const newToast: Toast = {
      ...payload,
      id,
      variant: payload.variant ?? 'default',
      createdAt: Date.now(),
    };

    setToasts((prev) => {
      const next = [...prev, newToast];
      // Keep only the latest MAX_TOASTS
      return next.slice(-MAX_TOASTS);
    });
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}

      {/* Toast container — fixed bottom-right */}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        <AnimatePresence mode="popLayout">
          {toasts.map((t) => (
            <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export default ToastProvider;
