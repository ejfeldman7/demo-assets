import React from "react";
import Architecture from "./Architecture.jsx";

function Card({ title, children }) {
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export default function Docs() {
  return (
    <div className="wrap">
      <h1>Solution Reference</h1>
      <p className="sub">What this demo is, how it's built on Databricks, and the story to tell while driving it.</p>

      <Card title="◆ The one-line story">
        <p style={{ fontSize: 15, lineHeight: 1.6, margin: 0 }}>
          When a failed RF module comes back from a customer, <b>Trace to Contain</b> uses the RMA as the trigger,
          pivots to the whole <b style={{ color: "var(--sky)" }}>wafer population</b> it came from, explains the likely
          driver with governed front-end→back-end correlation, predicts the at-risk population, and routes a
          <b style={{ color: "var(--lava)" }}> tiered containment recommendation</b> to the engineer who holds that
          authority — taking RMA-to-contained-disposition from <b>weeks to hours</b> and catching the population
          <i> before</i> it ships.
        </p>
      </Card>

      <Card title="◆ Solution architecture">
        <Architecture />
      </Card>

      <div className="split">
        <Card title="◆ Databricks products used">
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7, fontSize: 13 }}>
            <li><b>Unity Catalog</b> — governance, lineage &amp; audit end-to-end</li>
            <li><b>Delta Lake</b> — analytical system of record (13.5M-row FAB data)</li>
            <li><b>Lakeflow / Auto Loader</b> — STDF-like ingestion</li>
            <li><b>Feature Engineering</b> — one <code>die_features</code> definition, offline + online</li>
            <li><b>MLflow + Optuna</b> — HPO, tracking, <code>ft_margin_predictor@prod</code></li>
            <li><b>Lakebase</b> — one project holding the online feature vector and the operational store (<code>trace_ops</code>)</li>
            <li><b>Model Serving (FM API, Claude)</b> — the agent narrative</li>
            <li><b>Genie</b> — curated “Ask the Data” space over the lakehouse for population/portfolio NL→SQL</li>
            <li><b>Databricks Apps</b> — this FastAPI + React console</li>
          </ul>
        </Card>
        <Card title="◆ The operational loop">
          <ol style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7, fontSize: 13 }}>
            <li>RMA lands → case auto-created (pushed from QMS)</li>
            <li>Genealogy traces module→die→wafer→lot (with trace grain/confidence)</li>
            <li>Pivot to the sibling wafer population</li>
            <li>Correlation explains the driver (confounders stripped)</li>
            <li>Model predicts the at-risk population margin</li>
            <li>Agent recommends tiered containment (pending)</li>
            <li>Role-gated approval → MES hold instruction</li>
            <li>Disposition written back → close the loop</li>
          </ol>
        </Card>
      </div>

      <Card title="◆ Ask the Data — population &amp; portfolio Q&amp;A">
        <p style={{ fontSize: 13, lineHeight: 1.6, margin: 0 }}>
          The case queue drills into <i>one</i> return; <b style={{ color: "var(--sky)" }}>Ask the Data</b> steps back to the
          whole population. A curated Genie space over the seven <code>trace_to_contain.lakehouse</code> tables answers the
          blast-radius, shipment-exposure, and cross-case questions the guided flow can't pre-answer — returning the answer, the
          generated SQL, and the rows. It runs <b>on-behalf-of the signed-in user</b> (Unity Catalog / RLS apply) with an app
          service-principal fallback, and is built reproducibly by <code>src/ops/02_create_genie_space.py</code>, curated to the
          genie-workbench IQ-Scanner checklist (described tables/columns, instructions, joins, example SQL, and a benchmark set)
          with the simulator's ground-truth columns hidden.
        </p>
      </Card>

      <Card title="◆ The intelligence — why it's credible, not a toy">
        <div className="kv"><span className="lab">Confounder-aware correlation</span><span className="val">Center-freq holds after conditioning on ATE site; insertion-loss collapses (probe-card drift artifact)</span></div>
        <div className="kv"><span className="lab">One feature definition</span><span className="val">Same <code>die_features</code> for point-in-time training and online (Lakebase) scoring — no train/serve skew</span></div>
        <div className="kv"><span className="lab">Population-level decision</span><span className="val">Predicted margin separates excursion wafers (0.74 dB) from healthy (1.72 dB); top-20 at-risk wafers = 100% excursion</span></div>
        <div className="kv"><span className="lab">Human-in-the-loop</span><span className="val">Agent recommends; a Quality Engineer approves a hold; MRB disposes — segregation of duties, full audit</span></div>
      </Card>

      <div className="split">
        <Card title="◆ Governance &amp; guardrails">
          <p style={{ fontSize: 13, lineHeight: 1.6, margin: 0 }}>
            The agent produces <b>analysis and a pending recommendation only</b> — it never mutates operational data.
            A human with the right authority approves; the hold is issued as an <b>instruction to MES</b>, not a
            status flip; every decision (approver, role, authority-at-time, rejected alternatives, model version)
            is written to an append-only audit trail for IATF&nbsp;16949 / 8D / CAPA.
          </p>
        </Card>
        <Card title="◆ Demo script (2 minutes)">
          <ol style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7, fontSize: 13 }}>
            <li>Open the <b>Recommendation Pending</b> queue</li>
            <li>Pick an <b>Automotive T1 / excursion</b> case</li>
            <li>Walk genealogy → prediction → wafer map → correlation</li>
            <li>Show the agent's <b>Full lot hold</b> + AI explanation</li>
            <li>As <b>FA Engineer</b>, try to approve → blocked</li>
            <li>Switch to <b>Quality Engineer</b> → approve → MES hold</li>
            <li>Show the audit trail; <b>Reset Demo</b> to rerun</li>
          </ol>
        </Card>
      </div>

      <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        Synthetic data only · workspace unity-gateway-demo · catalog trace_to_contain · Lakebase project trace-to-contain.
        The review-council personas that shaped this build are a simulated exercise, not statements from named individuals.
      </p>
    </div>
  );
}
