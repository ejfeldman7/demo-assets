import type { Severity, WorkloadType } from "../api";
import { Database, GitBranch, Play, Terminal, type LucideIcon } from "lucide-react";

export function fmtCost(v: number | string | null | undefined): string {
  const n = typeof v === "string" ? Number(v) : v;
  if (n == null || !Number.isFinite(n)) return "—";
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${n.toFixed(2)}`;
}

export function fmtElapsed(v: number | string | null | undefined): string {
  const sec = typeof v === "string" ? Number(v) : v;
  if (sec == null || !Number.isFinite(sec)) return "—";
  const m = sec / 60;
  if (m < 1) return `${Math.round(sec)}s`;
  if (m < 60) return `${m.toFixed(m < 10 ? 1 : 0)}m`;
  const h = m / 60;
  return `${h.toFixed(1)}h`;
}

export function fmtAge(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  const m = s / 60;
  if (m < 60) return `${Math.round(m)}m ago`;
  const h = m / 60;
  if (h < 24) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// severity ordering for sorts (higher = more severe)
export const SEVERITY_RANK: Record<string, number> = { critical: 3, warning: 2, info: 1 };

export interface SevMeta {
  label: string;
  color: string; // hex
  // tailwind-ish inline styles are applied via style props using color
}
export function sevMeta(sev: Severity | string): SevMeta {
  switch (sev) {
    case "critical":
      return { label: "Critical", color: "#E5484D" };
    case "warning":
      return { label: "Warning", color: "#FFAB00" };
    case "info":
      return { label: "Info", color: "#4C8DFF" };
    default:
      return { label: sev, color: "#6B7482" };
  }
}

export function workloadIcon(t: WorkloadType | string): LucideIcon {
  switch (t) {
    case "query":
      return Terminal;
    case "job_run":
      return Play;
    case "pipeline":
      return GitBranch;
    default:
      return Database;
  }
}

export function workloadLabel(t: WorkloadType | string): string {
  switch (t) {
    case "query":
      return "Query";
    case "job_run":
      return "Job run";
    case "pipeline":
      return "Pipeline";
    default:
      return t;
  }
}

export function truncate(s: string | null | undefined, n = 80): string {
  if (!s) return "—";
  const clean = s.replace(/\s+/g, " ").trim();
  return clean.length > n ? clean.slice(0, n - 1) + "…" : clean;
}
