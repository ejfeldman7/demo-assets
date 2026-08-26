import { useState } from "react";
import { Kanban, User, Clock, DollarSign, GripVertical, Sparkles } from "lucide-react";
import { api, type Card as CardT, type CardStatus, type Member, type Priority } from "../api";
import { useApi } from "../hooks";
import { useToast } from "../components/Toast";
import { CopilotModal } from "../components/CopilotModal";
import { Card, EmptyState, PageHeader, SeverityChip, Spinner, Select, Button } from "../components/ui";
import { fmtCost, fmtElapsed, truncate, workloadIcon, workloadLabel } from "../lib/format";
import { RefreshCw } from "lucide-react";

const COLUMNS: { key: CardStatus; label: string; accent: string }[] = [
  { key: "new", label: "New", accent: "#4C8DFF" },
  { key: "investigating", label: "Investigating", accent: "#FFAB00" },
  { key: "assigned", label: "Assigned", accent: "#FF5F46" },
  { key: "resolved", label: "Resolved", accent: "#3DD68C" },
];

const PRIORITIES: Priority[] = ["low", "medium", "high"];
const PRIORITY_COLOR: Record<Priority, string> = { low: "#6B7482", medium: "#FFAB00", high: "#E5484D" };

function TriageCard({
  card,
  members,
  dragging,
  onDragStart,
  onDragEnd,
  onPatch,
  onExplain,
}: {
  card: CardT;
  members: Member[];
  dragging: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onPatch: (body: Partial<Pick<CardT, "status" | "assignee_id" | "priority" | "notes">>) => void;
  onExplain: () => void;
}) {
  const Icon = workloadIcon(card.workload_type);
  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={`group rounded-xl border border-line bg-app p-3.5 shadow-card transition-all hover:border-brand/40 ${
        dragging ? "opacity-40" : ""
      }`}
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Icon size={15} className="shrink-0 text-text-secondary" />
          <span className="truncate text-[11px] uppercase tracking-wide text-text-disabled">
            {workloadLabel(card.workload_type)} · {card.external_id}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {card.alert_priority > 0 && (
            <span
              className="rounded px-1.5 py-0.5 text-[10px] font-semibold tabular-nums"
              style={{ background: "#1B222C", color: "#97A0AF" }}
              title="Triage priority score (0–100)"
            >
              P{card.alert_priority}
            </span>
          )}
          <SeverityChip severity={card.severity} />
          <GripVertical size={14} className="cursor-grab text-text-disabled opacity-0 transition-opacity group-hover:opacity-100" />
        </div>
      </div>

      <div className="mb-3 font-mono text-[12.5px] leading-snug text-text-primary" title={card.object_name ?? undefined}>
        {truncate(card.object_name, 90)}
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-text-secondary">
        <span className="inline-flex items-center gap-1">
          <User size={12} /> {card.owner ?? "unknown"}
        </span>
        <span className="inline-flex items-center gap-1">
          <Clock size={12} /> {fmtElapsed(card.elapsed_sec)}
        </span>
        <span className="inline-flex items-center gap-1">
          <DollarSign size={12} /> {fmtCost(card.est_cost_usd)}
        </span>
      </div>

      {card.violation_reason && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {card.violation_reason.split("|").map((v) => {
            const override = v === "STATEMENT_TIMEOUT_OVERRIDE";
            return (
              <span
                key={v}
                title={
                  override
                    ? "Session-level SET STATEMENT_TIMEOUT — a user overrode the workspace/warehouse guardrail (session scope wins)"
                    : undefined
                }
                className="rounded-md border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
                style={{
                  borderColor: override ? "#FFAB0055" : "#232B37",
                  color: override ? "#FFAB00" : "#97A0AF",
                }}
              >
                {v.replace(/_/g, " ")}
              </span>
            );
          })}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5">
        <Select
          value={card.assignee_id ?? ""}
          onChange={(e) => onPatch({ assignee_id: e.target.value ? Number(e.target.value) : null } as never)}
          className="max-w-[130px] flex-1 py-1 text-[12px]"
          aria-label="Assignee"
        >
          <option value="">Unassigned</option>
          {members.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </Select>
        <Select
          value={card.priority}
          onChange={(e) => onPatch({ priority: e.target.value as Priority })}
          className="py-1 text-[12px]"
          aria-label="Priority"
          style={{ color: PRIORITY_COLOR[card.priority] }}
        >
          {PRIORITIES.map((p) => (
            <option key={p} value={p} style={{ color: "var(--text-primary)" }}>
              {p}
            </option>
          ))}
        </Select>
        <Select
          value={card.status}
          onChange={(e) => onPatch({ status: e.target.value as CardStatus })}
          className="py-1 text-[12px]"
          aria-label="Status"
        >
          {COLUMNS.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </Select>
      </div>

      <button
        onClick={onExplain}
        className="mt-2.5 flex w-full items-center justify-center gap-1.5 rounded-[10px] border border-line bg-surface py-1.5 text-[12px] font-medium text-text-secondary transition-colors hover:border-brand/40 hover:text-text-primary"
      >
        <Sparkles size={13} className="text-lava-warm" />
        Explain with Copilot
      </button>
    </div>
  );
}

export function TriageBoard() {
  const cards = useApi(() => api.cards(), { intervalMs: 15000 });
  const members = useApi(() => api.members());
  const toast = useToast();
  const [dragId, setDragId] = useState<number | null>(null);
  const [overCol, setOverCol] = useState<CardStatus | null>(null);
  const [explain, setExplain] = useState<{ findingId: number; context: string } | null>(null);

  const list = cards.data ?? [];
  const mem = members.data ?? [];

  const patch = async (id: number, body: Partial<Pick<CardT, "status" | "assignee_id" | "priority" | "notes">>) => {
    // Optimistic: apply the change locally right away (the card moves columns / the control updates
    // instantly) so the board feels native, then send the PATCH and reconcile — rolling back to
    // server truth on failure.
    cards.mutate((prev) => (prev ? prev.map((c) => (c.id === id ? { ...c, ...body } : c)) : prev));
    try {
      await api.patchCard(id, body);
      cards.refreshQuiet(); // reconcile with server truth (no spinner; UI already correct)
      if (body.status) toast({ kind: "success", title: `Card moved to ${body.status}` });
      else if ("assignee_id" in body)
        toast({ kind: "success", title: body.assignee_id ? "Card assigned" : "Card unassigned" });
      else if (body.priority) toast({ kind: "info", title: `Priority set to ${body.priority}` });
    } catch (e) {
      toast({ kind: "error", title: "Update failed", detail: e instanceof Error ? e.message : String(e) });
      cards.refresh(); // roll back the optimistic change to server state
    }
  };

  const drop = (status: CardStatus) => {
    setOverCol(null);
    if (dragId == null) return;
    const card = list.find((c) => c.id === dragId);
    setDragId(null);
    if (card && card.status !== status) patch(card.id, { status });
  };

  return (
    <div>
      <PageHeader
        title="Triage Board"
        subtitle="Drag a card between columns to change status, or use the per-card controls."
        actions={
          <Button icon={RefreshCw} onClick={() => cards.refresh()}>
            Refresh
          </Button>
        }
      />

      {cards.error ? (
        <Card>
          <EmptyState icon={Kanban} title="Could not load cards" hint={cards.error} />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {COLUMNS.map((col) => {
            const colCards = list.filter((c) => c.status === col.key);
            const isOver = overCol === col.key;
            return (
              <div
                key={col.key}
                onDragOver={(e) => {
                  e.preventDefault();
                  if (overCol !== col.key) setOverCol(col.key);
                }}
                onDragLeave={(e) => {
                  if (!e.currentTarget.contains(e.relatedTarget as Node)) setOverCol((c) => (c === col.key ? null : c));
                }}
                onDrop={() => drop(col.key)}
                className={`flex min-h-[220px] flex-col rounded-xl border bg-surface transition-colors ${
                  isOver ? "border-brand" : "border-line"
                }`}
              >
                <div className="flex items-center justify-between border-b border-line px-4 py-3">
                  <div className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full" style={{ backgroundColor: col.accent }} />
                    <span className="text-[13px] font-medium text-text-primary">{col.label}</span>
                  </div>
                  <span className="rounded-full border border-line px-2 py-0.5 text-[11px] tabular-nums text-text-secondary">
                    {colCards.length}
                  </span>
                </div>
                <div className="flex flex-1 flex-col gap-2.5 p-3">
                  {colCards.map((c) => (
                    <TriageCard
                      key={c.id}
                      card={c}
                      members={mem}
                      dragging={dragId === c.id}
                      onDragStart={() => setDragId(c.id)}
                      onDragEnd={() => setDragId(null)}
                      onPatch={(body) => patch(c.id, body)}
                      onExplain={() =>
                        setExplain({ findingId: c.finding_id, context: c.object_name ?? c.external_id })
                      }
                    />
                  ))}
                  {colCards.length === 0 && (
                    <div className="flex flex-1 items-center justify-center py-8 text-center text-[12px] text-text-disabled">
                      {cards.loading && !cards.data ? <Spinner size={16} /> : isOver ? "Drop here" : "Nothing here"}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!cards.loading && list.length === 0 && !cards.error && (
        <Card className="mt-4">
          <EmptyState
            icon={Kanban}
            title="No triage cards yet"
            hint="Cards are created when the poller matches a workload against an enabled rule. Use the demo controls to generate a slow query, then run a poll."
          />
        </Card>
      )}

      <CopilotModal
        findingId={explain?.findingId ?? null}
        context={explain?.context}
        onClose={() => setExplain(null)}
      />
    </div>
  );
}
