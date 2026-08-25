import { AlertOctagon, DollarSign, LayoutGrid, Radar, TrendingUp, Activity, Clock } from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks";
import { Card, EmptyState, PageHeader, SeverityChip, Spinner, Button } from "../components/ui";
import { LineChart, BarList } from "../components/charts";
import { fmtAge, fmtCost, workloadLabel } from "../lib/format";
import { RefreshCw } from "lucide-react";

const REFRESH_MS = 15000;

function StatTile({
  icon: Icon,
  label,
  value,
  sub,
  accent = "#2272EB",
  children,
}: {
  icon: typeof Radar;
  label: string;
  value: string;
  sub?: string;
  accent?: string;
  children?: React.ReactNode;
}) {
  return (
    <Card className="relative overflow-hidden">
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <div className="text-[12px] font-medium uppercase tracking-wide text-text-secondary">{label}</div>
          <div className="mt-2 text-[28px] font-light leading-none tracking-tight text-text-primary">{value}</div>
          {sub && <div className="mt-2 text-[12px] text-text-secondary">{sub}</div>}
          {children}
        </div>
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px]"
          style={{ backgroundColor: `${accent}1f`, color: accent }}
        >
          <Icon size={18} />
        </span>
      </div>
    </Card>
  );
}

export function Dashboard() {
  const summary = useApi(() => api.summary(), { intervalMs: REFRESH_MS });
  const trends = useApi(() => api.trends(24), { intervalMs: 60000 });

  const s = summary.data;
  const sevOrder: ("critical" | "warning" | "info")[] = ["critical", "warning", "info"];
  const totalOpen = s ? sevOrder.reduce((a, k) => a + (s.open_by_severity[k] ?? 0), 0) : 0;
  const cardsTotal = s ? Object.values(s.cards_by_status).reduce((a, b) => a + (b ?? 0), 0) : 0;

  const t = trends.data;
  const timeline = t?.timeline ?? [];
  const byType = t?.by_type ?? [];

  return (
    <div>
      <PageHeader
        title="Workload Watchtower"
        subtitle="Live view of flagged Databricks workloads, triage load, and spend."
        actions={
          <Button icon={RefreshCw} onClick={() => { summary.refresh(); trends.refresh(); }}>
            Refresh
          </Button>
        }
      />

      {/* stat tiles */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          icon={AlertOctagon}
          label="Open findings"
          value={summary.loading && !s ? "—" : String(totalOpen)}
          accent="#E5484D"
        >
          <div className="mt-3 flex flex-wrap gap-1.5">
            {sevOrder.map((k) =>
              s?.open_by_severity[k] ? (
                <span key={k} className="inline-flex items-center gap-1">
                  <SeverityChip severity={k} />
                  <span className="text-[12px] tabular-nums text-text-secondary">{s.open_by_severity[k]}</span>
                </span>
              ) : null,
            )}
            {totalOpen === 0 && <span className="text-[12px] text-text-secondary">No open findings</span>}
          </div>
        </StatTile>

        <StatTile
          icon={DollarSign}
          label="Open est. cost"
          value={s ? fmtCost(s.open_est_cost_usd) : "—"}
          sub="Estimated spend across open findings"
          accent="#FFAB00"
        />

        <StatTile
          icon={LayoutGrid}
          label="Triage cards"
          value={String(cardsTotal)}
          accent="#2272EB"
        >
          <div className="mt-3 flex flex-wrap gap-1.5 text-[11px] text-text-secondary">
            {(["new", "investigating", "assigned", "resolved"] as const).map((k) => (
              <span key={k} className="rounded-full border border-line px-2 py-0.5">
                {k} <span className="tabular-nums text-text-primary">{s?.cards_by_status[k] ?? 0}</span>
              </span>
            ))}
          </div>
        </StatTile>

        <StatTile
          icon={Radar}
          label="Last poll"
          value={s?.last_poll?.workloads_seen != null ? `${s.last_poll.workloads_seen}` : "—"}
          sub={
            s?.last_poll?.finished_at
              ? `${s.last_poll.findings_new ?? 0} new · ${fmtAge(s.last_poll.finished_at)}`
              : "No poll runs yet"
          }
          accent="#3DD68C"
        >
          {s?.last_poll?.seen_by_type && (
            <div className="mt-3 flex flex-wrap gap-1.5 text-[11px] text-text-secondary">
              {Object.entries(s.last_poll.seen_by_type)
                .sort((a, b) => b[1] - a[1])
                .map(([k, v]) => (
                  <span key={k} className="rounded-full border border-line px-2 py-0.5">
                    {workloadLabel(k)} <span className="tabular-nums text-text-primary">{v}</span>
                  </span>
                ))}
            </div>
          )}
        </StatTile>
      </div>

      {/* trends */}
      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <TrendingUp size={16} className="text-brand" />
              <h2 className="text-sm font-medium text-text-primary">Flagged spend over time</h2>
              <span className="text-[11px] text-text-disabled">last 24h</span>
            </div>
            {trends.loading && <Spinner size={14} />}
          </div>
          {trends.error ? (
            <EmptyState icon={Activity} title="Trends unavailable" hint={trends.error} />
          ) : timeline.length === 0 ? (
            <EmptyState
              icon={Activity}
              title="No trend data yet"
              hint="Unity Catalog snapshots populate as the poller runs. Trigger a poll or a load generator to seed history."
            />
          ) : (
            <>
              <LineChart data={timeline.map((d) => ({ label: d.hour, value: d.est_cost_usd }))} color="#2272EB" />
              <div className="mt-2 flex items-center justify-between text-[11px] text-text-disabled">
                <span>{timeline[0]?.hour ? new Date(timeline[0].hour.replace(" ", "T")).toLocaleString() : ""}</span>
                <span className="flex items-center gap-1">
                  <Clock size={11} /> {timeline.length} hourly buckets
                </span>
              </div>
            </>
          )}
        </Card>

        <Card>
          <div className="mb-4 flex items-center gap-2">
            <LayoutGrid size={16} className="text-lava-warm" />
            <h2 className="text-sm font-medium text-text-primary">Cost by workload type</h2>
          </div>
          {byType.length === 0 ? (
            <EmptyState icon={Activity} title="No breakdown yet" hint="By-type spend appears once snapshots exist." />
          ) : (
            <BarList
              data={byType.map((d) => ({ label: workloadLabel(d.workload_type), value: d.est_cost_usd }))}
              color="#FF5F46"
              fmt={(v) => fmtCost(v)}
            />
          )}
        </Card>
      </div>

      {/* workload count timeline */}
      {timeline.length > 0 && (
        <Card className="mt-4">
          <div className="mb-4 flex items-center gap-2">
            <Activity size={16} className="text-success" />
            <h2 className="text-sm font-medium text-text-primary">Flagged workloads over time</h2>
          </div>
          <LineChart data={timeline.map((d) => ({ label: d.hour, value: d.workloads }))} color="#3DD68C" height={130} />
        </Card>
      )}
    </div>
  );
}
