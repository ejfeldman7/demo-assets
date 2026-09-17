import React, { useEffect, useState } from "react";
import { api } from "./api.js";

const fmt = (v, d = 2) => (v == null ? "—" : Number(v).toFixed(d));

// donut showing at-risk fraction of the sibling population
function PopDonut({ rate, n, atRisk }) {
  const R = 46, C = 2 * Math.PI * R, pct = Math.max(0, Math.min(1, rate || 0));
  return (
    <div className="wafer">
      <svg width="120" height="120" viewBox="0 0 120 120">
        <circle cx="60" cy="60" r={R} fill="none" stroke="var(--line-2)" strokeWidth="12" />
        <circle cx="60" cy="60" r={R} fill="none" stroke="var(--lava)" strokeWidth="12"
          strokeDasharray={`${C * pct} ${C}`} strokeLinecap="round" transform="rotate(-90 60 60)" />
        <text x="60" y="56" textAnchor="middle" fill="var(--ink)" fontSize="22" fontWeight="700" fontFamily="var(--mono)">{(pct * 100).toFixed(0)}%</text>
        <text x="60" y="74" textAnchor="middle" fill="var(--ink-3)" fontSize="10" fontFamily="var(--mono)">AT RISK</text>
      </svg>
      <div>
        <div className="kv"><span className="lab">Sibling die on wafer</span><span className="val tnum">{n ?? "—"}</span></div>
        <div className="kv"><span className="lab">Predicted below guardband</span><span className="val tnum" style={{ color: "var(--lava)" }}>{atRisk ?? "—"}</span></div>
        <div className="muted" style={{ fontSize: 12, marginTop: 6, maxWidth: 230 }}>
          The returned unit is the trigger — the decision is about the whole wafer population it came from.
        </div>
      </div>
    </div>
  );
}

// predicted margin vs spec (0) and guardband (0.5)
function MarginBar({ pred, actual }) {
  const lo = -0.5, hi = 2.5, w = 300;
  const x = (v) => ((v - lo) / (hi - lo)) * w;
  return (
    <svg width={w} height="58" viewBox={`0 0 ${w} 58`}>
      <rect x={x(lo)} y="20" width={x(0) - x(lo)} height="14" fill="var(--crit-soft)" />
      <rect x={x(0)} y="20" width={x(0.5) - x(0)} height="14" fill="var(--warn-soft)" />
      <rect x={x(0.5)} y="20" width={x(hi) - x(0.5)} height="14" fill="var(--ok-soft)" />
      <line x1={x(0)} y1="14" x2={x(0)} y2="40" stroke="var(--crit)" strokeWidth="1.5" />
      <text x={x(0)} y="52" textAnchor="middle" fill="var(--crit)" fontSize="9" fontFamily="var(--mono)">SPEC</text>
      <line x1={x(0.5)} y1="14" x2={x(0.5)} y2="40" stroke="var(--warn)" strokeWidth="1.5" />
      <text x={x(0.5)} y="52" textAnchor="middle" fill="var(--warn)" fontSize="9" fontFamily="var(--mono)">GUARDBAND</text>
      {pred != null && <>
        <circle cx={x(pred)} cy="27" r="6" fill="var(--lava)" stroke="#fff" strokeWidth="1.5" />
        <text x={x(pred)} y="12" textAnchor="middle" fill="var(--ink)" fontSize="11" fontWeight="700" fontFamily="var(--mono)">{fmt(pred)}</text>
      </>}
      {actual != null && <circle cx={x(actual)} cy="27" r="4" fill="none" stroke="var(--ink-2)" strokeWidth="1.5" />}
    </svg>
  );
}

// interactive what-if — edit features, live prediction from the serving endpoint
const SLIDERS = [
  { k: "fc_meas", label: "Center frequency", unit: "MHz", min: 1954, max: 1965, step: 0.05 },
  { k: "il_meas", label: "Insertion loss (probe)", unit: "dB", min: 0.7, max: 1.4, step: 0.01 },
  { k: "radius", label: "Die radius (edge→1.0)", unit: "", min: 0, max: 1, step: 0.02 },
];
const TEMP_OPTIONS = [25, 85];   // test-insertion temperatures present in the data (binary)
function WhatIf({ caseId }) {
  const [base, setBase] = useState(null);
  const [ov, setOv] = useState({});
  const [pred, setPred] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.whatifBaseline(caseId).then((b) => {
      setBase(b);
      const init = {}; SLIDERS.forEach((s) => { init[s.k] = b.features[s.k]; });
      init.temp_c = TEMP_OPTIONS.includes(Number(b.features.temp_c)) ? Number(b.features.temp_c) : 25;
      setOv(init); setPred(b.baseline_pred); setErr(null);
    }).catch(() => setErr("baseline unavailable"));
  }, [caseId]);

  // debounced live prediction on edit
  useEffect(() => {
    if (!base || Object.keys(ov).length === 0) return;
    const t = setTimeout(async () => {
      setBusy(true); setErr(null);
      try { const r = await api.whatif(caseId, ov); setPred(r.pred_ft_margin_db); }
      catch (e) { setErr(e?.detail || "serving endpoint not ready"); }
      finally { setBusy(false); }
    }, 300);
    return () => clearTimeout(t);
  }, [ov]);

  if (!base) return <div className="muted" style={{ fontSize: 12 }}>{err || "Loading what-if…"}</div>;
  const status = pred == null ? null : pred <= 0 ? ["fail", "crit", "fails spec"] : pred <= 0.5 ? ["risk", "warn", "escape risk"] : ["pass", "ok", "passes guardband"];
  const delta = (pred != null && base.baseline_pred != null) ? pred - base.baseline_pred : null;
  const reset = () => {
    const init = {}; SLIDERS.forEach((s) => { init[s.k] = base.features[s.k]; });
    init.temp_c = TEMP_OPTIONS.includes(Number(base.features.temp_c)) ? Number(base.features.temp_c) : 25;
    setOv(init);
  };

  return (
    <div>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Adjust the front-end measurements and re-score the die live against <span className="mono">ft_margin_whatif@prod</span> on Model Serving.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 18px", marginBottom: 12 }}>
        {SLIDERS.map((s) => (
          <div key={s.k}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
              <span className="muted">{s.label}</span>
              <span className="mono tnum">{Number(ov[s.k] ?? 0).toFixed(s.step < 1 ? 2 : 0)} {s.unit}</span>
            </div>
            <input type="range" min={s.min} max={s.max} step={s.step} value={ov[s.k] ?? s.min}
              aria-label={s.label} style={{ width: "100%", accentColor: "var(--lava)" }}
              onChange={(e) => setOv({ ...ov, [s.k]: parseFloat(e.target.value) })} />
          </div>
        ))}
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
            <span className="muted">Test temperature</span>
            <span className="mono tnum">{ov.temp_c ?? 25} °C</span>
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 6 }} role="group" aria-label="Test temperature">
            {TEMP_OPTIONS.map((t) => (
              <button key={t} type="button" aria-pressed={Number(ov.temp_c) === t}
                className={"btn " + (Number(ov.temp_c) === t ? "primary" : "ghost")}
                style={{ flex: 1, padding: "7px 0" }}
                onClick={() => setOv({ ...ov, temp_c: t })}>{t} °C</button>
            ))}
          </div>
        </div>
      </div>
      <MarginBar pred={pred} actual={base.baseline_pred} />
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 6, flexWrap: "wrap" }}>
        <span style={{ fontSize: 22, fontWeight: 700 }} className="tnum">{pred == null ? "—" : pred.toFixed(2)}<span style={{ fontSize: 13, color: "var(--ink-2)" }}> dB</span></span>
        {status && <span className={"pill " + status[1]}>{status[2]}</span>}
        {delta != null && <span className="mono" style={{ fontSize: 12, color: delta < 0 ? "var(--crit)" : "var(--ok)" }}>{delta >= 0 ? "+" : ""}{delta.toFixed(2)} dB vs actual</span>}
        {busy && <span className="muted mono" style={{ fontSize: 11 }}>scoring…</span>}
        <button className="btn ghost" style={{ marginLeft: "auto", padding: "5px 11px" }} onClick={reset}>Reset to actual</button>
      </div>
      {err && <div className="gate" style={{ marginTop: 8 }}>{err}</div>}
      <div className="legend" style={{ marginTop: 8 }}>
        <span>◻ hollow marker = the die's actual measured prediction · ● lava marker = your what-if</span>
      </div>
    </div>
  );
}

// true wafer spatial map — die colored by predicted margin, case die ringed
function WaferMap({ caseId }) {
  const [wm, setWm] = useState(null);
  useEffect(() => { api.wafermap(caseId).then(setWm).catch(() => setWm({ dies: [] })); }, [caseId]);
  if (!wm) return <div className="muted" style={{ fontSize: 12 }}>Loading wafer map…</div>;
  const dies = wm.dies || [];
  const maxc = Math.max(20, ...dies.map((d) => Math.max(d.die_x, d.die_y))) + 1;
  const S = 240, cell = S / maxc, pad = cell * 0.12;
  const color = (m) => m == null ? "#333b48" : m <= 0.0 ? "var(--crit)" : m <= 0.5 ? "var(--warn)" : "#2f6d47";
  return (
    <div className="wafer">
      <svg width={S + 4} height={S + 4} viewBox={`-2 -2 ${S + 4} ${S + 4}`}>
        <circle cx={S / 2} cy={S / 2} r={S / 2} fill="none" stroke="var(--sky-deep)" strokeWidth="2" />
        {dies.map((d, i) => (
          <rect key={i} x={d.die_x * cell + pad} y={d.die_y * cell + pad} width={cell - 2 * pad} height={cell - 2 * pad}
            rx="1" fill={color(d.pred_ft_margin_db)} opacity={d.is_case_die ? 1 : 0.82}
            stroke={d.is_case_die ? "#fff" : "none"} strokeWidth={d.is_case_die ? 2 : 0}>
            <title>({d.die_x},{d.die_y}) {d.pred_ft_margin_db != null ? d.pred_ft_margin_db.toFixed(2) + " dB" : ""}{d.is_case_die ? " · returned unit" : ""}</title>
          </rect>
        ))}
      </svg>
      <div>
        <div className="legend" style={{ flexDirection: "column", gap: 6 }}>
          <span><span className="dot" style={{ background: "#2f6d47" }}></span> pass guardband (&gt;0.5 dB)</span>
          <span><span className="dot" style={{ background: "var(--warn)" }}></span> escape risk (0–0.5 dB)</span>
          <span><span className="dot" style={{ background: "var(--crit)" }}></span> fail spec (≤0 dB)</span>
          <span><span className="dot" style={{ background: "#fff" }}></span> returned unit (◻ ringed)</span>
        </div>
        <div className="muted" style={{ fontSize: 12, marginTop: 8, maxWidth: 230 }}>
          Predicted final-test margin per die across the wafer. The radial edge-worst pattern is the film-thickness excursion signature.
        </div>
      </div>
    </div>
  );
}

function CorrView({ corr }) {
  if (!corr) return null;
  const rows = [
    { p: "fc_meas", label: "Center freq" },
    { p: "il_meas", label: "Insertion loss" },
  ];
  const scale = (v) => Math.min(100, Math.abs(v || 0) * 100);
  return (
    <div className="corr">
      {rows.map(({ p, label }) => {
        const raw = corr[p]?.raw_pooled, strip = corr[p]?.within_week ?? corr[p]?.within_site;
        return (
          <div className="corrrow" key={p}>
            <div className="pn">{label}</div>
            <div className="corrbars">
              <div className="corrbar raw" style={{ width: scale(raw) + "%" }}><span className="lab">{fmt(raw)} raw</span></div>
              <div className="corrbar strip" style={{ width: scale(strip) + "%" }}><span className="lab">{fmt(strip)} conditioned</span></div>
            </div>
          </div>
        );
      })}
      <div className="legend">
        <span><span className="dot" style={{ background: "var(--sky-deep)" }}></span> raw (pooled)</span>
        <span><span className="dot" style={{ background: "var(--lava-dim)" }}></span> confounder-stripped</span>
      </div>
      <div className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>
        Insertion loss looks like a strong predictor raw, but collapses once you condition on time —
        it's a probe-card drift artifact. Center-frequency shift holds up. Don't trust the raw correlation.
      </div>
    </div>
  );
}

export default function CaseDetail({ caseId, role, roleInfo, onClose, onChange }) {
  const [d, setD] = useState(null);
  const [corr, setCorr] = useState(null);
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [narr, setNarr] = useState(null);
  const [narrBusy, setNarrBusy] = useState(false);

  const load = () => api.case(caseId).then((x) => { setD(x); }).catch(() => {});
  useEffect(() => { load(); api.correlations().then(setCorr).catch(() => {}); }, [caseId]);

  const act = async (fn) => {
    setBusy(true); setErr(null);
    try { await fn(); await load(); onChange && onChange(); setRationale(""); }
    catch (e) { setErr(e?.detail || "Action failed"); }
    finally { setBusy(false); }
  };

  if (!d) return (
    <><div className="scrim" onClick={onClose} /><aside className="drawer"><div className="loading">Loading case…</div></aside></>
  );

  const c = d.case, p = d.prediction, pop = d.population, rec = d.recommendation;
  const canContain = roleInfo?.can_contain;
  const isContainment = rec && rec.tier >= 1;
  const pending = rec && rec.status === "pending";

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer">
        <button className="close" onClick={onClose}>✕</button>
        <div className="dhead">
          <div className="mono muted" style={{ fontSize: 12 }}>{c.rma_id} · {c.customer}</div>
          <h1 style={{ marginTop: 4 }}>{c.case_id.replace("CASE-", "")}</h1>
          <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            <span className={"pill " + (c.state === "Closed" ? "ok" : c.state === "MES Hold Applied" ? "crit" : "warn")}>{c.state}</span>
            <span className="pill mut">{c.product}</span>
            {c.is_excursion_lineage
              ? <span className="pill crit">DEP-02 excursion lineage</span>
              : <span className="pill ok">nominal lineage</span>}
            {c.is_nff && <span className="pill mut">No fault found</span>}
          </div>
        </div>

        <div className="dbody">
          {/* genealogy */}
          <div className="card">
            <h3>◆ Genealogy — traced to wafer population</h3>
            <div className="gen">
              {d.genealogy.map((g, i) => (
                <div className="hop" key={i}>
                  <div className="rail">
                    <div className="node" style={{ background: g.level === "lot" ? "var(--sky-deep)" : "var(--sky)" }}></div>
                    {i < d.genealogy.length - 1 && <div className="line" />}
                  </div>
                  <div className="body">
                    <div className="lvl">{g.level} · trace {g.trace_grain} @ {(g.trace_confidence * 100).toFixed(0)}%</div>
                    <div className="key mono">{g.node_key}</div>
                    <div className="det">{g.detail}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="split">
            {/* prediction */}
            <div className="card">
              <h3>◆ Predicted back-end margin</h3>
              <MarginBar pred={p?.pred_ft_margin_db} actual={p?.actual_ft_margin_db} />
              <div className="kv"><span className="lab">Predicted FT margin</span><span className="val tnum" style={{ color: (p?.pred_ft_margin_db <= 0.5 ? "var(--crit)" : "var(--ink)") }}>{fmt(p?.pred_ft_margin_db)} dB</span></div>
              <div className="kv"><span className="lab">Escape risk</span><span className="val">{p?.pred_escape_risk ? <span className="pill crit">yes</span> : <span className="pill ok">no</span>}</span></div>
              <div className="kv"><span className="lab">Model</span><span className="val mono" style={{ fontSize: 11 }}>ft_margin_predictor@prod v{p?.model_version}</span></div>
            </div>
            {/* population */}
            <div className="card">
              <h3>◆ Wafer population at risk</h3>
              <PopDonut rate={pop?.at_risk_rate} n={pop?.n_population} atRisk={pop?.n_at_risk} />
            </div>
          </div>

          {/* wafer spatial map */}
          <div className="card">
            <h3>◆ Wafer spatial signature — predicted margin per die</h3>
            <WaferMap caseId={caseId} />
          </div>

          {/* what-if live scoring */}
          <div className="card">
            <h3>◆ What-if — live prediction on updated features</h3>
            <WhatIf caseId={caseId} />
          </div>

          {/* correlation */}
          <div className="card">
            <h3>◆ Likely driver — front-end / back-end correlation</h3>
            <CorrView corr={corr} />
          </div>

          {/* recommendation */}
          {rec ? (
            <div className="rec">
              <div className="top">
                <span className="mono muted" style={{ fontSize: 11 }}>TRACE-AGENT RECOMMENDS</span>
                <span className={"pill " + (rec.status === "approved" ? "ok" : rec.status === "rejected" ? "mut" : "warn")} style={{ marginLeft: "auto" }}>{rec.status}</span>
              </div>
              <div className="action">{rec.action}</div>
              <div className="why">{rec.rationale}</div>
              <div className="impact">↳ {rec.predicted_impact}</div>
              <div className="evgrid">
                <div className="ev"><div className="k">Confidence</div><div className="v">{(rec.confidence * 100).toFixed(0)}%</div></div>
                <div className="ev"><div className="k">Pred. margin</div><div className="v">{fmt(rec.evidence?.predicted_margin_db)} dB</div></div>
                <div className="ev"><div className="k">At-risk</div><div className="v">{rec.evidence?.n_at_risk ?? "—"}</div></div>
                <div className="ev"><div className="k">Top param</div><div className="v" style={{ fontSize: 13 }}>{rec.correlation_refs?.top_param}</div></div>
              </div>
              {rec.rejected_alternatives?.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div className="mono muted" style={{ fontSize: 10, textTransform: "uppercase", marginBottom: 4 }}>Alternatives considered</div>
                  {rec.rejected_alternatives.map((a, i) => (
                    <div className="rej" key={i}><b>{a.action}</b> — {a.why_not}</div>
                  ))}
                </div>
              )}

              {/* LLM narrative (model serving) */}
              <div style={{ marginTop: 12 }}>
                {narr ? (
                  <div style={{ background: "rgba(0,0,0,0.25)", border: "1px solid var(--line)", borderRadius: 8, padding: "11px 13px" }}>
                    <div className="mono muted" style={{ fontSize: 10, textTransform: "uppercase", marginBottom: 5 }}>
                      Agent explanation · {narr.model}
                    </div>
                    <div style={{ fontSize: 13, lineHeight: 1.55 }}>{narr.narrative}</div>
                  </div>
                ) : (
                  <button className="btn ghost" disabled={narrBusy}
                    onClick={async () => { setNarrBusy(true); try { setNarr(await api.narrative(caseId)); } catch {} finally { setNarrBusy(false); } }}>
                    {narrBusy ? "Asking the agent…" : "✦ Explain this recommendation with AI"}
                  </button>
                )}
              </div>

              {/* role-gated approval */}
              {pending && (
                <>
                  {isContainment && !canContain && (
                    <div className="gate">
                      As <b>{roleInfo?.label}</b> you can review and propose, but approving <b>{rec.action}</b> requires
                      Quality Engineer authority. Segregation of duties — switch role to approve.
                    </div>
                  )}
                  <textarea id="rationale" aria-label="Approval or rejection rationale" className="rationale"
                    placeholder="Approval / rejection rationale (recorded in the audit log)…"
                    value={rationale} onChange={(e) => setRationale(e.target.value)} />
                  <div className="btnrow">
                    <button className="btn primary" disabled={busy || (isContainment && !canContain)}
                      onClick={() => act(() => api.approve(caseId, { role, decision: "approve", rationale }))}>
                      Approve {isContainment ? "& issue MES hold" : ""}
                    </button>
                    <button className="btn" disabled={busy}
                      onClick={() => act(() => api.approve(caseId, { role, decision: "reject", rationale }))}>
                      Reject
                    </button>
                  </div>
                  {err && <div className="gate" style={{ color: "var(--crit)", borderColor: "#3a1512", background: "var(--crit-soft)" }}>{err}</div>}
                </>
              )}
              {rec.status === "approved" && c.state === "MES Hold Applied" && role === "MRB" && (
                <div className="btnrow">
                  <button className="btn primary" disabled={busy} onClick={() => act(() => api.dispose(caseId, { role, disposition: "scrap at-risk die", rationale }))}>Record MRB disposition (scrap)</button>
                  <button className="btn" disabled={busy} onClick={() => act(() => api.dispose(caseId, { role, disposition: "rework", rationale }))}>Rework</button>
                </div>
              )}
            </div>
          ) : (
            c.state !== "Closed" && (
              <div className="card" style={{ textAlign: "center" }}>
                <p className="muted" style={{ marginTop: 0 }}>No recommendation yet. Ask the agent to analyze the population and recommend an action.</p>
                <button className="btn primary" disabled={busy} onClick={() => act(() => api.recommend(caseId))}>Ask trace-agent for a recommendation</button>
              </div>
            )
          )}

          {/* audit timeline */}
          <div className="card">
            <h3>◆ Workflow &amp; audit trail</h3>
            <div className="tl">
              {d.events.map((e, i) => {
                const cls = e.event_type.includes("hold") || e.event_type.includes("containment") ? "hold"
                  : e.event_type.includes("closed") || e.event_type.includes("disposition") ? "close"
                  : e.event_type.includes("recommendation") ? "agent" : "";
                return (
                  <div className="ev" key={i}>
                    <div className="rail"><div className={"d " + cls}></div>{i < d.events.length - 1 && <div className="ln" />}</div>
                    <div className="txt">
                      <b>{e.detail}</b>
                      <div className="meta">{e.actor_role} · {new Date(e.event_ts).toLocaleString()}</div>
                      {e.mes_instruction && <div className="mes">⇒ {e.mes_instruction}</div>}
                    </div>
                  </div>
                );
              })}
              {d.approvals.map((a, i) => (
                <div className="ev" key={"a" + i}>
                  <div className="rail"><div className="d close"></div>{i < d.approvals.length - 1 && <div className="ln" />}</div>
                  <div className="txt">
                    <b>{a.decision} — {a.edited_action || rec?.action}</b>
                    <div className="meta">{a.approver_role} · {a.approver} · authority: {a.authority_level}</div>
                    {a.rationale && <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>"{a.rationale}"</div>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
