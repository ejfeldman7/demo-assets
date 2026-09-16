<!-- logo https://upload.wikimedia.org/wikipedia/commons/6/63/Databricks_Logo.png 240x80 -->
<!-- header Trace to Contain · Draft v0.1 · Internal -->
<!-- footer Databricks Field Engineering | Synthetic data only | Prepared for account-team review -->

# Trace to Contain — Demo PRD

<!-- toc -->

**Demo codename:** Trace to Contain · **Account:** an RF front-end module manufacturer · **Workspace:** fevm internal · **Owner:** Ethan Feldman (FE) · **Status:** Draft v0.1 (for review) · **Date:** 2026-09-11 · **Data:** Synthetic only

A governed FAB-to-field intelligence demo: an RMA becomes the trigger, the wafer/lot population becomes the evidence, and a role-gated containment decision closes the loop — analysis **and** operational action on one platform. This PRD was pressure-tested against four RF-manufacturing-representative personas (a simulated review exercise — see the Appendix). Their requirements shaped every section below.

## 1. TL;DR & hero metrics

**The pitch in one paragraph.** When a failed RF module comes back from a customer, RF fab engineers today spend days-to-weeks stitching together MES, FAB parametric, and test-log systems by hand to answer one question: *is the rest of that lot bad too, and did we already ship it?* Trace to Contain uses the RMA as the trigger, immediately pivots from the single unit to the **wafer/lot population it came from**, uses governed front-end→back-end correlation to explain the likely driver, predicts the at-risk population's back-end margin, and routes a **tiered, role-gated containment recommendation** to the engineer who actually holds that authority. The decision — and the model version, evidence, and lineage behind it — is written back for audit and to grade the system over time.

| Hero metric | Target | Framing |
|-------------|--------|---------|
| **Decision latency** — RMA receipt → contained disposition | **Weeks → Hours** | Cash-conversion and escape-risk story on a live quality event; **not** "engineer time saved." |
| **Containment reach** — at-risk units identified before shipment | **Population caught** | Proof the escape was contained, not just explained. |

**Opening line for the pitch:** *"When a bad module comes back from your customer, how long does it take you today to know whether the rest of that lot is also bad — and to stop it shipping? We'll take that from weeks to hours, and prove we caught the population before it reached your customer."* No "lakehouse / Lakebase / MLflow" in the first sentence.

## 2. Context & the seam we own

Following a customer discovery call, the strongest demo opportunity is correlation analysis that connects **FAB (wafer front-end) test data** to **module-level (back-end) outcomes** — explaining failures and supporting faster engineering decisions, delivered as an operational decision workflow rather than another dashboard.

**Play the seam, not the fortress.** The manufacturer already runs entrenched yield / test-data-analytics tooling (PDF Solutions, Onto-class YMS, MES, homegrown SPC). We **do not** compete on wafer-sort yield or final-test parametric analytics — that triggers an incumbent immune response and a committee knife-fight. The defensible white space is the **orphaned seam**: nobody today stitches die-level fab measurements to module-level and field-return outcomes across those three islands. That seam is exactly where quality escapes hide.

**Positioning — say this out loud:** *"We're not replacing your yield tool. We connect the three islands it can't reach — fab, final test, and field — and turn that into a governed, auditable action."* Coexist first; expansion follows the data. Never say *rip-and-replace*, and never put *automate* next to *disposition* — that phrase triggers the quality organization's antibodies.

## 3. Goals & non-goals

**Goals**

- Demonstrate a single, credible **operational loop** — RMA trigger → population trace → correlation → prediction → tiered recommendation → role-gated approval → write-back — on realistic synthetic RF-test data.
- Prove the **Databricks-only** capabilities incumbents structurally can't offer: cross-domain genealogy, one governed feature definition offline+online, end-to-end Unity Catalog lineage from raw measurement to disposition, and a self-grading closed loop.
- Give the account team a repeatable **narrative and set of hero numbers** that map to a real P&L outcome (escape/warranty cost, containment latency).
- Establish a narrow, honest first-pilot scope the manufacturer's FA/MRB org would actually approve.

**Non-goals**

- **Not this:** Beating the incumbent yield tool at wafer-sort yield analytics.
- **Not this:** Auto-executing any disposition. The agent recommends; humans with authority approve; MES/QMS execute.
- **Not this:** A production integration with real the plant MES/QMS/STDF feeds (we *acknowledge and stub* these, we don't build them).
- **Not this:** Single-unit RMA root-cause as the analytical core (statistically indefensible — see §6).

## 4. Who the demo must convince

We pressure-tested the concept against four RF-manufacturing-representative viewpoints (simulated persona review — see Appendix). Each imposes a hard requirement the demo must satisfy or it dies in that seat.

| Seat | What they judge on | Hard requirement |
|------|--------------------|------------------|
| **Test Engineer** | Does the correlation survive real test-floor physics? | Confounders (site/tool/temp/time) stripped; population-level, not RMA-of-one; wafer-map signal present. |
| **Data Engineer** | Does the architecture survive messy fab data? | Genealogy as a graph with trace grain/confidence; skew & point-in-time *shown*, not asserted. |
| **Executive** | Does it move a number I care about? | Anchored to escape/containment $$; leads with outcome, not architecture; concedes incumbent turf. |
| **Ops Leader** | Does it survive the real FA/MRB process? | Segregation of duties; hold flows into MES; audit satisfies IATF 16949 / 8D / CAPA. |

**The day-1 user is a person, not "an engineer."** Primary persona: the **FA / Product Engineer** who receives the case (pushed automatically from the QMS when an RMA is logged — not "opens an app"). Approving authorities are distinct roles (§10).

## 5. The operational loop (demo narrative)

The RMA is the **doorbell, not the evidence base.** The instant the case opens, the system moves off the single returned unit and onto the population. This is what reconciles the executive's emotional hook with the test engineer's statistical bar.

1. **RMA lands → case auto-created.** Pushed from the QMS/CRM (stubbed), not opened manually. Case severity/customer/reliability flag set on arrival.
2. **Genealogy graph → pivot to the population.** Show module→die→wafer→lot as a *graph*, with trace grain + confidence per hop. Immediately expand to the sibling die / wafer / reticle-field population that shares the suspect lineage.
3. **Explain the likely driver (population correlation).** Front-end→back-end correlation shown as *partial / within-stratum* with the tool/site/time confounder stripped out, plus the wafer-map spatial pattern. Label which limit: spec vs. guardbanded test vs. control.
4. **Predict the at-risk population.** Expected final-test margin loss across the lot, with uncertainty, contributing features, model version, and spec comparison. Show the same feature value from training set and online lookup, side by side.
5. **Agent recommends tiered containment.** Least-disruptive effective action (100% screen / sample / partial hold / full hold / no action) with a cost↔risk trade-off, evidence, and confidence. Presented as *pending approval* — never executed.
6. **Role-gated approval.** FA engineer proposes; Quality Engineer approves a hold; MRB (multi-signature) for scrap/release. Hold is issued as an *instruction to MES*, not a status flip. Full audit record captured.
7. **Close the loop.** Disposition written back to the lakehouse, linked to the prediction & recommendation, feeding accuracy / acceptance metrics and model retraining.

## 6. First-pilot scope

One product, one measurement family, one outcome — chosen so the genealogy actually resolves and the physics is demonstrable.

| Dimension | Pilot choice | Why |
|-----------|--------------|-----|
| **Hero product** | High-volume **BAW / TC-SAW filter** (discrete band filter) | Single dominant die type → die→module genealogy resolves cleanly, dodging the multi-die trap for the main story. |
| **Front-end signal** | Wafer-probe **center-frequency shift + insertion loss** | Driven by piezo/electrode film thickness → believable **radial wafer-map signature** and a real causal chain, not a fitted coincidence. |
| **Prediction target** | Continuous **final-test IL / margin delta**, population-level, conditioned on site/tool/temp/limit-set | Continuous delta is more honest and useful than pass/fail — RF margins live in the guardband. |
| **Outcome / hero metric** | At-risk units caught before module assembly; RMA→contained-disposition time | Catching a bad filter wafer before it's built into an expensive module is where the money is. |
| **Genealogy foil** | One secondary **multi-die FEM** product, wafer-traceable only | Shown in the genealogy panel to prove the graph + graceful degradation when die-level trace doesn't exist. |

**The tension we resolved.** The exec wants the RMA war-story hook; the test engineer says single-RMA traceability is statistically indefensible (tiny N, 30–50%+ no-fault-found). **Resolution:** the RMA triggers the narrative; the analytical payload is always the *population*, backed by large-N correlation. Single-unit root-cause is a Phase-2 flourish once the model is credible.

## 7. Data architecture

Lakehouse (Delta / Unity Catalog) is the analytical **system of record**: full history, correlation, training, point-in-time. Lakebase (Postgres/OLTP) holds only **current decision-relevant operational state** — never a duplicate of raw FAB history.

### 7.1 Genealogy as a graph, not a chain (make-or-break)

An RF module is *many* die (PA, filters, switch, controller) from different wafers/lots/fabs, and RF die often have no per-die serial — so trace is frequently only to *wafer* or *lot*, not die. Model it as nodes + a bridge that carries the honest grain:

| Table | Grain | Key fields |
|-------|-------|-----------|
| `lot` | one lot | lot_id, product, fab, start_ts |
| `wafer` | lot × wafer# | wafer_id (lot_id+wafer_no), reticle_map |
| `die` | wafer × (x,y) | die_id (wafer_id+x+y), die_uid? |
| `module` | one assembled unit | module_sn, tray_pos |
| `die_to_module` | bridge (many→1) | module_sn, die_id, **trace_grain** (die/wafer/lot), **trace_confidence** |

The genealogy panel prints the actual keys at each hop and **labels the grain/confidence** — and degrades gracefully ("traceable to wafer 3 of lot X, not to die"). That single detail earns credibility with real engineers.

### 7.2 Parametric as a long / EAV table

Hundreds of params × millions of die; test programs rev and rename parameters. A wide fixed-column table breaks. Model long:

| Table | Grain / columns | Purpose |
|-------|-----------------|---------|
| `fab_measurements` | wafer_id, die_x, die_y, param_id, value, unit, lo_limit, hi_limit, test_program_rev, site, temp, meas_ts | Wafer-sort parametric, long grain, with covariates for de-confounding. |
| `parameter_dim` | param_id, canonical_name, aliases[], family | Reconciles renamed params across program revs. |
| `back_end_outcomes` | module_sn, ft_param, value, bin, meas_ts | Final-test parametric & bins (the label source). |
| `rma_history` | rma_id, module_sn, customer, failure_mode, received_ts | Field returns — the trigger, not the evidence base. |
| `feature_history` | entity_id, feature values, **event_ts**, valid_from/to | Time-versioned features for point-in-time training joins. |
| `correlation_results` | stratum keys, param_id, partial_corr, **computed_as_of** | Precomputed, stratified correlations served to app + agent. |
| `model_evaluations` | model_version, metric, holdout_window | Point-in-time holdout results per model version. |

### 7.3 Lakebase — operational state only

| Table | Holds |
|-------|-------|
| `engineering_cases` | Active investigations: case_id, module_sn, severity, customer, sla_clock, state. |
| `online_features` | Current per-case/per-entity feature vector (served from one shared definition). |
| `predictions` | Prediction, uncertainty, model_version, feature values seen, ts. |
| `recommendations` | Action + tier, supporting measurements, correlation refs, predicted impact, confidence, rejected alternatives, status. |
| `approval_actions` | Approver + **role & authority level at time of action**, decision, rationale, ts (append-only). |
| `workflow_events` | State transitions, MES/QMS instructions issued, escalations. |

**Volume honesty — put it on screen.** Show the row-count delta explicitly: `fab_measurements` in the **billions** (lakehouse) vs. `online_features` in the **thousands** (Lakebase). One panel makes the split self-justifying.

### 7.4 Show — don't assert — feature parity & point-in-time

- **One feature definition** (Databricks Feature Engineering) backs both training and online serving. In the demo, print `feature_x` from the training set and from the Lakebase online lookup for the same die and show they're identical.
- **Point-in-time as-of join:** label = back-end outcome at final-test time; features = parametric values as of *wafer-sort* time via `create_training_set()`. Narrate the **leakage trap we refused**: training never reads current `online_features`.
- **Authoritative-insertion rule** for retest/rebin, stated explicitly (e.g. first-pass result), so labels are consistent.

### 7.5 Governance & lineage (differentiator)

Everything through Unity Catalog. Show UC **lineage end-to-end**: `fab_measurements` → `feature_history` → training set → model → prediction → disposition write-back. Lakebase governed in UC too, so the operational store isn't an ungoverned island. For a regulated manufacturer, *this is the audit story* — and incumbents can't tell it.

## 8. Correlation & ML approach

**Kill confounding before claiming correlation.** Multi-site ATE (×16/×32 parallel), probe-card/handler/socket effects, chamber temperature, and day-to-day tool drift manufacture *fake* correlations by default. Cross-insertion, the same parameter is autocorrelated (predicting a thing from an earlier measurement of itself).

- Site / tool / temperature / date / limit-set version are **first-class covariates**.
- "Top correlated parameters" is shown as **partial / within-stratum** correlation, scoped per-product and per-limit-set — never raw, to avoid Simpson's paradox sign-flips.
- Wafer-map spatial signal (radial / edge / ring / cluster) is a first-class feature; treat spatial autocorrelation honestly (adjacent die aren't independent).

**Model & MLflow lifecycle**

- **Target:** continuous final-test IL/margin delta (regression), population-level. Not pass/fail, not RMA-risk.
- MLflow registry with champion/challenger; `model_evaluations` against a point-in-time holdout; feature-drift monitoring (fab drift = feature drift = model decay); disposition write-back feeds retraining.
- Model is *one input* to the workflow — **do not hang the pitch on accuracy** (synthetic data invites the yield PhDs to break it). Anchor on workflow + containment outcome.

## 9. Agent design & guardrails

The agent is responsible for **analysis and recommendation only** — never unrestricted mutation of operational data. It retrieves governed genealogy, stratified correlation results, current features, and prediction records, and returns a structured, evidence-backed recommendation.

**Tiered containment (not one flat "lot hold"):** No action → Sample screen → 100% screen → Partial hold → Full lot hold. The agent recommends the **least-disruptive effective** action and shows the trade-off — false hold (scrap / expedite / missed commit / burned credibility) vs. missed hold (escape / SCAR / customer line-down). Every recommendation carries evidence, confidence/uncertainty, model version, and the alternatives it rejected.

Governed retrieval answers the call's natural-language examples: *"Trace this RMA to the wafer and show abnormal FAB measurements," "Which parameters are most correlated with back-end failure for this product?," "What's the predicted back-end delta for this lot?," "Which other modules share this feature pattern?," "Recommend the next action and explain the evidence."*

## 10. Workflow & authority model

**The single most important operational thing to get right:** a lot hold is an **authorized containment instruction that flows into MES/QMS**, executed by the role that holds that authority — *not* a status flipped by one engineer in a standalone app. A "HOLD" the app records but MES doesn't honor is phantom containment: the worst outcome in a quality system.

**Segregation of duties (three roles, not one button)**

| Role | Authority |
|------|-----------|
| **FA / Product Engineer** | Opens case, reviews evidence, dispositions the failed *unit*, *proposes* a lot-level action. |
| **Quality Engineer / Supervisor** | Only role that can approve a **lot hold / containment**. |
| **MRB (multi-signature quorum)** | Required for scrap / rework / use-as-is / hold release. |

**State machine (not a status field):** New → Under FA Review → Recommendation Pending → Approved/Rejected/Edited → MES Hold Applied → MRB Disposition → Written Back → Closed. Pending > N hours auto-escalates to a supervisor. A prioritized queue (customer severity, AEC/reliability or safety flag, RMA age) with an SLA clock prevents the pending list becoming a graveyard and prevents alert fatigue.

**Audit fields (for IATF 16949 / customer audits / 8D / CAPA)**

- Approver **role & authority level at time of action** — not just who clicked.
- Rejected alternatives + free-text rationale (why this action, not the others).
- Input data snapshot ref + full data lineage + model version + feature values seen (reproduce what the model saw).
- Link to **NCR / 8D / CAPA** record and the MRB disposition.
- Containment type *and tier*; immutable, append-only event log.

**Integration honesty — say it plainly:** *"The app recommends and records the decision; MES executes the hold; QMS owns the CAPA."* Acknowledge the touch-points (MES for WIP/lot status, QMS/CAPA for 8D/NCR/SCAR, YMS for measurements, ERP for inventory quarantine/ship-block, customer notification path) even where stubbed. That honesty is what makes a quality leader trust it instead of fear it.

## 11. Synthetic data — build in the realistic ugliness

Correlations that look too clean tell the room it won't survive real fab noise. Deliberately model:

- **Graph genealogy** with a multi-die FEM (die from different wafers/lots) and one product that is **only wafer-traceable** so the UI degrades gracefully.
- **Wafer-map spatial signatures** — a radial film-thickness gradient driving center-frequency shift, plus edge/ring patterns.
- **Confounders** — site-to-site offset, a tool that drifts over a specific week, temperature dependence — so we can *demonstrate stripping them out* and the signal surviving.
- **Schema drift** — the same physical parameter under two names across two `test_program_rev`s, reconciled via `parameter_dim`.
- **Test reality** — multi-site records, a retest/rebin row (with the dedup-to-authoritative-result rule), guardbanded test limits distinct from spec limits.
- **Field reality** — small RMA N with a realistic no-fault-found fraction, so the population pivot is visibly necessary.
- **STDF-shaped ingestion slice** — a landing volume of STDF-like files → Auto Loader / Lakeflow parse → the long table. Name the STDF problem; don't build a full binary parser.

## 12. Explicit cuts & deferrals

What we deliberately leave out of the pitch or the first build, and why — so the account team can defend the choices in the room.

| Item | Decision | Why |
|------|----------|-----|
| Yield-uplift framing | Cut from pitch | Incumbent turf; triggers immune response. |
| Prediction accuracy as lead metric | Cut as lead | Synthetic data invites model-breaking; anchor on workflow + containment. |
| Single-unit RMA root-cause | Defer to Phase 2 | Statistically indefensible until the model is credible on population N. |
| Wide `fab_measurements` table | Replace | Use long/EAV + `parameter_dim`; wide breaks on program revs. |
| Real MES/QMS/STDF integration | Stub + acknowledge | Demo shows the shape and honesty of the touch-points, not production connectors. |
| "Engineer approves" single button | Replace | Violates segregation of duties; use the 3-role model (§10). |

## 13. Success metrics

Two hero numbers on screen (§1); the rest are supporting, framed as risk/cash, not productivity.

- **RMA-receipt → contained-disposition time** (hero) — decision latency on a live quality event.
- **At-risk units identified before shipment** (hero) — proof of containment reach.
- Earlier abnormal-lot identification (lead time vs. current process).
- Recommendation acceptance rate (trust signal from the closed loop).
- Prediction accuracy on point-in-time holdout (supporting, never the headline).
- Manual-lookup time eliminated (supporting productivity metric).

## 14. Build plan

Target: **fevm internal workspace**, synthetic data only, packaged as a Declarative Automation Bundle (DAB). Stack: Delta/UC lakehouse, Databricks Feature Engineering, MLflow, Lakebase (Postgres), a Databricks App (FastAPI + React) for the case UI, and a governed agent for retrieval/recommendation.

**Phase 0 — Synthetic data + genealogy graph (foundation)**

- Generate BAW/TC-SAW hero product + multi-die FEM foil with the realistic ugliness (§11).
- Graph genealogy tables + `die_to_module` bridge with trace grain/confidence.
- Long `fab_measurements` + `parameter_dim`; STDF-shaped landing → parse slice.
- Everything in Unity Catalog with visible lineage.

**Phase 1 — Correlation + model + feature store (intelligence)**

- Stratified/partial correlation job → `correlation_results` with `computed_as_of`.
- One feature definition; point-in-time training set; regression model + MLflow registry + point-in-time `model_evaluations`.
- Feature upsert → Lakebase `online_features`; prove parity + no-leakage on screen.

**Phase 2 — Operational app + agent + loop (the demo)**

- Case UI: genealogy graph, correlation/wafer-map view, prediction panel, tiered recommendation.
- Governed agent for retrieval + structured recommendation.
- 3-role authority model, state machine, append-only audit, MES-instruction stub, write-back & self-grading.

## 15. Risks & open questions

- **Synthetic credibility.** If correlations look too clean the room disbelieves it — mitigated by §11, but needs a careful hand on the wafer-map physics.
- **Scope creep.** The graph genealogy + feature-parity proof are the expensive, credibility-critical pieces; the app is where it's tempting to over-invest. Keep the app thin, the data honest.
- **"What does it take on our real data?"** Have a crisp answer on STDF ingestion effort and timeline — execs and data engineers both ask.
- **Open:** Which specific filter band / product family reads most authentically to the customer? Do we have a (even anonymized) real quality-escape war story to map the demo to?
- **Open:** Confirm the fevm workspace has Lakebase + Feature Engineering + model serving enabled for the build.

## Appendix — the review council

This PRD was pressure-tested against four simulated RF-manufacturing-representative personas (an internal review exercise — *not* statements from real named individuals). Each surfaced the requirement that shaped the section noted.

**Test Engineer (shaped §6, §8).** *"Kill confounding before you claim correlation. If you show me a raw correlation that turns out to be 'both drifted on the same tool that week,' you've confirmed I shouldn't trust the platform. Strip the confounder out and show the signal survives — that's the moment I believe it."* Also: genealogy is a tree, not a chain; RMA-of-one is the weakest analytical unit (high no-fault-found); lead with population correlation on a BAW/TC-SAW filter and wafer-map frequency signal.

**Data Engineer (shaped §7).** *"Don't say 'reduces training-serving skew' — show the same feature value out of both paths. Don't say 'point-in-time' — show the as-of join and the leakage you refused. This audience believes demonstrations, not adjectives."* Also: flat genealogy table is naive — model the graph with trace_grain/confidence; long/EAV parametric; name the STDF ingestion reality; add UC lineage; show the billions-vs-thousands row-count delta.

**Executive (shaped §1, §2).** *"If your first sentence has 'lakehouse,' 'Lakebase,' 'MLflow,' or 'genealogy graph' in it, I've checked my phone. If it's about a bad part reaching my customer and how fast we caught the rest, I'm leaning forward. Fund-a-pilot happens in that first sentence or it doesn't happen."* Also: anchor to escape/warranty cost + containment latency; de-prioritize yield; concede incumbent turf and win the seam; don't sell the approval button as a feature.

**Ops Leader (shaped §10).** *"Lead with 'this makes your existing FA/MRB process faster and more traceable,' not 'this automates disposition.' The word automates near the word disposition is what triggers the quality organization's antibodies."* Also: segregation of duties (3 roles); a hold is an instruction to MES, not a status flip; audit must capture authority-at-time + rejected alternatives + 8D/CAPA linkage; name the day-1 user; push the case from the QMS.
