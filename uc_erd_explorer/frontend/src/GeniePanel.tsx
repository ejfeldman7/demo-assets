import { useEffect, useRef, useState } from 'react'
import { askGenie } from './api'

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
}

/**
 * Genie chat — a floating action button that opens a right-side slide-in panel
 * (styled after the Databricks / Lakebase-console contextual panels).
 *
 * Wired to the real Genie Space via /api/genie/ask (server/routes/genie.py), which is
 * scoped to 3 narrow, pre-filtered metadata views (table_summary, column_inventory,
 * fk_edges) -- it can only ever answer structural/ERD questions about the configured
 * catalogs, never business data or out-of-scope catalogs.
 */
export function GeniePanel() {
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [conversationId, setConversationId] = useState<string | undefined>()
  const inputRef = useRef<HTMLInputElement>(null)

  // Move focus into the panel's input when it opens (keyboard users land where they can
  // act, not back at the FAB); focus after paint so the input is visible/focusable.
  useEffect(() => {
    if (open) requestAnimationFrame(() => inputRef.current?.focus())
  }, [open])

  async function send() {
    const text = input.trim()
    if (!text || busy) return
    setMessages((m) => [...m, { role: 'user', text }])
    setInput('')
    setBusy(true)
    try {
      const resp = await askGenie(text, conversationId)
      setConversationId(resp.conversation_id)
      setMessages((m) => [...m, { role: 'assistant', text: resp.answer }])
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', text: `Error: ${(e as Error).message}` },
      ])
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {/* Floating action button */}
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Ask Genie"
        style={{
          position: 'fixed',
          bottom: 24,
          right: 24,
          height: 46,
          padding: '0 18px 0 16px',
          borderRadius: 24,
          border: 'none',
          background: 'linear-gradient(135deg,#ff3621,#ff6b4a)',
          color: 'var(--on-accent)',
          fontSize: 14,
          fontWeight: 600,
          cursor: 'pointer',
          display: open ? 'none' : 'flex',
          alignItems: 'center',
          gap: 8,
          boxShadow: '0 6px 18px rgba(255,54,33,0.35)',
          zIndex: 30,
        }}
      >
        <span style={{ fontSize: 16 }}>✦</span> Ask Genie
      </button>

      {/* Right-side slide-in panel. `inert` when closed removes its input/Send/Close from
          the tab order and the accessibility tree while it's slid off-screen -- otherwise a
          keyboard/screen-reader user would tab into an invisible panel. role="dialog" +
          aria-label name it; it's a non-modal side panel (the canvas stays usable), so no
          aria-modal. */}
      <div
        role="dialog"
        aria-label="Ask Genie"
        inert={!open}
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          height: '100vh',
          width: 380,
          maxWidth: '90vw',
          background: 'var(--surface)',
          boxShadow: '-12px 0 32px rgba(16,24,40,0.16)',
          borderLeft: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          transform: open ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.28s cubic-bezier(0.4,0,0.2,1)',
          zIndex: 30,
        }}
      >
        <div
          style={{
            padding: '16px 18px',
            background: 'var(--db-navy)',
            color: 'var(--on-accent)',
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
          }}
        >
          <div>
            <div style={{ fontWeight: 700, fontSize: 15, display: 'flex', alignItems: 'center', gap: 7 }}>
              <span style={{ color: 'var(--db-red)' }}>✦</span> Ask Genie
            </div>
            <div style={{ fontSize: 11.5, fontWeight: 400, opacity: 0.75, marginTop: 3 }}>
              megacorp schema · structure &amp; relationships only
            </div>
          </div>
          <button
            onClick={() => setOpen(false)}
            aria-label="Close Genie panel"
            style={{
              border: 'none',
              background: 'rgba(255,255,255,0.12)',
              color: 'var(--on-accent)',
              width: 28,
              height: 28,
              borderRadius: 7,
              cursor: 'pointer',
              fontSize: 16,
              lineHeight: 1,
            }}
          >
            ×
          </button>
        </div>

        <div
          // Live region so a screen reader announces Genie's answers as they arrive
          // (polite = after the user's current utterance). additions-only so re-renders of
          // existing messages aren't re-read.
          role="log"
          aria-live="polite"
          aria-relevant="additions"
          aria-label="Conversation with Genie"
          style={{ flex: 1, overflowY: 'auto', padding: 14, background: 'var(--bg)' }}
        >
          {messages.length === 0 && (
            <div
              style={{
                color: 'var(--text-muted)',
                fontSize: 13,
                textAlign: 'center',
                marginTop: 48,
                lineHeight: 1.6,
              }}
            >
              Ask a question about the schema, e.g.
              <br />
              <em>“Which tables reference materials?”</em>
            </div>
          )}
          {messages.map((m, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
                marginBottom: 10,
              }}
            >
              <div
                style={{
                  maxWidth: '82%',
                  padding: '9px 13px',
                  borderRadius: 12,
                  fontSize: 13,
                  lineHeight: 1.5,
                  background: m.role === 'user' ? 'var(--db-blue)' : 'var(--surface)',
                  color: m.role === 'user' ? 'var(--on-accent)' : 'var(--text)',
                  border: m.role === 'user' ? 'none' : '1px solid var(--border)',
                  boxShadow: m.role === 'user' ? 'none' : 'var(--shadow-sm)',
                  whiteSpace: 'pre-wrap',
                }}
              >
                {m.text}
              </div>
            </div>
          ))}
          {busy && (
            <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>
              Genie is thinking…
            </div>
          )}
        </div>

        <div
          style={{
            display: 'flex',
            gap: 8,
            padding: 12,
            borderTop: '1px solid var(--border)',
            background: 'var(--surface)',
          }}
        >
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
            placeholder="Ask about the schema…"
            aria-label="Ask Genie about the schema"
            style={{
              flex: 1,
              border: '1px solid var(--border-strong)',
              borderRadius: 9,
              padding: '9px 12px',
              fontSize: 13,
              outline: 'none',
              color: 'var(--text)',
            }}
          />
          <button
            onClick={send}
            disabled={busy}
            style={{
              border: 'none',
              borderRadius: 9,
              background: 'var(--db-blue)',
              color: 'var(--on-accent)',
              padding: '0 16px',
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: 600,
            }}
          >
            Send
          </button>
        </div>
      </div>
    </>
  )
}
