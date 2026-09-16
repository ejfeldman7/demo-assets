import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import CaseDetail from "./CaseDetail.jsx";
import Docs from "./Docs.jsx";
import Walkthrough from "./Walkthrough.jsx";
import AskGenie from "./AskGenie.jsx";

const STATES = ["All", "New", "Under FA Review", "Recommendation Pending", "MES Hold Applied", "MRB Disposition", "Closed"];

// RF-industry 4-point starburst, in a Databricks lava mark = the co-brand.
function Starburst() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 1 L13.6 10.4 L23 12 L13.6 13.6 L12 23 L10.4 13.6 L1 12 L10.4 10.4 Z" fill="#fff" />
    </svg>
  );
}

function sevPill(sev) {
  const m = { Critical: "crit", High: "warn", Medium: "info", Low: "mut" };
  return <span className={"pill " + (m[sev] || "mut")}>{sev}</span>;
}
function statePill(s, breached) {
  const m = {
    "New": "mut", "Under FA Review": "info", "Recommendation Pending": "warn",
    "MES Hold Applied": "crit", "MRB Disposition": "warn", "Closed": "ok",
  };
  return (
    <span className={"pill " + (m[s] || "mut")}>
      {breached ? "⚠ " : ""}{s}
    </span>
  );
}

export default function App() {
  const [role, setRole] = useState("FA Engineer");
  const [roles, setRoles] = useState([]);
  const [summary, setSummary] = useState(null);
  const [state, setState] = useState("Recommendation Pending");
  const [qtext, setQtext] = useState("");
  const [rows, setRows] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [view, setView] = useState("queue");
  const [resetting, setResetting] = useState(false);

  const reload = () => {
    api.summary().then(setSummary).catch(() => {});
    api.cases(state === "All" ? null : state, qtext).then(setRows).catch(() => setRows([]));
  };
  const doReset = async () => {
    if (!confirm("Reset the demo? This restores all cases, recommendations, approvals and holds to the original seeded state.")) return;
    setResetting(true);
    try { await api.reset(); setOpenId(null); reload(); }
    catch (e) { alert("Reset failed: " + (e?.detail || e)); }
    finally { setResetting(false); }
  };
  useEffect(() => { api.roles().then(setRoles).catch(() => {}); }, []);
  // debounce so typing in the search box doesn't fire a query per keystroke
  useEffect(() => {
    const t = setTimeout(reload, 250);
    return () => clearTimeout(t);
  }, [state, qtext]);

  const roleDesc = roles.find((r) => r.role === role);
  const stateCount = (s) => summary?.by_state?.find((x) => x.state === s)?.n;

  return (
    <div className="app">
      <nav className="nav">
        <div className="brand">
          <div className="mark"><Starburst /></div>
          <div>
            <b>Trace&nbsp;to&nbsp;Contain</b>
            <span>FAB → FIELD INTELLIGENCE</span>
          </div>
        </div>
        <div className={"navitem " + (view === "queue" ? "on" : "")} onClick={() => setView("queue")}>
          <span className="dot" style={{ background: "var(--lava)" }}></span> Case Queue
          <span className="c">{summary?.totals?.total ?? ""}</span>
        </div>
        <div className={"navitem " + (view === "docs" ? "on" : "")} onClick={() => setView("docs")}>
          <span className="dot" style={{ background: "var(--sky)" }}></span> Solution Reference
        </div>
        <div className={"navitem " + (view === "walkthrough" ? "on" : "")} onClick={() => setView("walkthrough")}>
          <span className="dot" style={{ background: "var(--ok)" }}></span> Demo Walkthrough
        </div>
        <div className="navitem" onClick={doReset} style={{ opacity: resetting ? 0.5 : 1 }}>
          <span className="dot" style={{ background: "var(--warn)" }}></span> {resetting ? "Resetting…" : "Reset Demo"}
        </div>
        <div className="role">
          <label htmlFor="role-select">Acting as</label>
          <select id="role-select" aria-label="Acting as role" value={role} onChange={(e) => setRole(e.target.value)}>
            {roles.map((r) => <option key={r.role} value={r.role}>{r.label}</option>)}
          </select>
          <div className="desc">
            {roleDesc?.can_contain
              ? "Authorized to approve containment / lot holds."
              : "Can review evidence and propose actions — cannot approve a hold."}
          </div>
          <div className="cobrand">
            <b>Databricks</b> intelligence &amp; action<br />
            <i>Manufacturing</i> data &amp; lineage
          </div>
        </div>
      </nav>

      <main className="main">
        {view === "docs" ? <Docs /> : view === "walkthrough" ? <Walkthrough /> : (
        <div className="wrap">
          <h1>Engineering Case Queue</h1>
          <p className="sub">RMA-triggered cases traced to their wafer population, scored, and routed for a governed containment decision.</p>

          {summary && (
            <div className="metrics">
              <div className="metric accent">
                <div className="k">Open cases</div>
                <div className="v tnum">{summary.totals.total - (stateCount("Closed") || 0)}<small> / {summary.totals.total}</small></div>
              </div>
              <div className="metric">
                <div className="k">Pending approval</div>
                <div className="v tnum" style={{ color: "var(--warn)" }}>{summary.pending_recs}</div>
              </div>
              <div className="metric">
                <div className="k">Units at risk (population)</div>
                <div className="v tnum" style={{ color: "var(--lava)" }}>{Number(summary.at_risk.units_at_risk).toLocaleString()}</div>
              </div>
              <div className="metric">
                <div className="k">SLA breached</div>
                <div className="v tnum" style={{ color: (summary.totals.sla_breached > 0 ? "var(--crit)" : "var(--ink)") }}>{summary.totals.sla_breached}</div>
              </div>
              <div className="metric">
                <div className="k">Excursion lineage</div>
                <div className="v tnum">{summary.totals.excursion}</div>
              </div>
            </div>
          )}

          <div className="toolbar">
            <div className="chips">
              {STATES.map((s) => (
                <button key={s} className={"chip " + (state === s ? "on" : "")} onClick={() => setState(s)}>
                  {s}{stateCount(s) != null && s !== "All" ? ` ${stateCount(s)}` : ""}
                </button>
              ))}
            </div>
            <input className="search" aria-label="Search cases" placeholder="Search RMA, module, wafer, customer…"
              value={qtext} onChange={(e) => setQtext(e.target.value)} />
          </div>

          {rows == null ? <div className="loading">Loading cases…</div> : (
            <table className="tbl">
              <thead><tr>
                <th>Case</th><th>Customer</th><th>Failure mode</th><th>Wafer</th>
                <th>Pred. margin</th><th>At-risk</th><th>Recommendation</th><th>State</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.case_id} onClick={() => setOpenId(r.case_id)}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{r.case_id.replace("CASE-", "")}</div>
                      <div className="muted mono" style={{ fontSize: 11 }}>{sevPill(r.severity)}</div>
                    </td>
                    <td>{r.customer}</td>
                    <td>{r.is_nff ? <span className="muted">No fault found</span> : r.failure_mode}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{r.wafer_id}</td>
                    <td className="tnum">{r.pred_ft_margin_db != null
                      ? <span style={{ color: r.pred_ft_margin_db <= 0.5 ? "var(--crit)" : "var(--ink)" }}>{r.pred_ft_margin_db.toFixed(2)} dB</span>
                      : "—"}</td>
                    <td className="tnum">{r.n_at_risk != null
                      ? <span>{r.n_at_risk} <span className="muted">({(r.at_risk_rate * 100).toFixed(0)}%)</span></span>
                      : "—"}</td>
                    <td>{r.rec_action
                      ? <span className={"pill " + (r.rec_tier >= 3 ? "crit" : r.rec_tier >= 1 ? "warn" : "ok")}>{r.rec_action}</span>
                      : <span className="muted">—</span>}</td>
                    <td>{statePill(r.state, r.sla_breached)}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={8} className="loading">No cases in this state.</td></tr>}
              </tbody>
            </table>
          )}
        </div>
        )}
      </main>

      {openId && (
        <CaseDetail
          caseId={openId} role={role} roleInfo={roleDesc}
          onClose={() => setOpenId(null)}
          onChange={() => { reload(); }}
        />
      )}

      <AskGenie />
    </div>
  );
}
