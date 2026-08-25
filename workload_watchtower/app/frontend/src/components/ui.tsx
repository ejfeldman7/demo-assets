import type { ButtonHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { Loader2, type LucideIcon } from "lucide-react";
import { sevMeta } from "../lib/format";

// ── Card ──────────────────────────────────────────────────────────────────
export function Card({ children, className = "", padded = true }: { children: ReactNode; className?: string; padded?: boolean }) {
  return (
    <div className={`rounded-xl border border-line bg-surface shadow-card ${padded ? "p-5" : ""} ${className}`}>
      {children}
    </div>
  );
}

// ── Spinner ──────────────────────────────────────────────────────────────
export function Spinner({ size = 18, className = "" }: { size?: number; className?: string }) {
  return <Loader2 size={size} className={`animate-spin text-text-secondary ${className}`} />;
}

// ── Severity chip ──────────────────────────────────────────────────────────
export function SeverityChip({ severity }: { severity: string }) {
  const m = sevMeta(severity);
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ color: m.color, backgroundColor: `${m.color}1f` }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: m.color }} />
      {m.label}
    </span>
  );
}

// ── generic chip ─────────────────────────────────────────────────────────
export function Chip({ children, color, className = "" }: { children: ReactNode; color?: string; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-0.5 text-[11px] font-medium text-text-secondary ${className}`}
      style={color ? { color, backgroundColor: `${color}1a`, borderColor: `${color}33` } : undefined}
    >
      {children}
    </span>
  );
}

// ── Buttons ─────────────────────────────────────────────────────────────
type Variant = "primary" | "ghost" | "danger";
interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  icon?: LucideIcon;
  loading?: boolean;
}
export function Button({ variant = "ghost", icon: Icon, loading, children, className = "", disabled, ...rest }: BtnProps) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-[10px] px-3.5 py-2 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const variants: Record<Variant, string> = {
    primary: "bg-brand text-white hover:bg-brand-hover",
    ghost: "border border-line bg-surface text-text-primary hover:bg-hover",
    danger: "border border-line bg-surface text-critical hover:bg-hover",
  };
  return (
    <button className={`${base} ${variants[variant]} ${className}`} disabled={disabled || loading} {...rest}>
      {loading ? <Loader2 size={15} className="animate-spin" /> : Icon ? <Icon size={15} /> : null}
      {children}
    </button>
  );
}

// ── Pill button (rounded, icon + label) — the Genie-style pill ──────────────
export function Pill({
  icon: Icon,
  children,
  active,
  iconColor,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon?: LucideIcon; active?: boolean; iconColor?: string }) {
  return (
    <button
      className={`inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-[13px] font-medium transition-colors ${
        active
          ? "border-brand/50 bg-brand/10 text-text-primary"
          : "border-line bg-surface text-text-secondary hover:bg-hover hover:text-text-primary"
      } ${className}`}
      {...rest}
    >
      {Icon && <Icon size={15} style={iconColor ? { color: iconColor } : undefined} />}
      {children}
    </button>
  );
}

// ── Select ────────────────────────────────────────────────────────────────
export function Select({ className = "", ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={`rounded-[10px] border border-line bg-app px-2.5 py-1.5 text-[13px] text-text-primary outline-none transition-colors focus:border-brand ${className}`}
      {...rest}
    />
  );
}

// ── Text input ──────────────────────────────────────────────────────────
export function Input({ className = "", ...rest }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`rounded-[10px] border border-line bg-app px-3 py-2 text-sm text-text-primary outline-none transition-colors placeholder:text-text-disabled focus:border-brand ${className}`}
      {...rest}
    />
  );
}

// ── Toggle switch ──────────────────────────────────────────────────────────
export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: () => void; label?: string }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={onChange}
      className="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors"
      style={{ backgroundColor: checked ? "#2272EB" : "var(--border)" }}
    >
      <span
        className="inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform"
        style={{ transform: checked ? "translateX(19px)" : "translateX(3px)" }}
      />
    </button>
  );
}

// ── Empty state ─────────────────────────────────────────────────────────
export function EmptyState({ icon: Icon, title, hint }: { icon: LucideIcon; title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full border border-line bg-app text-text-secondary">
        <Icon size={22} />
      </div>
      <div className="text-sm font-medium text-text-primary">{title}</div>
      {hint && <div className="mt-1 max-w-sm text-[13px] text-text-secondary">{hint}</div>}
    </div>
  );
}

// ── Page header ───────────────────────────────────────────────────────────
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-light tracking-tight text-text-primary">{title}</h1>
        {subtitle && <p className="mt-1 text-[13px] text-text-secondary">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
