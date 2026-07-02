import type { ConfigResponse, GraphResponse, SchemaTreeResponse } from './types'

export async function fetchSchemaTree(): Promise<SchemaTreeResponse> {
  const res = await fetch('/api/schema-tree')
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
export async function fetchGraph(pairs?: string[]): Promise<GraphResponse> {
  const qs = pairs && pairs.length > 0 ? `?pairs=${encodeURIComponent(pairs.join(','))}` : ''
  const res = await fetch(`/api/graph${qs}`)
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `Failed to load graph: ${res.status} ${res.statusText}`)
  }
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
