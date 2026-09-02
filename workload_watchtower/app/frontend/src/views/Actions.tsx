import { useState, useRef, useEffect } from "react";
import { Mail, Send, ChevronDown, ChevronRight, CheckCircle2, XCircle, FileText, Users, Plus, X } from "lucide-react";
import { api, type ActionRow } from "../api";
import { useApi } from "../hooks";
import { useToast } from "../components/Toast";
import { Card, EmptyState, PageHeader, Spinner, Button, Chip, Input } from "../components/ui";
import { fmtAge } from "../lib/format";

function DistributionList() {
  const subs = useApi(() => api.subscribers());
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const list = subs.data ?? [];

  const add = async () => {
    const e = email.trim();
    if (!e) return;
    setBusy(true);
    try {
      await api.addSubscriber(e);
      setEmail("");
      subs.refresh();
      toast({ kind: "success", title: `Added ${e} to the list` });
    } catch (err) {
      toast({ kind: "error", title: "Could not add", detail: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number, e: string) => {
    try {
      await api.deleteSubscriber(id);
      subs.refresh();
      toast({ kind: "info", title: `Removed ${e}` });
    } catch (err) {
      toast({ kind: "error", title: "Could not remove", detail: err instanceof Error ? err.message : String(err) });
    }
  };

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center gap-2">
        <Users size={16} className="text-brand" />
        <h2 className="text-sm font-medium text-text-primary">Distribution list</h2>
        <span className="text-[11px] text-text-disabled">
          alerts are emailed to everyone here · critical findings auto-send
        </span>
      </div>
      <div className="mb-3 flex gap-2">
        <Input
          type="email"
          placeholder="name@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
          className="max-w-xs flex-1"
          aria-label="Add email to distribution list"
        />
        <Button variant="primary" icon={Plus} loading={busy} onClick={add}>
          Add
        </Button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {list.length === 0 && (
          <span className="text-[12px] text-text-secondary">No recipients yet — add one to receive alerts.</span>
        )}
        {list.map((s) => (
          <span
            key={s.id}
            className="inline-flex items-center gap-1.5 rounded-full border border-line bg-app px-2.5 py-1 text-[12px] text-text-primary"
          >
            {s.email}
            <button
              onClick={() => remove(s.id, s.email)}
              className="text-text-disabled transition-colors hover:text-danger"
              aria-label={`Remove ${s.email}`}
            >
              <X size={13} />
            </button>
          </span>
        ))}
      </div>
    </Card>
  );
}

const RESULT_META: Record<string, { color: string; icon: typeof Send; label: string }> = {
  drafted: { color: "#FFAB00", icon: FileText, label: "Drafted" },
  sending: { color: "#8AB4F8", icon: Send, label: "Sending…" },
  sent: { color: "#3DD68C", icon: CheckCircle2, label: "Sent" },
  failed: { color: "#E5484D", icon: XCircle, label: "Failed" },
};

function payloadString(p: unknown): string {
  if (p == null) return "";
  if (typeof p === "string") return p;
  try {
    return JSON.stringify(p, null, 2);
  } catch {
    return String(p);
  }
}

export function Actions() {
  const actions = useApi(() => api.actions(), { intervalMs: 20000 });
  const cfg = useApi(() => api.config());
  const toast = useToast();
  const [expanded, setExpanded] = useState<number | null>(null);
  const [sending, setSending] = useState<number | null>(null);
  const timers = useRef<number[]>([]);
  // Clear any pending post-send refresh timers if the view unmounts (avoids refresh-after-unmount).
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const list = actions.data ?? [];

  // A row is sendable when it's drafted/failed, or a 'sending' that's been stuck past the job's
  // worst-case runtime (job died before writing back) — reclaimable rather than stuck forever.
  const STALE_SENDING_MS = 5 * 60 * 1000;
  const canSend = (a: ActionRow) =>
    a.result === "drafted" ||
    a.result === "failed" ||
    (a.result === "sending" && a.updated_at != null && Date.now() - Date.parse(a.updated_at) > STALE_SENDING_MS);

  const send = async (a: ActionRow) => {
    setSending(a.id);
    try {
      // The endpoint queues the send on jobs compute and always returns result='sending'.
      const r = await api.sendAction(a.id);
      toast({ kind: "info", title: "Sending from jobs compute…", detail: r.detail });
      actions.refresh();
      // The send job runs ~30–40s on cold serverless; nudge a couple of refreshes so the row
      // flips sending→sent without waiting for the 20s auto-refresh. Tracked so they're cleared
      // on unmount.
      timers.current.push(
        window.setTimeout(() => actions.refresh(), 15000),
        window.setTimeout(() => actions.refresh(), 35000),
      );
    } catch (e) {
      toast({ kind: "error", title: "Could not send", detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setSending(null);
    }
  };

  return (
    <div>
      <PageHeader
        title="Actions"
        subtitle="Email automations from rule matches. Critical findings auto-send; others are drafted for approval."
        actions={
          cfg.data?.smtp_configured ? (
            <Chip color="#3DD68C">Email live · SMTP</Chip>
          ) : (
            <Chip color="#FFAB00">Email not configured</Chip>
          )
        }
      />

      <DistributionList />

      <Card padded={false}>
        {actions.error ? (
          <EmptyState icon={Mail} title="Could not load actions" hint={actions.error} />
        ) : actions.loading && !actions.data ? (
          <div className="flex items-center justify-center py-16">
            <Spinner />
          </div>
        ) : list.length === 0 ? (
          <EmptyState
            icon={Mail}
            title="No actions yet"
            hint="When a rule with a 'card_email' action matches a workload, its drafted email shows up here ready to send."
          />
        ) : (
          <div className="divide-y divide-line">
            {list.map((a) => {
              const meta = RESULT_META[a.result] ?? RESULT_META.drafted;
              const MetaIcon = meta.icon;
              const isOpen = expanded === a.id;
              const payload = payloadString(a.payload);
              return (
                <div key={a.id}>
                  <div
                    className="flex cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:bg-hover"
                    onClick={() => setExpanded(isOpen ? null : a.id)}
                  >
                    <span className="text-text-disabled">{isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</span>
                    <span
                      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px]"
                      style={{ backgroundColor: `${meta.color}1f`, color: meta.color }}
                    >
                      <MetaIcon size={16} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-[13px] text-text-primary">
                        <span className="font-medium">{a.action}</span>
                        <span className="text-text-disabled">→</span>
                        <span className="truncate text-text-secondary">{a.target ?? "—"}</span>
                      </div>
                      <div className="truncate text-[12px] text-text-secondary">
                        {a.object_name ?? "—"} {a.owner ? `· ${a.owner}` : ""}
                      </div>
                    </div>
                    <span className="text-[11px] text-text-disabled">{fmtAge(a.created_at)}</span>
                    <span
                      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium"
                      style={{ color: meta.color, backgroundColor: `${meta.color}1f` }}
                    >
                      {meta.label}
                    </span>
                    {canSend(a) && (
                      <Button
                        variant="primary"
                        icon={Send}
                        loading={sending === a.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          send(a);
                        }}
                        className="py-1.5"
                      >
                        {a.result === "drafted" ? "Send" : "Retry"}
                      </Button>
                    )}
                  </div>
                  {isOpen && (
                    <div className="bg-app px-4 py-3 pl-14">
                      <div className="mb-2 flex flex-wrap items-center gap-2 text-[12px]">
                        <Chip>finding #{a.finding_id}</Chip>
                        {a.target && <Chip>to: {a.target}</Chip>}
                      </div>
                      {payload ? (
                        <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-surface p-3 font-mono text-[12px] leading-relaxed text-text-primary">
                          {payload}
                        </pre>
                      ) : (
                        <div className="text-[12px] text-text-secondary">No payload recorded.</div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
