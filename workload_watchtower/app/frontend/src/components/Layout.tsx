import { useState, type ReactNode } from "react";
import {
  LayoutDashboard,
  Kanban,
  ListFilter,
  SlidersHorizontal,
  Mail,
  Sparkles,
  BarChart3,
  Moon,
  Sun,
  RefreshCw,
  Play,
  type LucideIcon,
} from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks";
import { useToast } from "./Toast";
import { Pill } from "./ui";

export type ViewKey = "dashboard" | "board" | "findings" | "rules" | "actions" | "monitoring" | "ask";

const NAV: { key: ViewKey; label: string; icon: LucideIcon }[] = [
  { key: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { key: "board", label: "Triage Board", icon: Kanban },
  { key: "findings", label: "Findings", icon: ListFilter },
  { key: "rules", label: "Rules", icon: SlidersHorizontal },
  { key: "actions", label: "Actions", icon: Mail },
  { key: "monitoring", label: "Monitoring", icon: BarChart3 },
  { key: "ask", label: "Ask Watchtower", icon: Sparkles },
];

const TITLES: Record<ViewKey, string> = {
  dashboard: "Dashboard",
  board: "Triage Board",
  findings: "Findings",
  rules: "Rules",
  actions: "Actions",
  monitoring: "Monitoring",
  ask: "Ask Watchtower",
};

// Watchtower wordmark — our own radar/watchtower glyph (NOT the Databricks logo).
function Wordmark() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <span className="relative flex h-8 w-8 items-center justify-center rounded-[9px] bg-app ring-1 ring-line">
        <svg viewBox="0 0 32 32" width="20" height="20" aria-hidden>
          <circle cx="16" cy="16" r="12" fill="none" stroke="#FF3621" strokeWidth="1.4" strokeOpacity="0.35" />
          <circle cx="16" cy="16" r="7" fill="none" stroke="#FF3621" strokeWidth="1.4" strokeOpacity="0.55" />
          <path d="M16 16 L16 5" stroke="#FF3621" strokeWidth="2" strokeLinecap="round" />
          <circle cx="16" cy="16" r="2.3" fill="#FF5F46" />
        </svg>
      </span>
      <div className="leading-tight">
        <div className="text-[15px] font-semibold tracking-tight text-text-primary">Watchtower</div>
        <div className="text-[10px] uppercase tracking-[0.14em] text-text-disabled">Workload monitor</div>
      </div>
    </div>
  );
}

// The poller runs on its schedule; this lets an admin force a poll now (e.g. after a rule
// change, or to confirm setup). Requires the app SP to have run permission on the poller job.
function RunPollControl() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      const r = await api.opsPoll();
      toast({ kind: "success", title: "Poll triggered", detail: `run_id ${r.run_id} · job ${r.job_id}` });
    } catch (e) {
      toast({ kind: "error", title: "Poll failed", detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Pill icon={RefreshCw} iconColor="#4C8DFF" disabled={busy} onClick={run}>
      {busy ? "Polling…" : "Run poll"}
    </Pill>
  );
}

// Two-letter avatar initials from the workspace label (e.g. "acme-prod" -> "AC").
function initials(label: string): string {
  const parts = label.replace(/[_-]+/g, " ").trim().split(/\s+/).filter(Boolean);
  const letters = parts.length >= 2 ? parts[0][0] + parts[1][0] : (parts[0] ?? "").slice(0, 2);
  return (letters || "WT").toUpperCase();
}

export function Layout({
  view,
  onNavigate,
  dark,
  onToggleTheme,
  children,
}: {
  view: ViewKey;
  onNavigate: (v: ViewKey) => void;
  dark: boolean;
  onToggleTheme: () => void;
  children: ReactNode;
}) {
  const cfg = useApi(() => api.config());
  const workspace = cfg.data?.workspace ?? "Databricks";
  return (
    <div className="flex h-full min-h-screen bg-app">
      {/* sidebar */}
      <aside className="fixed inset-y-0 left-0 flex w-[240px] flex-col border-r border-line bg-sidebar">
        <div className="flex h-16 items-center border-b border-line px-3">
          <Wordmark />
        </div>
        <nav className="flex flex-1 flex-col gap-1 p-3">
          {NAV.map((item) => {
            const active = item.key === view;
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                onClick={() => onNavigate(item.key)}
                className={`group relative flex items-center gap-3 rounded-[10px] px-3 py-2 text-[13px] font-medium transition-colors ${
                  active ? "bg-hover text-text-primary" : "text-text-secondary hover:bg-hover hover:text-text-primary"
                }`}
              >
                {active && <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r bg-lava" />}
                <Icon size={17} className={active ? "text-lava-warm" : ""} />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="border-t border-line p-3 text-[11px] text-text-disabled">
          <div className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
            Connected · Lakebase
          </div>
        </div>
      </aside>

      {/* main column */}
      <div className="flex min-w-0 flex-1 flex-col pl-[240px]">
        {/* top bar */}
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-4 border-b border-line bg-app/85 px-6 backdrop-blur">
          <div className="text-[15px] font-medium text-text-primary">{TITLES[view]}</div>
          <div className="flex items-center gap-3">
            <RunPollControl />
            <div className="mx-1 h-6 w-px bg-line" />
            <button
              onClick={onToggleTheme}
              className="flex h-9 w-9 items-center justify-center rounded-full border border-line text-text-secondary transition-colors hover:bg-hover hover:text-text-primary"
              aria-label="Toggle theme"
            >
              {dark ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <span className="hidden items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-[12px] text-text-secondary sm:inline-flex">
              <Play size={12} className="text-lava-warm" />
              {workspace}
            </span>
            <span
              className="flex h-9 w-9 items-center justify-center rounded-full text-[13px] font-semibold text-white"
              style={{ background: "linear-gradient(135deg,#FF5F46,#FF3621)" }}
              title={workspace}
            >
              {initials(workspace)}
            </span>
          </div>
        </header>

        <main className="wt-fade-in mx-auto w-full max-w-[1400px] flex-1 px-6 py-7">{children}</main>
      </div>
    </div>
  );
}
