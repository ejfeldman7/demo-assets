import { useMemo, useState } from "react";
import { ListFilter, ChevronDown, ChevronRight, Search, Sparkles, Ban } from "lucide-react";
import { api, type Finding } from "../api";
import { useApi } from "../hooks";
import { useToast } from "../components/Toast";
import { CopilotModal } from "../components/CopilotModal";
import { Card, EmptyState, PageHeader, SeverityChip, Spinner, Select, Button, Chip } from "../components/ui";
import { RefreshCw } from "lucide-react";

// Kill (cancel) is only possible for live, cancellable workloads — queries can't be cancelled by API.
const KILLABLE = ["job_run", "pipeline", "cluster"];
import { SEVERITY_RANK, fmtCost, fmtElapsed, fmtAge, truncate, workloadIcon, workloadLabel } from "../lib/format";

type SortKey = "severity" | "elapsed_sec" | "est_cost_usd";
// findings.status values (NOT card statuses)
const STATUS_OPTIONS = ["", "open", "acknowledged", "resolved", "expired"];

export function Findings() {
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<SortKey>("severity");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [explain, setExplain] = useState<{ findingId: number; context: string } | null>(null);
  const [killing, setKilling] = useState<number | null>(null);
  const toast = useToast();
  const findings = useApi(() => api.findings(status || undefined, 200), { intervalMs: 20000, deps: [status] });

  const doKill = async (f: Finding) => {
    if (!window.confirm(`Cancel this ${f.workload_type} (${f.object_name ?? f.external_id})? This stops the running workload.`))
      return;
    setKilling(f.id);
    try {
      const r = await api.killFinding(f.id);
      toast({ kind: "success", title: "Workload cancelled", detail: r.detail });
      findings.refresh();
    } catch (e) {
      toast({ kind: "error", title: "Kill failed", detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setKilling(null);
    }
  };

  const rows = useMemo(() => {
    const list = [...(findings.data ?? [])];
    list.sort((a, b) => {
      if (sort === "severity") return (SEVERITY_RANK[b.severity] ?? 0) - (SEVERITY_RANK[a.severity] ?? 0);
      return (b[sort] ?? 0) - (a[sort] ?? 0);
    });
    return list;
  }, [findings.data, sort]);

  return (
    <div>
      <PageHeader
        title="Findings"
        subtitle="Every workload flagged by an enabled rule, newest polls first."
        actions={
          <>
            <Select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter status">
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s ? s[0].toUpperCase() + s.slice(1) : "All statuses"}
                </option>
              ))}
            </Select>
            <Select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort by">
              <option value="severity">Sort: severity</option>
              <option value="elapsed_sec">Sort: elapsed</option>
              <option value="est_cost_usd">Sort: cost</option>
            </Select>
            <Button icon={RefreshCw} onClick={() => findings.refresh()}>
              Refresh
            </Button>
          </>
        }
      />

      <Card padded={false}>
        {findings.error ? (
          <EmptyState icon={ListFilter} title="Could not load findings" hint={findings.error} />
        ) : findings.loading && !findings.data ? (
          <div className="flex items-center justify-center py-16">
            <Spinner />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Search}
            title="No findings"
            hint="Nothing has tripped a rule yet. Generate a slow query and run a poll from the top bar to seed findings."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-secondary">
                  <th className="w-8 py-2.5 pl-4"></th>
                  <th className="py-2.5 pr-3 font-medium">Workload</th>
                  <th className="py-2.5 pr-3 font-medium">Object</th>
                  <th className="py-2.5 pr-3 font-medium">Owner</th>
                  <th className="py-2.5 pr-3 font-medium">Rule</th>
                  <th className="py-2.5 pr-3 text-right font-medium">Elapsed</th>
                  <th className="py-2.5 pr-3 text-right font-medium">Cost</th>
                  <th className="py-2.5 pr-3 font-medium">Severity</th>
                  <th className="py-2.5 pr-3 font-medium">Seen</th>
                  <th className="py-2.5 pr-4 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((f) => (
                  <FindingRow
                    key={f.id}
                    f={f}
                    expanded={expanded === f.id}
                    onToggle={() => setExpanded(expanded === f.id ? null : f.id)}
                    onExplain={() => setExplain({ findingId: f.id, context: f.object_name ?? f.external_id })}
                    onKill={() => doKill(f)}
                    killing={killing === f.id}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <CopilotModal
        findingId={explain?.findingId ?? null}
        context={explain?.context}
        onClose={() => setExplain(null)}
      />
    </div>
  );
}

function FindingRow({
  f,
  expanded,
  onToggle,
  onExplain,
  onKill,
  killing,
}: {
  f: Finding;
  expanded: boolean;
  onToggle: () => void;
  onExplain: () => void;
  onKill: () => void;
  killing: boolean;
}) {
  const Icon = workloadIcon(f.workload_type);
  const hasQuery = !!f.query_text;
  return (
    <>
      <tr
        className={`border-b border-line transition-colors hover:bg-hover ${hasQuery ? "cursor-pointer" : ""}`}
        onClick={hasQuery ? onToggle : undefined}
      >
        <td className="py-2.5 pl-4 text-text-disabled">
          {hasQuery ? expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} /> : null}
        </td>
        <td className="py-2.5 pr-3">
          <span className="inline-flex items-center gap-2 text-text-primary">
            <Icon size={14} className="text-text-secondary" />
            {workloadLabel(f.workload_type)}
          </span>
        </td>
        <td className="max-w-[280px] truncate py-2.5 pr-3 font-mono text-[12px] text-text-primary" title={f.object_name ?? undefined}>
          {truncate(f.object_name, 60)}
        </td>
        <td className="py-2.5 pr-3 text-text-secondary">{f.owner ?? "—"}</td>
        <td className="py-2.5 pr-3 text-text-secondary">{f.rule_name ?? "—"}</td>
        <td className="py-2.5 pr-3 text-right tabular-nums text-text-primary">{fmtElapsed(f.elapsed_sec)}</td>
        <td className="py-2.5 pr-3 text-right tabular-nums text-text-primary">{fmtCost(f.est_cost_usd)}</td>
        <td className="py-2.5 pr-3">
          <SeverityChip severity={f.severity} />
        </td>
        <td className="py-2.5 pr-3 text-text-secondary">{fmtAge(f.last_seen ?? f.first_seen)}</td>
        <td className="py-2.5 pr-4 text-right">
          <div className="inline-flex items-center justify-end gap-1.5">
            {KILLABLE.includes(f.workload_type) && (
              <button
                onClick={(e) => { e.stopPropagation(); onKill(); }}
                disabled={killing}
                className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-[12px] font-medium text-text-secondary transition-colors hover:border-danger/40 hover:text-danger disabled:opacity-50"
                title="Cancel this running workload"
              >
                <Ban size={13} className="text-danger" />
                {killing ? "Killing…" : "Kill"}
              </button>
            )}
            <button
              onClick={(e) => { e.stopPropagation(); onExplain(); }}
              className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-[12px] font-medium text-text-secondary transition-colors hover:border-brand/40 hover:text-text-primary"
              title="Explain with Triage Copilot"
            >
              <Sparkles size={13} className="text-lava-warm" />
              Explain
            </button>
          </div>
        </td>
      </tr>
      {expanded && hasQuery && (
        <tr className="border-b border-line bg-app">
          <td />
          <td colSpan={9} className="px-3 py-3">
            <div className="mb-2 flex items-center gap-2">
              <Chip>{f.external_id}</Chip>
              {f.compute_ref && <Chip>compute: {f.compute_ref}</Chip>}
              <Chip>status: {f.status}</Chip>
            </div>
            <pre className="max-h-72 overflow-auto rounded-lg border border-line bg-surface p-3 font-mono text-[12px] leading-relaxed text-text-primary">
              {f.query_text}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}
