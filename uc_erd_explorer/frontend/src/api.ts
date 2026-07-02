import type { GraphResponse, SchemaFilter } from './types'

export async function fetchGraph(schemas: SchemaFilter): Promise<GraphResponse> {
  const qs = schemas === 'both' ? '' : `?schemas=${schemas}`
  const res = await fetch(`/api/graph${qs}`)
  if (!res.ok) {
    throw new Error(`Failed to load graph: ${res.status} ${res.statusText}`)
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
