import { useState, useEffect } from "react";
import { DollarSign, Save, AlertTriangle, Users, Clock } from "lucide-react";
import { api, type BudgetConfig } from "../api";
import { useApi } from "../hooks";
import { useToast } from "../components/Toast";
import { Card, EmptyState, PageHeader, Spinner, Button, Chip, Input, Toggle } from "../components/ui";
import { fmtCost, fmtAge } from "../lib/format";

function ConfigPanel({ onSaved }: { onSaved: () => void }) {
  const cfg = useApi(() => api.budgetConfig());
  const toast = useToast();
  const [draft, setDraft] = useState<Partial<BudgetConfig>>({});
  const [saving, setSaving] = useState(false);

  // Seed the editable draft once config loads.
  useEffect(() => {
    if (cfg.data) setDraft(cfg.data);
  }, [cfg.data]);

  const set = <K extends keyof BudgetConfig>(k: K, v: BudgetConfig[K]) => setDraft((d) => ({ ...d, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      await api.patchBudgetConfig({
        user_budget_usd: Number(draft.user_budget_usd),
        window_hours: Number(draft.window_hours),
        scan_every_min: Number(draft.scan_every_min),
        workspace_ids: draft.workspace_ids ?? "",
        enabled: !!draft.enabled,
        notify_users: !!draft.notify_users,
      });
      toast({ kind: "success", title: "Budget config saved" });
      cfg.refresh();
      onSaved();
    } catch (e) {
      toast({ kind: "error", title: "Could not save", detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  if (cfg.loading && !cfg.data) return <div className="flex justify-center py-10"><Spinner /></div>;

  const field = "mb-1 block text-[11px] font-medium uppercase tracking-wide text-text-disabled";
  return (
    <Card>
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2 text-[13px] font-semibold text-text-primary">
          <DollarSign size={15} className="text-lava-warm" /> Budget configuration
        </div>
        <div className="flex items-center gap-2">
          {cfg.data?.last_scan_at && (
            <span className="text-[11px] text-text-disabled">last scan {fmtAge(cfg.data.last_scan_at)}</span>
          )}
          <Toggle checked={!!draft.enabled} onChange={() => set("enabled", !draft.enabled)} caption="Budget scanning enabled" />
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <label className={field}>Budget $ / user</label>
          <Input type="number" min={0} step="1" value={draft.user_budget_usd ?? ""}
                 onChange={(e) => set("user_budget_usd", Number(e.target.value))} />
        </div>
        <div>
          <label className={field}>Window (hours)</label>
          <Input type="number" min={1} value={draft.window_hours ?? ""}
                 onChange={(e) => set("window_hours", Number(e.target.value))} />
        </div>
        <div>
          <label className={field}>Scan every (min)</label>
          <Input type="number" min={5} value={draft.scan_every_min ?? ""}
                 onChange={(e) => set("scan_every_min", Number(e.target.value))} />
        </div>
        <div className="sm:col-span-2 lg:col-span-3">
          <label className={field}>Workspace IDs to scan (comma-separated)</label>
          <Input value={draft.workspace_ids ?? ""} placeholder="7474659985291613, 123456789012345"
                 onChange={(e) => set("workspace_ids", e.target.value)} />
        </div>
        <div className="flex items-end sm:col-span-2 lg:col-span-3">
          <Toggle checked={!!draft.notify_users} onChange={() => set("notify_users", !draft.notify_users)}
                  caption="Also email the over-budget user (not just admins)" />
        </div>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <Button variant="primary" icon={Save} loading={saving} onClick={save}>Save</Button>
        <span className="text-[11px] text-text-disabled">
          Admins (subscribers) are always notified; the over-budget user is emailed only when the toggle is on.
        </span>
      </div>
    </Card>
  );
}

export function Budget() {
  const status = useApi(() => api.budgetStatus(), { intervalMs: 60000 });
  const alerts = useApi(() => api.budgetAlerts(), { intervalMs: 60000 });

  const users = status.data?.users ?? [];
  const th = "px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-text-disabled";
  const td = "px-3 py-2 text-[13px] text-text-secondary tabular-nums";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Budget"
        subtitle="Per-user SQL cost over the rolling window — settled workspace SQL cost prorated by each user's query-history execution share. The scan runs on an hourly cadence; each over-budget user is emailed at most once per window (default 24h)."
      />

      <ConfigPanel onSaved={() => { status.refresh(); }} />

      {/* live per-user cost */}
      <Card padded={false}>
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-text-primary">
            <Users size={15} className="text-lava-warm" /> Per-user cost
          </div>
          {status.data && (
            <div className="flex items-center gap-2 text-[11px] text-text-disabled">
              <Chip>budget {fmtCost(status.data.budget_usd)}</Chip>
              <Chip>{status.data.window_hours}h window</Chip>
            </div>
          )}
        </div>
        {status.error ? (
          <EmptyState icon={AlertTriangle} title="Could not load costs" hint={status.error} />
        ) : status.loading && !status.data ? (
          <div className="flex justify-center py-12"><Spinner /></div>
        ) : users.length === 0 ? (
          <EmptyState icon={DollarSign} title="No user cost yet" hint="The proration query returned no rows for the configured workspace(s)/window." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead><tr className="border-b border-line">
                <th className={th}>User</th><th className={th}>Queries</th><th className={th}>Exec min</th>
                <th className={th}>% exec</th><th className={th}>Est. DBUs</th><th className={th}>Est. cost</th>
              </tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.user_identity} className={`border-b border-line/60 ${u.over_budget ? "bg-danger/5" : ""}`}>
                    <td className="px-3 py-2 text-[13px] text-text-primary">
                      <span className="inline-flex items-center gap-2">
                        {u.over_budget && <AlertTriangle size={13} className="text-danger" />}
                        {u.user_identity}
                      </span>
                    </td>
                    <td className={td}>{u.query_count}</td>
                    <td className={td}>{u.total_execution_min}</td>
                    <td className={td}>{u.pct_of_total_execution}%</td>
                    <td className={td}>{u.estimated_dbus}</td>
                    <td className={`px-3 py-2 text-[13px] font-medium tabular-nums ${u.over_budget ? "text-danger" : "text-text-primary"}`}>
                      {fmtCost(u.estimated_list_cost_usd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* audit trail of budget emails */}
      <Card padded={false}>
        <div className="flex items-center gap-2 border-b border-line px-4 py-3 text-[13px] font-semibold text-text-primary">
          <Clock size={15} className="text-lava-warm" /> Budget alerts sent
        </div>
        {(alerts.data ?? []).length === 0 ? (
          <EmptyState icon={Clock} title="No budget alerts yet" hint="When a user crosses budget, the email sent to them and the admins is logged here." />
        ) : (
          <div className="divide-y divide-line">
            {(alerts.data ?? []).map((a) => (
              <div key={a.id} className="flex items-center gap-3 px-4 py-2.5">
                <Chip color={a.result === "sent" ? "#3DD68C" : "#E5484D"}>{a.result}</Chip>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] text-text-primary">{a.user_identity} · {fmtCost(a.est_cost_usd)} <span className="text-text-disabled">/ {fmtCost(a.budget_usd)}</span></div>
                  <div className="truncate text-[11px] text-text-disabled">→ {a.recipients ?? "—"}{a.error ? ` · ${a.error}` : ""}</div>
                </div>
                <span className="text-[11px] text-text-disabled">{fmtAge(a.alerted_at)}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
