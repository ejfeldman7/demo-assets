import React, { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

// Population/portfolio questions the guided case flow can't pre-answer — blast radius,
// shipment exposure, cross-case patterns — routed to the curated Genie space.
// Rendered as a floating button + right-side slide-in panel so it's reachable from any view
// (ask population questions while triaging a case), not a separate page.
const STARTERS = [
  "How many wafers ran on DEP-02 during weeks 25 through 34?",
  "Which lots have the highest field-return rate, and were they on DEP-02?",
  "How many modules from excursion wafers already shipped to customers?",
  "Average final-test margin by deposition tool?",
  "How many RMAs trace back to the DEP-02 excursion versus no-fault-found?",
];

export default function AskGenie() {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState([]); // { q, a }  (a === null while in flight)
  const [convo, setConvo] = useState(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);
  const panelRef = useRef(null);
  const logRef = useRef(null);

  // inert set via ref (cross-React-version safe): removes the off-screen panel from tab
  // order + a11y tree while closed.
  useEffect(() => { if (panelRef.current) panelRef.current.inert = !open; }, [open]);
  // focus the input when opened; close on Escape.
  useEffect(() => {
    if (open) requestAnimationFrame(() => inputRef.current?.focus());
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  // keep the newest turn in view
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [turns, busy]);

  const run = async (idx, qn) => {
    setBusy(true);
    setTurns((t) => t.map((x, i) => (i === idx ? { ...x, a: null } : x)));
    try {
      const res = await api.genieAsk(qn, convo);
      if (res.conversation_id) setConvo(res.conversation_id);
      setTurns((t) => t.map((x, i) => (i === idx ? { ...x, a: res } : x)));
    } catch (e) {
      // keep the question bubble + surface a retryable inline error — don't discard the ask
      setTurns((t) => t.map((x, i) => (i === idx ? { ...x, a: { error: e?.detail || "Genie query failed." } } : x)));
    } finally {
      setBusy(false);
    }
  };
  const ask = (question) => {
    const qn = (question ?? input).trim();
    if (!qn || busy) return;
    setInput("");
    const idx = turns.length;
    setTurns((t) => [...t, { q: qn, a: null }]);
    run(idx, qn);
  };

  return (
    <>
      {/* Floating action button */}
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Ask the Data"
        style={{
          position: "fixed", bottom: 24, right: 24, height: 46, padding: "0 18px 0 15px",
          borderRadius: 24, border: "none",
          background: "linear-gradient(135deg,#ff3621,#ff6b4a)", color: "#fff",
          fontSize: 14, fontWeight: 600, fontFamily: "var(--sans)", cursor: "pointer",
          display: open ? "none" : "flex", alignItems: "center", gap: 8,
          boxShadow: "0 6px 18px rgba(255,54,33,0.35)", zIndex: 60,
        }}
      >
        <span style={{ fontSize: 16 }}>✦</span> Ask the Data
      </button>

      {/* Right-side slide-in panel (non-modal: the case view stays usable behind it) */}
      <div
        ref={panelRef}
        role="dialog"
        aria-label="Ask the Data"
        style={{
          position: "fixed", top: 0, right: 0, height: "100vh", width: 440, maxWidth: "92vw",
          background: "var(--panel)", borderLeft: "1px solid var(--line)",
          boxShadow: "-12px 0 32px rgba(0,0,0,0.4)",
          display: "flex", flexDirection: "column",
          transform: open ? "translateX(0)" : "translateX(100%)",
          transition: "transform 0.28s cubic-bezier(0.4,0,0.2,1)", zIndex: 60,
        }}
      >
        {/* header */}
        <div style={{
          padding: "14px 16px", background: "var(--panel-2)", borderBottom: "1px solid var(--line)",
          display: "flex", alignItems: "flex-start", justifyContent: "space-between",
        }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 14, display: "flex", alignItems: "center", gap: 7, color: "var(--ink)" }}>
              <span style={{ color: "var(--sky)" }}>✦</span> Ask the Data
            </div>
            <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 3 }}>
              Population &amp; portfolio Q&amp;A · curated Genie · synthetic data
            </div>
          </div>
          <button
            onClick={() => setOpen(false)} aria-label="Close Ask the Data"
            style={{
              border: "1px solid var(--line-2)", background: "transparent", color: "var(--ink-2)",
              width: 28, height: 28, borderRadius: 7, cursor: "pointer", fontSize: 16, lineHeight: 1,
            }}
          >×</button>
        </div>

        {/* conversation / starters */}
        <div ref={logRef} role="log" aria-live="polite" aria-relevant="additions"
          aria-label="Conversation with Genie"
          style={{ flex: 1, overflowY: "auto", padding: 14, background: "var(--bg)" }}>
          {turns.length === 0 && (
            <div>
              <div style={{ color: "var(--ink-3)", fontSize: 12, marginBottom: 10, lineHeight: 1.6 }}>
                The case queue drills into one return; this steps back to the whole population.
                Try a question:
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
                {STARTERS.map((s) => (
                  <button key={s} className="chip" disabled={busy}
                    style={{ textAlign: "left", whiteSpace: "normal", lineHeight: 1.35 }}
                    onClick={() => ask(s)}>{s}</button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t, i) => (
            <div key={i} style={{ marginBottom: 14 }}>
              <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
                <div style={{
                  maxWidth: "88%", padding: "8px 12px", borderRadius: 12, fontSize: 13, lineHeight: 1.45,
                  background: "var(--sky)", color: "#fff",
                }}>{t.q}</div>
              </div>
              {t.a === null
                ? <div className="loading" style={{ fontSize: 12 }}>Genie is querying governed tables… (a cold first call can take ~10–30s)</div>
                : t.a.error
                  ? <div className="card" style={{ borderColor: "var(--crit)" }}>
                      <div style={{ color: "var(--crit)", fontSize: 12, marginBottom: 8 }}>{t.a.error}</div>
                      <button className="btn ghost" style={{ fontSize: 12 }} disabled={busy} onClick={() => run(i, t.q)}>Retry</button>
                    </div>
                  : <GenieAnswer a={t.a} />}
            </div>
          ))}
        </div>

        {/* input */}
        <div style={{ display: "flex", gap: 8, padding: 12, borderTop: "1px solid var(--line)", background: "var(--panel)", flexWrap: "wrap" }}>
          <input
            ref={inputRef} className="search" style={{ flex: 1, minWidth: 0 }}
            placeholder="Ask about wafers, lots, tools, RMAs, shipments…"
            value={input} disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") ask(); }}
          />
          <button className="btn primary" disabled={busy || !input.trim()} onClick={() => ask()}>Ask</button>
          {convo && (
            <button className="btn ghost" style={{ fontSize: 12 }} disabled={busy}
              onClick={() => { setConvo(null); setTurns([]); }}>New conversation</button>
          )}
        </div>
      </div>
    </>
  );
}

function GenieAnswer({ a }) {
  const [showSql, setShowSql] = useState(false);
  return (
    <div style={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 11, padding: 12 }}>
      {a.answer && <div style={{ marginBottom: 10, whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.5 }}>{a.answer}</div>}
      {a.columns?.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead><tr>{a.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {a.rows.map((r, i) => (
                <tr key={i}>{r.map((v, j) => (
                  <td key={j} className="mono" style={{ fontSize: 12 }}>{v == null ? "—" : String(v)}</td>
                ))}</tr>
              ))}
              {a.rows.length === 0 && <tr><td colSpan={a.columns.length} className="muted">No rows returned.</td></tr>}
            </tbody>
          </table>
          {a.truncated && <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>Showing first 200 rows.</div>}
        </div>
      )}
      {a.sql && (
        <div style={{ marginTop: 10 }}>
          <button className="btn ghost" style={{ fontSize: 12, padding: "5px 10px" }} onClick={() => setShowSql((s) => !s)}>
            {showSql ? "Hide" : "Show"} generated SQL
          </button>
          {showSql && (
            <pre className="mono" style={{
              background: "var(--panel-2)", border: "1px solid var(--line)", borderRadius: 8,
              padding: 12, marginTop: 8, overflowX: "auto", fontSize: 12, whiteSpace: "pre-wrap",
            }}>{a.sql}</pre>
          )}
        </div>
      )}
      {!a.answer && !a.sql && <div className="muted" style={{ fontSize: 12 }}>Genie returned no answer for that question.</div>}
    </div>
  );
}
