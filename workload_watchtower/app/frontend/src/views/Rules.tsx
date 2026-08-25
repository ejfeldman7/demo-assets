import { useState } from "react";
import { SlidersHorizontal, Plus, Trash2, Check, X, Pencil } from "lucide-react";
import { api, type Rule, type Severity } from "../api";
import { useApi } from "../hooks";
import { useToast } from "../components/Toast";
import { Card, EmptyState, PageHeader, SeverityChip, Spinner, Select, Input, Button, Toggle, Chip } from "../components/ui";
import { fmtCost, fmtElapsed, workloadLabel } from "../lib/format";

const WORKLOAD_TYPES = ["query", "job_run", "pipeline"];
const METRICS = ["elapsed_sec", "est_cost_usd"];
const SEVERITIES: Severity[] = ["info", "warning", "critical"];
const ACTIONS = ["card", "card_email"];

function metricLabel(m: string, threshold: number) {
  return m === "est_cost_usd" ? fmtCost(threshold) : fmtElapsed(threshold);
}

export function Rules() {
  const rules = useApi(() => api.rules());
  const toast = useToast();
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Partial<Rule>>({});
  const [showAdd, setShowAdd] = useState(false);

  const list = [...(rules.data ?? [])].sort((a, b) => a.workload_type.localeCompare(b.workload_type) || a.threshold - b.threshold);

  const toggle = async (r: Rule) => {
    try {
      await api.patchRule(r.id, { enabled: !r.enabled });
      rules.refreshQuiet();
      toast({ kind: "info", title: `${r.name} ${!r.enabled ? "enabled" : "disabled"}` });
    } catch (e) {
      toast({ kind: "error", title: "Toggle failed", detail: e instanceof Error ? e.message : String(e) });
    }
  };

  const saveEdit = async (r: Rule) => {
    try {
      await api.patchRule(r.id, {
        threshold: draft.threshold ?? r.threshold,
        severity: (draft.severity as Severity) ?? r.severity,
        action: draft.action ?? r.action,
      });
      setEditing(null);
      setDraft({});
      rules.refresh();
      toast({ kind: "success", title: "Rule updated" });
    } catch (e) {
      toast({ kind: "error", title: "Update failed", detail: e instanceof Error ? e.message : String(e) });
    }
  };

  const remove = async (r: Rule) => {
    try {
      await api.deleteRule(r.id);
      rules.refresh();
      toast({ kind: "info", title: `Deleted "${r.name}"` });
    } catch (e) {
      toast({ kind: "error", title: "Delete failed", detail: e instanceof Error ? e.message : String(e) });
    }
  };

  return (
    <div>
      <PageHeader
        title="Rules"
        subtitle="Thresholds the poller evaluates each cycle. Matches create findings and triage cards."
        actions={
          <Button variant="primary" icon={Plus} onClick={() => setShowAdd((v) => !v)}>
            Add rule
          </Button>
        }
      />

      {showAdd && <AddRuleForm onClose={() => setShowAdd(false)} onCreated={() => { setShowAdd(false); rules.refresh(); }} />}

      <Card padded={false}>
        {rules.error ? (
          <EmptyState icon={SlidersHorizontal} title="Could not load rules" hint={rules.error} />
        ) : rules.loading && !rules.data ? (
          <div className="flex items-center justify-center py-16">
            <Spinner />
          </div>
        ) : list.length === 0 ? (
          <EmptyState icon={SlidersHorizontal} title="No rules yet" hint="Add a rule to start flagging workloads." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-secondary">
                  <th className="py-2.5 pl-4 font-medium">Rule</th>
                  <th className="py-2.5 pr-3 font-medium">Workload</th>
                  <th className="py-2.5 pr-3 font-medium">Metric</th>
                  <th className="py-2.5 pr-3 font-medium">Threshold</th>
                  <th className="py-2.5 pr-3 font-medium">Severity</th>
                  <th className="py-2.5 pr-3 font-medium">Action</th>
                  <th className="py-2.5 pr-3 font-medium">Enabled</th>
                  <th className="py-2.5 pr-4 text-right font-medium">Edit</th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => {
                  const isEdit = editing === r.id;
                  return (
                    <tr key={r.id} className="border-b border-line transition-colors hover:bg-hover">
                      <td className="py-2.5 pl-4 font-medium text-text-primary">{r.name}</td>
                      <td className="py-2.5 pr-3">
                        <Chip>{workloadLabel(r.workload_type)}</Chip>
                      </td>
                      <td className="py-2.5 pr-3 font-mono text-[12px] text-text-secondary">{r.metric}</td>
                      <td className="py-2.5 pr-3">
                        {isEdit ? (
                          <Input
                            type="number"
                            defaultValue={r.threshold}
                            onChange={(e) => setDraft((d) => ({ ...d, threshold: Number(e.target.value) }))}
                            className="w-24 py-1"
                          />
                        ) : (
                          <span className="tabular-nums text-text-primary">{metricLabel(r.metric, r.threshold)}</span>
                        )}
                      </td>
                      <td className="py-2.5 pr-3">
                        {isEdit ? (
                          <Select
                            defaultValue={r.severity}
                            onChange={(e) => setDraft((d) => ({ ...d, severity: e.target.value as Severity }))}
                            className="py-1"
                          >
                            {SEVERITIES.map((s) => (
                              <option key={s} value={s}>
                                {s}
                              </option>
                            ))}
                          </Select>
                        ) : (
                          <SeverityChip severity={r.severity} />
                        )}
                      </td>
                      <td className="py-2.5 pr-3">
                        {isEdit ? (
                          <Select
                            defaultValue={r.action}
                            onChange={(e) => setDraft((d) => ({ ...d, action: e.target.value }))}
                            className="py-1"
                          >
                            {ACTIONS.map((a) => (
                              <option key={a} value={a}>
                                {a}
                              </option>
                            ))}
                          </Select>
                        ) : (
                          <span className="text-text-secondary">{r.action}</span>
                        )}
                      </td>
                      <td className="py-2.5 pr-3">
                        <Toggle checked={r.enabled} onChange={() => toggle(r)} label={`Toggle ${r.name}`} />
                      </td>
                      <td className="py-2.5 pr-4">
                        <div className="flex items-center justify-end gap-1">
                          {isEdit ? (
                            <>
                              <button
                                onClick={() => saveEdit(r)}
                                className="rounded-md p-1.5 text-success transition-colors hover:bg-hover"
                                aria-label="Save"
                              >
                                <Check size={15} />
                              </button>
                              <button
                                onClick={() => { setEditing(null); setDraft({}); }}
                                className="rounded-md p-1.5 text-text-secondary transition-colors hover:bg-hover"
                                aria-label="Cancel"
                              >
                                <X size={15} />
                              </button>
                            </>
                          ) : (
                            <>
                              <button
                                onClick={() => { setEditing(r.id); setDraft({}); }}
                                className="rounded-md p-1.5 text-text-secondary transition-colors hover:bg-hover hover:text-text-primary"
                                aria-label="Edit"
                              >
                                <Pencil size={14} />
                              </button>
                              <button
                                onClick={() => remove(r)}
                                className="rounded-md p-1.5 text-text-secondary transition-colors hover:bg-hover hover:text-critical"
                                aria-label="Delete"
                              >
                                <Trash2 size={14} />
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function AddRuleForm({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState({
    name: "",
    workload_type: "query",
    metric: "elapsed_sec",
    threshold: 1800,
    severity: "warning" as Severity,
    action: "card_email",
    enabled: true,
  });
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!form.name.trim()) {
      toast({ kind: "error", title: "Name is required" });
      return;
    }
    setSaving(true);
    try {
      await api.createRule(form);
      toast({ kind: "success", title: `Rule "${form.name}" created` });
      onCreated();
    } catch (e) {
      toast({ kind: "error", title: "Create failed", detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="mb-4">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-medium text-text-primary">New rule</h3>
        <button onClick={onClose} className="rounded-md p-1 text-text-secondary hover:bg-hover" aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        <label className="flex flex-col gap-1.5 lg:col-span-3">
          <span className="text-[12px] text-text-secondary">Name</span>
          <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Runaway serverless query (45m)" />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[12px] text-text-secondary">Workload type</span>
          <Select value={form.workload_type} onChange={(e) => setForm({ ...form, workload_type: e.target.value })}>
            {WORKLOAD_TYPES.map((w) => (
              <option key={w} value={w}>
                {workloadLabel(w)}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[12px] text-text-secondary">Metric</span>
          <Select value={form.metric} onChange={(e) => setForm({ ...form, metric: e.target.value })}>
            {METRICS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[12px] text-text-secondary">Threshold {form.metric === "est_cost_usd" ? "(USD)" : "(seconds)"}</span>
          <Input type="number" value={form.threshold} onChange={(e) => setForm({ ...form, threshold: Number(e.target.value) })} />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[12px] text-text-secondary">Severity</span>
          <Select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value as Severity })}>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[12px] text-text-secondary">Action</span>
          <Select value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })}>
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </Select>
        </label>
      </div>
      <div className="mt-4 flex items-center justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" icon={Plus} loading={saving} onClick={submit}>
          Create rule
        </Button>
      </div>
    </Card>
  );
}
