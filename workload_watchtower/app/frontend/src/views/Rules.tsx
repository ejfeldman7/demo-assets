import { useState } from "react";
import { SlidersHorizontal, Plus, Trash2, Check, X, Pencil } from "lucide-react";
import { api, type Rule, type Severity, type RuleKind } from "../api";
import { useApi } from "../hooks";
import { useToast } from "../components/Toast";
import { Card, EmptyState, PageHeader, SeverityChip, Spinner, Select, Input, Button, Toggle, Chip } from "../components/ui";
import { fmtCost, fmtElapsed, workloadLabel, truncate } from "../lib/format";

const WORKLOAD_TYPES = ["query", "job_run", "pipeline", "cluster"];
const METRICS = ["elapsed_sec", "est_cost_usd"];
const SEVERITIES: Severity[] = ["info", "warning", "critical"];
const KINDS: RuleKind[] = ["threshold", "pattern", "semantic"];
const ACTION_TOKENS = ["card", "email", "kill"] as const;

const parseActions = (a: string): Set<string> => new Set((a || "").split("_").filter(Boolean));
const joinActions = (s: Set<string>): string => ACTION_TOKENS.filter((t) => s.has(t)).join("_") || "none";
// Kill is only meaningful for cancellable live workloads; queries (incl. all pattern/semantic
// matches) can't be cancelled by API — the poller/app records those as unsupported.
const killable = (workloadType: string) =>
  ["query", "pattern_match", "job_run", "pipeline", "cluster"].includes(workloadType);

function ActionPicker({ value, onChange }: { value: Set<string>; onChange: (s: Set<string>) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {ACTION_TOKENS.map((t) => {
        const on = value.has(t);
        return (
          <button
            key={t}
            type="button"
            onClick={() => { const n = new Set(value); on ? n.delete(t) : n.add(t); onChange(n); }}
            className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 transition-colors ${
              on ? "bg-lava/15 text-lava-warm ring-lava/40" : "text-text-disabled ring-line hover:text-text-secondary"
            }`}
          >
            {t}
          </button>
        );
      })}
    </div>
  );
}

function kindLabel(k: RuleKind) {
  return k === "threshold" ? "Threshold" : k === "pattern" ? "Pattern" : "Semantic";
}

function matchSummary(r: Rule): string {
  if (r.kind === "threshold") return `${r.metric} ≥ ${r.metric === "est_cost_usd" ? fmtCost(r.threshold) : fmtElapsed(r.threshold)}`;
  if (r.kind === "pattern") return `${r.pattern_is_regex ? "regex" : "contains"}: ${truncate(r.pattern, 40)}`;
  return `intent: ${truncate(r.pattern, 40)}`;
}

export function Rules() {
  const rules = useApi(() => api.rules());
  const cfg = useApi(() => api.config());
  const isAdmin = !!cfg.data?.is_admin;
  const toast = useToast();
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Partial<Rule>>({});
  const [showAdd, setShowAdd] = useState(false);

  const list = [...(rules.data ?? [])].sort(
    (a, b) => a.kind.localeCompare(b.kind) || a.workload_type.localeCompare(b.workload_type) || a.threshold - b.threshold);

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
      const effAction = draft.action ?? r.action;
      const effSev = (draft.severity as Severity) ?? r.severity;
      // auto_kill only makes sense for a critical rule whose action includes kill; clear it
      // otherwise so a stale flag can't linger after kill/severity is changed.
      const effAutoKill = (draft.auto_kill ?? r.auto_kill) && effAction.split("_").includes("kill") && effSev === "critical";
      await api.patchRule(r.id, {
        threshold: draft.threshold ?? r.threshold,
        severity: effSev,
        action: effAction,
        pattern: draft.pattern ?? r.pattern,
        pattern_is_regex: draft.pattern_is_regex ?? r.pattern_is_regex,
        auto_kill: effAutoKill,
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
        subtitle="What the poller flags each cycle — duration/cost thresholds, query-text patterns, or LLM-classified intent. Matches create findings, cards, emails, and (for cancellable workloads) kills."
        actions={
          isAdmin ? <Button variant="primary" icon={Plus} onClick={() => setShowAdd((v) => !v)}>Add rule</Button> : undefined
        }
      />

      {isAdmin && showAdd && <AddRuleForm onClose={() => setShowAdd(false)} onCreated={() => { setShowAdd(false); rules.refresh(); }} />}

      <Card padded={false}>
        {rules.error ? (
          <EmptyState icon={SlidersHorizontal} title="Could not load rules" hint={rules.error} />
        ) : rules.loading && !rules.data ? (
          <div className="flex items-center justify-center py-16"><Spinner /></div>
        ) : list.length === 0 ? (
          <EmptyState icon={SlidersHorizontal} title="No rules yet" hint="Add a rule to start flagging workloads." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-line text-[11px] uppercase tracking-wide text-text-secondary">
                  <th className="py-2.5 pl-4 font-medium">Rule</th>
                  <th className="py-2.5 pr-3 font-medium">Kind</th>
                  <th className="py-2.5 pr-3 font-medium">Match</th>
                  <th className="py-2.5 pr-3 font-medium">Severity</th>
                  <th className="py-2.5 pr-3 font-medium">Actions</th>
                  <th className="py-2.5 pr-3 font-medium">Enabled</th>
                  <th className="py-2.5 pr-4 text-right font-medium">Edit</th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => {
                  const isEdit = editing === r.id;
                  const acts = draft.action !== undefined && isEdit ? parseActions(draft.action) : parseActions(r.action);
                  const sev = (isEdit ? draft.severity : undefined) ?? r.severity;
                  return (
                    <tr key={r.id} className="border-b border-line align-top transition-colors hover:bg-hover">
                      <td className="py-2.5 pl-4 font-medium text-text-primary">
                        {r.name}
                        <div className="text-[11px] font-normal text-text-disabled">{workloadLabel(r.workload_type)}</div>
                      </td>
                      <td className="py-2.5 pr-3"><Chip>{kindLabel(r.kind)}</Chip></td>
                      <td className="py-2.5 pr-3">
                        {!isEdit ? (
                          <span className="font-mono text-[12px] text-text-secondary">{matchSummary(r)}</span>
                        ) : r.kind === "threshold" ? (
                          <Input type="number" defaultValue={r.threshold} className="w-28 py-1"
                                 onChange={(e) => setDraft((d) => ({ ...d, threshold: Number(e.target.value) }))} />
                        ) : (
                          <div className="flex items-center gap-2">
                            <Input defaultValue={r.pattern ?? ""} className="w-56 py-1"
                                   onChange={(e) => setDraft((d) => ({ ...d, pattern: e.target.value }))} />
                            {r.kind === "pattern" && (
                              <Toggle checked={draft.pattern_is_regex ?? r.pattern_is_regex}
                                      onChange={() => setDraft((d) => ({ ...d, pattern_is_regex: !(d.pattern_is_regex ?? r.pattern_is_regex) }))}
                                      caption="Regex" />
                            )}
                          </div>
                        )}
                      </td>
                      <td className="py-2.5 pr-3">
                        {isEdit ? (
                          <Select defaultValue={r.severity} className="py-1"
                                  onChange={(e) => setDraft((d) => ({ ...d, severity: e.target.value as Severity }))}>
                            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
                          </Select>
                        ) : <SeverityChip severity={r.severity} />}
                      </td>
                      <td className="py-2.5 pr-3">
                        {isEdit ? (
                          <div className="space-y-1.5">
                            <ActionPicker value={acts} onChange={(s) => setDraft((d) => ({ ...d, action: joinActions(s) }))} />
                            {acts.has("kill") && sev === "critical" && killable(r.workload_type) && (
                              <Toggle checked={draft.auto_kill ?? r.auto_kill}
                                      onChange={() => setDraft((d) => ({ ...d, auto_kill: !(d.auto_kill ?? r.auto_kill) }))}
                                      caption="Auto-kill" />
                            )}
                          </div>
                        ) : (
                          <div className="flex flex-wrap items-center gap-1">
                            {[...parseActions(r.action)].map((t) => <Chip key={t}>{t}</Chip>)}
                            {r.auto_kill && <Chip color="#E5484D">auto-kill</Chip>}
                          </div>
                        )}
                      </td>
                      <td className="py-2.5 pr-3">
                        {isAdmin
                          ? <Toggle checked={r.enabled} onChange={() => toggle(r)} label={`Toggle ${r.name}`} />
                          : <Chip color={r.enabled ? "#3DD68C" : undefined}>{r.enabled ? "on" : "off"}</Chip>}
                      </td>
                      <td className="py-2.5 pr-4">
                        {!isAdmin ? null : (
                        <div className="flex items-center justify-end gap-1">
                          {isEdit ? (
                            <>
                              <button onClick={() => saveEdit(r)} className="rounded-md p-1.5 text-success hover:bg-hover" aria-label="Save"><Check size={15} /></button>
                              <button onClick={() => { setEditing(null); setDraft({}); }} className="rounded-md p-1.5 text-text-secondary hover:bg-hover" aria-label="Cancel"><X size={15} /></button>
                            </>
                          ) : (
                            <>
                              <button onClick={() => { setEditing(r.id); setDraft({}); }} className="rounded-md p-1.5 text-text-secondary hover:bg-hover hover:text-text-primary" aria-label="Edit"><Pencil size={14} /></button>
                              <button onClick={() => remove(r)} className="rounded-md p-1.5 text-text-secondary hover:bg-hover hover:text-critical" aria-label="Delete"><Trash2 size={14} /></button>
                            </>
                          )}
                        </div>
                        )}
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
  const [kind, setKind] = useState<RuleKind>("threshold");
  const [name, setName] = useState("");
  const [workloadType, setWorkloadType] = useState("query");
  const [metric, setMetric] = useState("elapsed_sec");
  const [threshold, setThreshold] = useState(1800);
  const [pattern, setPattern] = useState("");
  const [isRegex, setIsRegex] = useState(false);
  const [severity, setSeverity] = useState<Severity>("warning");
  const [actions, setActions] = useState<Set<string>>(new Set(["card", "email"]));
  const [autoKill, setAutoKill] = useState(false);
  const [saving, setSaving] = useState(false);

  // Pattern/semantic rules always scan query text → workload_type 'pattern_match'.
  const effWorkloadType = kind === "threshold" ? workloadType : "pattern_match";
  const showAutoKill = actions.has("kill") && severity === "critical" && killable(effWorkloadType);

  const submit = async () => {
    if (!name.trim()) return toast({ kind: "error", title: "Name is required" });
    if (kind !== "threshold" && !pattern.trim())
      return toast({ kind: "error", title: kind === "pattern" ? "Pattern is required" : "Intent is required" });
    setSaving(true);
    try {
      await api.createRule({
        name, workload_type: effWorkloadType, kind, metric,
        threshold: kind === "threshold" ? threshold : 0,
        pattern: kind === "threshold" ? null : pattern,
        pattern_is_regex: kind === "pattern" ? isRegex : false,
        severity, action: joinActions(actions),
        auto_kill: showAutoKill ? autoKill : false,
        enabled: true,
      });
      toast({ kind: "success", title: `Rule "${name}" created` });
      onCreated();
    } catch (e) {
      toast({ kind: "error", title: "Create failed", detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  const lbl = "flex flex-col gap-1.5";
  const lblText = "text-[12px] text-text-secondary";
  return (
    <Card className="mb-4">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-medium text-text-primary">New rule</h3>
        <button onClick={onClose} className="rounded-md p-1 text-text-secondary hover:bg-hover" aria-label="Close"><X size={16} /></button>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        <label className={`${lbl} lg:col-span-2`}>
          <span className={lblText}>Name</span>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Full table scan on prod" />
        </label>
        <label className={lbl}>
          <span className={lblText}>Kind</span>
          <Select value={kind} onChange={(e) => setKind(e.target.value as RuleKind)}>
            {KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
          </Select>
        </label>

        {kind === "threshold" ? (
          <>
            <label className={lbl}>
              <span className={lblText}>Workload type</span>
              <Select value={workloadType} onChange={(e) => setWorkloadType(e.target.value)}>
                {WORKLOAD_TYPES.map((w) => <option key={w} value={w}>{workloadLabel(w)}</option>)}
              </Select>
            </label>
            <label className={lbl}>
              <span className={lblText}>Metric</span>
              <Select value={metric} onChange={(e) => setMetric(e.target.value)}>
                {METRICS.map((m) => <option key={m} value={m}>{m}</option>)}
              </Select>
            </label>
            <label className={lbl}>
              <span className={lblText}>Threshold {metric === "est_cost_usd" ? "(USD)" : "(seconds)"}</span>
              <Input type="number" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
            </label>
          </>
        ) : (
          <label className={`${lbl} lg:col-span-3`}>
            <span className={lblText}>
              {kind === "pattern" ? "Text pattern (matched against query text)" : "Intent (LLM classifies each query against this)"}
            </span>
            <div className="flex items-center gap-2">
              <Input value={pattern} onChange={(e) => setPattern(e.target.value)}
                     placeholder={kind === "pattern" ? "e.g. SELECT * FROM prod.  (or a regex)" : "e.g. queries that scan raw PII columns"} />
              {kind === "pattern" && <Toggle checked={isRegex} onChange={() => setIsRegex((v) => !v)} caption="Regex" />}
            </div>
          </label>
        )}

        <label className={lbl}>
          <span className={lblText}>Severity</span>
          <Select value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
        </label>
        <div className={`${lbl} lg:col-span-2`}>
          <span className={lblText}>Actions</span>
          <ActionPicker value={actions} onChange={setActions} />
          {actions.has("kill") && !killable(effWorkloadType) && (
            <span className="text-[11px] text-warning">Kill doesn't apply to this workload type — kept for the audit trail.</span>
          )}
          {showAutoKill && (
            <div className="mt-1"><Toggle checked={autoKill} onChange={() => setAutoKill((v) => !v)} caption="Auto-kill on match (critical only)" /></div>
          )}
        </div>
      </div>
      <div className="mt-4 flex items-center justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" icon={Plus} loading={saving} onClick={submit}>Create rule</Button>
      </div>
    </Card>
  );
}
