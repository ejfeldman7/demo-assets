import type { CatalogEnv, ConfigResponse, GraphResponse, SchemaTreeResponse } from './types'

export async function fetchSchemaTree(env: CatalogEnv = 'prod'): Promise<SchemaTreeResponse> {
  const res = await fetch(`/api/schema-tree?env=${env}`)
  if (!res.ok) {
    throw new Error(`Failed to load catalog/schema tree: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export async function fetchConfig(): Promise<ConfigResponse> {
  const res = await fetch('/api/config')
  if (!res.ok) {
    throw new Error(`Failed to load config: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

/** pairs = list of "catalog.schema" strings to narrow to. Omit/undefined for everything in scope. */
export async function fetchGraph(pairs?: string[], env: CatalogEnv = 'prod'): Promise<GraphResponse> {
  const params = new URLSearchParams({ env })
  if (pairs && pairs.length > 0) params.set('pairs', pairs.join(','))
  const res = await fetch(`/api/graph?${params.toString()}`)
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `Failed to load graph: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export interface SnapshotStatus {
  source_mode: 'snapshot' | 'information_schema'
  job_configured: boolean
  snapshot: { refreshed_at: string; catalogs: string } | null
}

export async function fetchSnapshotStatus(): Promise<SnapshotStatus> {
  const res = await fetch('/api/admin/snapshot-status')
  if (!res.ok) throw new Error(`Failed to load snapshot status: ${res.status}`)
  return res.json()
}

export async function triggerSnapshotRefresh(): Promise<{ run_id: number; already_running?: boolean }> {
  const res = await fetch('/api/admin/refresh-snapshot', { method: 'POST' })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `Failed to start refresh: ${res.status}`)
  }
  return res.json()
}

export interface RefreshRunStatus {
  run_id: number
  life_cycle_state: string | null
  result_state: string | null
  run_page_url: string | null
}

export async function fetchRefreshRunStatus(runId: number): Promise<RefreshRunStatus> {
  const res = await fetch(`/api/admin/refresh-snapshot/status?run_id=${runId}`)
  if (!res.ok) throw new Error(`Failed to read run status: ${res.status}`)
  return res.json()
}

export interface AuditFinding {
  severity: 'warn' | 'info'
  category: string
  title: string
  detail: string
  count: number
  objects: string[]
}

export interface AuditResponse {
  available: boolean
  reason?: string
  summary: Record<string, number>
  findings: AuditFinding[]
}

/** Deterministic schema-health audit over the current scope (same pairs/env as the graph). */
export async function fetchAudit(pairs?: string[], env: CatalogEnv = 'prod'): Promise<AuditResponse> {
  const params = new URLSearchParams({ env })
  if (pairs && pairs.length > 0) params.set('pairs', pairs.join(','))
  const res = await fetch(`/api/audit?${params.toString()}`)
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `Audit failed: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export interface DbxmetagenStatus {
  present: boolean
  location: string | null
  tables_found: string[]
  repo_url: string
}

/** Whether dbxmetagen's output is present for the in-scope catalogs (best-effort, read-only). */
export async function fetchDbxmetagen(env: CatalogEnv = 'prod'): Promise<DbxmetagenStatus> {
  const res = await fetch(`/api/integrations/dbxmetagen?env=${env}`)
  if (!res.ok) throw new Error(`dbxmetagen check failed: ${res.status}`)
  return res.json()
}

export interface PredictedEdge {
  id: string
  source: string
  target: string
  fk_columns: string[]
  pk_columns: string[]
  predicted: true
  confidence: number | null
  is_fk: boolean
  reasoning: string | null
}

export interface DbxmetagenFkResponse {
  present: boolean
  location: string | null
  edges: PredictedEdge[]
}

/** dbxmetagen's confidence-scored FK predictions as overlay edges (empty if absent). */
export async function fetchDbxmetagenFkPredictions(env: CatalogEnv = 'prod'): Promise<DbxmetagenFkResponse> {
  const res = await fetch(`/api/integrations/dbxmetagen/fk-predictions?env=${env}`)
  if (!res.ok) throw new Error(`dbxmetagen FK predictions failed: ${res.status}`)
  return res.json()
}

export interface GenieResponse {
  conversation_id: string
  message_id: string
  status: string
  answer: string
}

export async function askGenie(
  message: string,
  conversationId?: string,
): Promise<GenieResponse> {
  const res = await fetch('/api/genie/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  })
  if (!res.ok) throw new Error(`Genie request failed: ${res.status}`)
  return res.json()
}
