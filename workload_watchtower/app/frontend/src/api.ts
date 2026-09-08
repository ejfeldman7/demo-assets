// api.ts — typed client for the Watchtower FastAPI backend (all under /api).

export type Severity = "critical" | "warning" | "info";
export type CardStatus = "new" | "investigating" | "assigned" | "resolved";
export type Priority = "low" | "medium" | "high";
export type WorkloadType = "query" | "job_run" | "pipeline" | string;

export interface Summary {
  open_by_severity: Partial<Record<Severity, number>>;
  open_by_type: Partial<Record<string, number>>;
  cards_by_status: Partial<Record<CardStatus, number>>;
  open_est_cost_usd: number;
  last_poll: {
    finished_at: string | null;
    workloads_seen: number | null;
    findings_new: number | null;
    duration_ms: number | null;
    seen_by_type: Record<string, number> | null;
  };
}

export interface Finding {
  id: number;
  workload_type: WorkloadType;
  external_id: string;
  owner: string | null;
  object_name: string | null;
  compute_ref: string | null;
  started_at: string | null;
  elapsed_sec: number | null;
  est_cost_usd: number | null;
  severity: Severity;
  status: string;
  first_seen: string | null;
  last_seen: string | null;
  query_text: string | null;
  rule_name: string | null;
}

export interface Card {
  id: number;
  finding_id: number;
  status: CardStatus;
  priority: Priority;
  notes: string | null;
  assignee_id: number | null;
  assignee_name: string | null;
  workload_type: WorkloadType;
  object_name: string | null;
  owner: string | null;
  elapsed_sec: number | null;
  est_cost_usd: number | null;
  severity: Severity;
  external_id: string;
  query_text: string | null;
  health_status: string | null;
  alert_priority: number;
  violation_reason: string | null;
}

export type RuleKind = "threshold" | "pattern" | "semantic";

export interface Rule {
  id: number;
  name: string;
  workload_type: WorkloadType;
  kind: RuleKind;
  metric: "elapsed_sec" | "est_cost_usd" | string;
  threshold: number;
  pattern: string | null;
  pattern_is_regex: boolean;
  severity: Severity;
  action: string;
  auto_kill: boolean;
  enabled: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface BudgetConfig {
  user_budget_usd: number;
  window_hours: number;
  scan_every_min: number;
  workspace_ids: string;
  enabled: boolean;
  notify_users: boolean;
  last_scan_at: string | null;
}

export interface UserCost {
  user_identity: string;
  query_count: number;
  total_execution_min: number;
  pct_of_total_execution: number;
  estimated_dbus: number;
  estimated_list_cost_usd: number;
  over_budget: boolean;
}

export interface BudgetStatus {
  budget_usd: number;
  window_hours: number;
  workspace_ids: string;
  users: UserCost[];
}

export interface BudgetAlert {
  id: number;
  user_identity: string;
  est_cost_usd: number;
  budget_usd: number;
  window_hours: number;
  recipients: string | null;
  result: string;
  error: string | null;
  alerted_at: string | null;
}

export interface Member {
  id: number;
  name: string;
  email: string;
  role: string;
}

export interface Subscriber {
  id: number;
  email: string;
  active: boolean;
  created_at: string | null;
}

export interface ActionRow {
  id: number;
  finding_id: number;
  action: string;
  target: string | null;
  result: "drafted" | "sending" | "sent" | "failed";
  created_at: string | null;
  updated_at: string | null;
  object_name: string | null;
  owner: string | null;
  payload: unknown;
}

export interface PollRun {
  id: number;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  workloads_seen: number | null;
  findings_new: number | null;
  findings_upd: number | null;
  errors: string | null;
}

export interface Trends {
  by_type: { workload_type: string; workloads: number; est_cost_usd: number; max_elapsed_min: number }[];
  timeline: { hour: string; workloads: number; est_cost_usd: number }[];
}

export interface JobRunRef {
  job_id: number;
  run_id: number;
}

export interface Explanation {
  finding_id: number;
  model: string;
  explanation: string;
}

export interface AskAnswer {
  question: string;
  model: string;
  answer: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  config: () =>
    req<{
      smtp_configured: boolean;
      dashboard_url: string | null;
      dashboard_embed_url: string | null;
      workspace: string | null;
      is_admin: boolean;
    }>("/config"),
  summary: () => req<Summary>("/summary"),
  findings: (status?: string, limit = 200) =>
    req<Finding[]>(`/findings?${new URLSearchParams({ ...(status ? { status } : {}), limit: String(limit) })}`),
  cards: () => req<Card[]>("/cards"),
  patchCard: (id: number, body: Partial<Pick<Card, "status" | "assignee_id" | "priority" | "notes">>) =>
    req<{ ok: boolean }>(`/cards/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  rules: () => req<Rule[]>("/rules"),
  createRule: (body: {
    name: string;
    workload_type: string;
    kind: string;
    metric: string;
    threshold: number;
    pattern: string | null;
    pattern_is_regex: boolean;
    severity: string;
    action: string;
    auto_kill: boolean;
    enabled: boolean;
  }) => req<{ id: number }>("/rules", { method: "POST", body: JSON.stringify(body) }),
  patchRule: (
    id: number,
    body: Partial<Pick<Rule, "threshold" | "severity" | "action" | "enabled" | "pattern" | "pattern_is_regex" | "auto_kill">>,
  ) => req<{ ok: boolean }>(`/rules/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteRule: (id: number) => req<{ ok: boolean }>(`/rules/${id}`, { method: "DELETE" }),
  killFinding: (id: number) =>
    req<{ ok: boolean; detail: string }>(`/findings/${id}/kill`, { method: "POST" }),
  budgetConfig: () => req<BudgetConfig>("/budget/config"),
  patchBudgetConfig: (body: Partial<Omit<BudgetConfig, "last_scan_at">>) =>
    req<{ ok: boolean }>("/budget/config", { method: "PATCH", body: JSON.stringify(body) }),
  budgetStatus: () => req<BudgetStatus>("/budget/status"),
  budgetAlerts: () => req<BudgetAlert[]>("/budget/alerts"),
  members: () => req<Member[]>("/members"),
  subscribers: () => req<Subscriber[]>("/subscribers"),
  addSubscriber: (email: string) =>
    req<{ id: number }>("/subscribers", { method: "POST", body: JSON.stringify({ email }) }),
  deleteSubscriber: (id: number) => req<{ ok: boolean }>(`/subscribers/${id}`, { method: "DELETE" }),
  actions: () => req<ActionRow[]>("/actions"),
  sendAction: (id: number) =>
    req<{ ok: boolean; result: string; detail: string }>(`/actions/${id}/send`, { method: "POST" }),
  pollRuns: () => req<PollRun[]>("/poll-runs"),
  trends: (hours = 24) => req<Trends>(`/trends?hours=${hours}`),
  opsPoll: () => req<JobRunRef>("/ops/poll", { method: "POST" }),
  // agentic (Foundation Model) features
  explainFinding: (id: number) => req<Explanation>(`/findings/${id}/explain`, { method: "POST" }),
  ask: (question: string) => req<AskAnswer>("/ask", { method: "POST", body: JSON.stringify({ question }) }),
};
