import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { CheckCircle2, AlertTriangle, Info, X } from "lucide-react";

type ToastKind = "success" | "error" | "info";
interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  detail?: string;
}

const ToastCtx = createContext<(t: Omit<Toast, "id">) => void>(() => {});
// eslint-disable-next-line react-refresh/only-export-components
export const useToast = () => useContext(ToastCtx);

const ICONS = { success: CheckCircle2, error: AlertTriangle, info: Info };
const COLORS = { success: "#3DD68C", error: "#E5484D", info: "#4C8DFF" };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { ...t, id }]);
    setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== id)), 6000);
  }, []);

  const dismiss = (id: number) => setToasts((prev) => prev.filter((x) => x.id !== id));

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-5 right-5 z-50 flex w-[360px] max-w-[calc(100vw-2.5rem)] flex-col gap-2.5">
        {toasts.map((t) => {
          const Icon = ICONS[t.kind];
          return (
            <div
              key={t.id}
              style={{ animation: "wt-toast-in 0.2s ease-out" }}
              className="flex items-start gap-3 rounded-xl border border-line bg-surface p-3.5 shadow-pop"
            >
              <Icon size={18} style={{ color: COLORS[t.kind] }} className="mt-0.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-text-primary">{t.title}</div>
                {t.detail && <div className="mt-0.5 break-words text-xs text-text-secondary">{t.detail}</div>}
              </div>
              <button
                onClick={() => dismiss(t.id)}
                className="shrink-0 rounded-md p-1 text-text-secondary transition-colors hover:bg-hover hover:text-text-primary"
                aria-label="Dismiss"
              >
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastCtx.Provider>
  );
}
