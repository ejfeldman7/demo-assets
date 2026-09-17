"""Trace to Contain — FastAPI backend (Lakebase-backed operational app).

Serves the case-management workflow: reads decision-relevant state from the Lakebase
`trace_ops` schema, runs the governed tiered-containment recommendation engine, and
enforces the role-gated state machine + append-only audit on approval.
"""
import os, json, time, threading, logging
from datetime import timedelta
from typing import Optional
import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

SCHEMA = "trace_ops"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# ----------------------------------------------------------------- Lakebase auth
# Databricks Apps runtime injects PGHOST / PGUSER (SP client id). We generate a fresh
# 1-hour OAuth DB credential via the SDK and refresh it in the background.
_token = {"val": None, "exp": 0}
_lock = threading.Lock()

def _new_token() -> str:
    import os as _os
    if _os.environ.get("LOCAL_PG_TOKEN"):
        return _os.environ["LOCAL_PG_TOKEN"]
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    ep = os.environ.get("LAKEBASE_ENDPOINT") or os.environ["PGAPPNAME"]
    cred = w.postgres.generate_database_credential(endpoint=ep)
    return cred.token

def current_token() -> str:
    with _lock:
        if _token["val"] is None or time.time() > _token["exp"]:
            _token["val"] = _new_token()
            _token["exp"] = time.time() + 30 * 60   # refresh every 30 min
        return _token["val"]

def _connect() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["PGHOST"], user=os.environ["PGUSER"], password=current_token(),
        dbname=os.environ.get("PGDATABASE", "databricks_postgres"), sslmode="require",
        row_factory=dict_row, connect_timeout=30, options=f"-c search_path={SCHEMA}")

app = FastAPI(title="Trace to Contain")

# gzip API/JSON + static payloads (works through the Databricks Apps proxy)
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=500)

# per-request timing — logged via uvicorn.error so it shows up in `databricks apps logs`
_log = logging.getLogger("uvicorn.error")

@app.middleware("http")
async def _timing(request: Request, call_next):
    t0 = time.perf_counter()
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        _log.info("%s %s -> %s %.0fms", request.method, request.url.path, resp.status_code,
                  (time.perf_counter() - t0) * 1000)
    return resp

def q(sql: str, params=None, one=False, write=False):
    with _connect() as c:
        with c.cursor() as cur:
            cur.execute(sql, params or ())
            if write:
                c.commit()
                try:
                    return cur.fetchone() if one else cur.fetchall()
                except psycopg.ProgrammingError:
                    return None
            return cur.fetchone() if one else cur.fetchall()

# ----------------------------------------------------------------- recommendation engine
TIERS = ["No action", "Sample screen", "100% screen", "Partial wafer hold", "Full lot hold"]

def recommend(is_nff, at_risk_rate, n_pop, mean_margin, is_exc, severity, priority):
    if is_nff or (at_risk_rate is not None and at_risk_rate < 0.02 and not is_exc):
        tier = 0
    elif at_risk_rate is None:
        tier = 1
    elif at_risk_rate < 0.10: tier = 1
    elif at_risk_rate < 0.25: tier = 2
    elif at_risk_rate < 0.45: tier = 3
    else: tier = 4
    escalated = bool(priority and priority >= 3 and 0 < tier < 4)
    if escalated: tier += 1
    tier = min(tier, 4)
    conf = 0.6 + (min(n_pop or 0, 400) / 400.0) * 0.3 + (0.07 if is_exc else 0)
    conf = round(min(conf, 0.97), 2)
    ar = f"{100*at_risk_rate:.1f}%" if at_risk_rate is not None else "n/a"
    if tier == 0:
        rationale = ("No fault found on the returned unit and the sibling wafer population is within "
                     "guardband — analysis only, no containment.")
    else:
        rationale = (f"{ar} of the {n_pop or 0} sibling die on this wafer are predicted below the guardband "
                     f"(mean predicted margin {mean_margin:.2f} dB). "
                     f"{'Excursion lineage (DEP-02) confirmed. ' if is_exc else ''}"
                     f"Recommending the least-disruptive effective containment: {TIERS[tier]}"
                     + (f" (escalated one tier for {severity} customer)." if escalated else "."))
    impact = "No units held." if tier == 0 else \
        f"~{int((at_risk_rate or 0) * (n_pop or 0))} at-risk die contained before module assembly."
    rej = []
    if tier > 0:
        rej.append({"action": TIERS[tier-1], "why_not": "Under-contains the predicted at-risk population; escape risk to customer."})
    if tier < 4:
        rej.append({"action": TIERS[tier+1], "why_not": "Over-contains healthy material; unnecessary scrap/expedite cost."})
    return tier, rationale, impact, conf, rej

# ----------------------------------------------------------------- role / authority model
ROLES = {
    "FA Engineer":       {"can_propose": True,  "can_contain": False, "can_dispose": False,
                          "label": "FA / Product Engineer", "who": "a.chen@example.com"},
    "Quality Engineer":  {"can_propose": True,  "can_contain": True,  "can_dispose": False,
                          "label": "Quality Engineer / Supervisor", "who": "q.rivera@example.com"},
    "MRB":               {"can_propose": False, "can_contain": True,  "can_dispose": True,
                          "label": "Material Review Board", "who": "mrb-quorum@example.com"},
}
CONTAINMENT_TIERS = {1, 2, 3, 4}   # tiers that require Quality authority to approve

# ----------------------------------------------------------------- endpoints
@app.get("/api/health")
def health():
    return {"ok": True}

@app.get("/api/roles")
def roles():
    return [{"role": r, **v} for r, v in ROLES.items()]

@app.get("/api/summary")
def summary():
    by_state = q(f"SELECT state, count(*) n FROM engineering_cases GROUP BY state")
    tot = q("""SELECT count(*) total,
                 sum(CASE WHEN is_excursion_lineage THEN 1 ELSE 0 END) excursion,
                 sum(CASE WHEN sla_due_ts < now() AND state NOT IN ('Closed') THEN 1 ELSE 0 END) sla_breached
               FROM engineering_cases""", one=True)
    at_risk = q("SELECT coalesce(sum(n_at_risk),0) units_at_risk FROM wafer_population", one=True)
    pend = q("SELECT count(*) n FROM recommendations WHERE status='pending'", one=True)
    return {"by_state": by_state, "totals": tot, "at_risk": at_risk, "pending_recs": pend["n"]}

@app.get("/api/correlations")
def correlations():
    rows = q("SELECT param_id, method, corr, n FROM correlation_results")
    out = {}
    for r in rows:
        out.setdefault(r["param_id"], {})[r["method"]] = r["corr"]
    return out

@app.get("/api/cases")
def cases(state: Optional[str] = None, qtext: Optional[str] = None, limit: int = 60):
    where, params = [], []
    if state and state != "All":
        where.append("c.state = %s"); params.append(state)
    if qtext:
        where.append("(c.case_id ILIKE %s OR c.module_sn ILIKE %s OR c.wafer_id ILIKE %s OR c.customer ILIKE %s)")
        params += [f"%{qtext}%"]*4
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    rows = q(f"""SELECT c.case_id, c.customer, c.product, c.failure_mode, c.is_nff, c.severity, c.priority,
                   c.wafer_id, c.lot_id, c.state, c.is_excursion_lineage, c.sla_due_ts,
                   (c.sla_due_ts < now() AND c.state <> 'Closed') AS sla_breached,
                   p.pred_ft_margin_db, wp.n_at_risk, wp.at_risk_rate,
                   r.action AS rec_action, r.tier AS rec_tier, r.status AS rec_status
                 FROM engineering_cases c
                 LEFT JOIN predictions p ON p.case_id=c.case_id
                 LEFT JOIN wafer_population wp ON wp.case_id=c.case_id
                 LEFT JOIN LATERAL (SELECT action, tier, status FROM recommendations
                                    WHERE case_id=c.case_id ORDER BY created_ts DESC LIMIT 1) r ON true
                 {clause}
                 ORDER BY c.priority DESC, sla_breached DESC, c.received_ts
                 LIMIT %s""", params)
    return rows

@app.get("/api/cases/{case_id}")
def case_detail(case_id: str):
    c = q("SELECT * FROM engineering_cases WHERE case_id=%s", (case_id,), one=True)
    if not c:
        raise HTTPException(404, "case not found")
    feats = q("SELECT * FROM online_features WHERE case_id=%s", (case_id,), one=True)
    pred = q("SELECT * FROM predictions WHERE case_id=%s", (case_id,), one=True)
    pop = q("SELECT * FROM wafer_population WHERE case_id=%s", (case_id,), one=True)
    gen = q("SELECT hop, level, node_key, trace_grain, trace_confidence, detail FROM case_genealogy WHERE case_id=%s ORDER BY hop", (case_id,))
    rec = q("SELECT * FROM recommendations WHERE case_id=%s ORDER BY created_ts DESC LIMIT 1", (case_id,), one=True)
    appr = q("""SELECT a.* FROM approval_actions a JOIN recommendations r ON a.rec_id=r.rec_id
                WHERE r.case_id=%s ORDER BY a.action_ts""", (case_id,))
    events = q("SELECT event_type, from_state, to_state, actor, actor_role, detail, mes_instruction, event_ts FROM workflow_events WHERE case_id=%s ORDER BY event_ts", (case_id,))
    # sibling wafer map: predicted margin per die on this wafer (for the spatial view)
    return {"case": c, "features": feats, "prediction": pred, "population": pop,
            "genealogy": gen, "recommendation": rec, "approvals": appr, "events": events}

@app.post("/api/cases/{case_id}/recommend")
def gen_recommendation(case_id: str):
    c = q("SELECT * FROM engineering_cases WHERE case_id=%s", (case_id,), one=True)
    if not c:
        raise HTTPException(404, "case not found")
    pop = q("SELECT * FROM wafer_population WHERE case_id=%s", (case_id,), one=True)
    pred = q("SELECT * FROM predictions WHERE case_id=%s", (case_id,), one=True)
    tier, rationale, impact, conf, rej = recommend(
        c["is_nff"], pop["at_risk_rate"] if pop else None, pop["n_population"] if pop else None,
        pop["mean_pred_margin"] if pop else 0.0, c["is_excursion_lineage"], c["severity"], c["priority"])
    evidence = {"predicted_margin_db": round(pred["pred_ft_margin_db"], 3) if pred and pred["pred_ft_margin_db"] is not None else None,
                "n_population": pop["n_population"] if pop else None, "n_at_risk": pop["n_at_risk"] if pop else None,
                "at_risk_rate": round(pop["at_risk_rate"], 3) if pop and pop["at_risk_rate"] is not None else None,
                "trace_grain": "die", "excursion_lineage": c["is_excursion_lineage"]}
    corr = {"top_param": "fc_meas", "within_site_corr": 0.59, "raw_pooled_corr": 0.538,
            "note": "il_meas raw -0.41 collapses to -0.17 within week (probe-card drift artifact)"}
    row = q("""INSERT INTO recommendations
                 (case_id,action,tier,rationale,evidence,correlation_refs,predicted_impact,confidence,rejected_alternatives,status,model_version)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s) RETURNING rec_id""",
            (case_id, TIERS[tier], tier, rationale, json.dumps(evidence), json.dumps(corr), impact, conf,
             json.dumps(rej), pred["model_version"] if pred else None), one=True, write=True)
    q("""INSERT INTO workflow_events (case_id,event_type,from_state,to_state,actor,actor_role,detail)
         VALUES (%s,'recommendation_generated',%s,'Recommendation Pending','trace-agent','agent',%s)""",
      (case_id, c["state"], f"Agent recommended: {TIERS[tier]} (confidence {conf})"), write=True)
    q("UPDATE engineering_cases SET state='Recommendation Pending', updated_ts=now() WHERE case_id=%s", (case_id,), write=True)
    return {"rec_id": row["rec_id"], "action": TIERS[tier], "tier": tier}

@app.post("/api/cases/{case_id}/approve")
def approve(case_id: str, body: dict = Body(...)):
    role = body.get("role"); decision = body.get("decision")  # approve | reject | edit
    rationale = body.get("rationale", ""); edited_action = body.get("edited_action")
    if role not in ROLES:
        raise HTTPException(400, "unknown role")
    rec = q("SELECT * FROM recommendations WHERE case_id=%s ORDER BY created_ts DESC LIMIT 1", (case_id,), one=True)
    c = q("SELECT * FROM engineering_cases WHERE case_id=%s", (case_id,), one=True)
    if not rec or not c:
        raise HTTPException(404, "no pending recommendation")
    tier = rec["tier"] if edited_action is None else TIERS.index(edited_action)
    perms = ROLES[role]
    # authority gate: containment tiers require Quality (or MRB); FA cannot apply a hold
    if decision == "approve" and tier in CONTAINMENT_TIERS and not perms["can_contain"]:
        raise HTTPException(403, f"{ROLES[role]['label']} cannot approve containment ({TIERS[tier]}). "
                                 f"A Quality Engineer holds that authority (segregation of duties).")
    actor = perms["who"]; authority = "lot_hold" if tier in CONTAINMENT_TIERS else "analysis_only"
    q("""INSERT INTO approval_actions (rec_id,case_id,approver,approver_role,authority_level,decision,edited_action,rationale)
         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
      (rec["rec_id"], case_id, actor, ROLES[role]["label"], authority, decision, edited_action, rationale), write=True)
    from_state = c["state"]
    if decision == "reject":
        q("UPDATE recommendations SET status='rejected' WHERE rec_id=%s", (rec["rec_id"],), write=True)
        q("UPDATE engineering_cases SET state='Under FA Review', updated_ts=now() WHERE case_id=%s", (case_id,), write=True)
        q("""INSERT INTO workflow_events (case_id,event_type,from_state,to_state,actor,actor_role,detail)
             VALUES (%s,'recommendation_rejected',%s,'Under FA Review',%s,%s,%s)""",
          (case_id, from_state, actor, ROLES[role]["label"], rationale or "Recommendation rejected"), write=True)
        return {"state": "Under FA Review", "decision": "reject"}
    # approve / edit
    q("UPDATE recommendations SET status='approved', action=%s, tier=%s WHERE rec_id=%s",
      (TIERS[tier], tier, rec["rec_id"]), write=True)
    if tier == 0:
        new_state, mes = "Closed", None
        evt, detail = "closed_no_action", "No-action recommendation approved; case closed."
    else:
        new_state = "MES Hold Applied"
        mes = f"HOLD wafer {c['wafer_id']} :: {TIERS[tier]} :: instruction issued to MES"
        evt, detail = "containment_applied", f"{TIERS[tier]} approved by {ROLES[role]['label']}"
    q("UPDATE engineering_cases SET state=%s, updated_ts=now() WHERE case_id=%s", (new_state, case_id), write=True)
    q("""INSERT INTO workflow_events (case_id,event_type,from_state,to_state,actor,actor_role,detail,mes_instruction)
         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
      (case_id, evt, from_state, new_state, actor, ROLES[role]["label"], detail, mes), write=True)
    return {"state": new_state, "decision": "approve", "mes_instruction": mes, "action": TIERS[tier]}

@app.get("/api/cases/{case_id}/wafermap")
def wafermap(case_id: str):
    c = q("SELECT wafer_id FROM engineering_cases WHERE case_id=%s", (case_id,), one=True)
    if not c:
        raise HTTPException(404, "case not found")
    dies = q("""SELECT die_x, die_y, radius, pred_ft_margin_db, pred_escape_risk, is_case_die
                FROM wafer_die_map WHERE wafer_id=%s ORDER BY die_y, die_x""", (c["wafer_id"],))
    return {"wafer_id": c["wafer_id"], "dies": dies}

NARRATIVE_ENDPOINT = os.environ.get("NARRATIVE_ENDPOINT", "databricks-claude-opus-4-8")

@app.post("/api/cases/{case_id}/narrative")
def narrative(case_id: str):
    """LLM narrative explaining the recommendation in engineer-facing prose (model serving)."""
    d = case_detail(case_id)
    c, rec, pop, pred = d["case"], d["recommendation"], d["population"], d["prediction"]
    if not rec:
        raise HTTPException(400, "no recommendation to narrate")
    prompt = f"""You are a RF-semiconductor failure-analysis assistant. In 3-4 sentences, plain and factual,
explain to an engineer why this containment recommendation was made. Do not invent numbers.

RMA {c['rma_id']} — customer {c['customer']} ({c['severity']}). Product {c['product']}.
Returned unit failure mode: {c['failure_mode']}{' (no fault found on retest)' if c['is_nff'] else ''}.
Traced to wafer {c['wafer_id']} on tool {c['dep_tool']}; excursion lineage: {c['is_excursion_lineage']}.
Predicted final-test margin for the returned die: {pred['pred_ft_margin_db']:.2f} dB (spec 0.0, guardband 0.5).
Sibling wafer population: {pop['n_population']} die, {pop['n_at_risk']} predicted below guardband ({(pop['at_risk_rate'] or 0)*100:.0f}%).
Strongest front-end driver: center-frequency shift (correlation holds after conditioning on ATE site);
measured insertion loss looks correlated raw but collapses once conditioned on time (probe-card drift artifact).
Agent recommendation: {rec['action']} (confidence {rec['confidence']:.0%}). {rec['rationale']}
Emphasise the population-level reasoning and that the returned unit is only the trigger."""
    try:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient().serving_endpoints.get_open_ai_client()
        resp = client.chat.completions.create(
            model=NARRATIVE_ENDPOINT,
            messages=[{"role": "user", "content": prompt}], max_tokens=300, timeout=30)
        return {"narrative": resp.choices[0].message.content, "model": NARRATIVE_ENDPOINT}
    except Exception as e:
        return {"narrative": rec["rationale"] + " " + (rec["predicted_impact"] or ""),
                "model": "fallback (deterministic)", "error": str(e)[:200]}

# ----------------------------------------------------------------- what-if live scoring
WHATIF_ENDPOINT = os.environ.get("WHATIF_ENDPOINT", "trace-margin-whatif")
WHATIF_FEATURES = ["fc_meas", "il_meas", "return_loss_db", "q_factor", "bw_mhz", "rejection_db",
                   "leakage_na", "static_cap_pf", "temp_coeff_ppm", "radius", "temp_c", "site"]
# nominal defaults for features not stored in online_features
WHATIF_DEFAULTS = {"leakage_na": 5.0, "static_cap_pf": 2.2, "temp_coeff_ppm": -22.0, "temp_c": 25.0}
# the SDK's serving query() has no timeout param; run it on a small pool so a cold/hung endpoint
# can be abandoned at a wall-clock deadline instead of blocking the request thread indefinitely.
import concurrent.futures as _cf
_whatif_pool = _cf.ThreadPoolExecutor(max_workers=4)

@app.get("/api/cases/{case_id}/whatif")
def whatif_baseline(case_id: str):
    """Return the case's current feature vector + baseline prediction, to seed the what-if panel."""
    f = q("SELECT * FROM online_features WHERE case_id=%s", (case_id,), one=True)
    p = q("SELECT pred_ft_margin_db FROM predictions WHERE case_id=%s", (case_id,), one=True)
    if not f:
        raise HTTPException(404, "case not found")
    vec = {k: (f.get(k) if f.get(k) is not None else WHATIF_DEFAULTS.get(k, 0.0)) for k in WHATIF_FEATURES}
    return {"features": vec, "baseline_pred": p["pred_ft_margin_db"] if p else None,
            "spec": 0.0, "guardband": 0.5}

@app.post("/api/cases/{case_id}/whatif")
def whatif_predict(case_id: str, body: dict = Body(...)):
    """Live prediction against the real-time serving endpoint with (possibly edited) feature values."""
    f = q("SELECT * FROM online_features WHERE case_id=%s", (case_id,), one=True)
    if not f:
        raise HTTPException(404, "case not found")
    overrides = body.get("overrides", {})
    rec = {}
    for k in WHATIF_FEATURES:
        if k in overrides and overrides[k] is not None:
            rec[k] = float(overrides[k])
        elif f.get(k) is not None:
            rec[k] = float(f[k])
        else:
            rec[k] = float(WHATIF_DEFAULTS.get(k, 0.0))
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        fut = _whatif_pool.submit(w.serving_endpoints.query, name=WHATIF_ENDPOINT, dataframe_records=[rec])
        resp = fut.result(timeout=30)
        pred = float(resp.predictions[0])
        return {"pred_ft_margin_db": pred,
                "escape_risk": 0.0 < pred <= 0.5, "fail_spec": pred <= 0.0,
                "features": rec, "endpoint": WHATIF_ENDPOINT}
    except _cf.TimeoutError:
        raise HTTPException(504, "Scoring endpoint is taking too long (it may be scaling up) — retry in a moment.")
    except Exception as e:
        raise HTTPException(503, f"serving endpoint unavailable: {str(e)[:200]}")

# ----------------------------------------------------------------- Genie (population Q&A)
# A curated Genie space over the analytical lakehouse (trace_to_contain.lakehouse) answers
# open-ended, population- and portfolio-level questions the guided case flow can't pre-answer
# (blast-radius, shipment exposure, cross-case patterns). Create it with
# src/ops/02_create_genie_space.py, then set GENIE_SPACE_ID in app.yaml.
GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
GENIE_TIMEOUT = timedelta(seconds=90)   # bound the wait; SDK default is ~20 min

def _genie_clients(request: Request):
    """Clients to try, best-governance first: on-behalf-of the signed-in user (UC/RLS apply to
    *them*, forwarded by Databricks Apps), then the app service principal. The SP fallback covers
    *auth* failures (e.g. the forwarded token lacks the Genie scope); a timeout is deliberately
    NOT retried on the SP — both identities hit the same space/warehouse, so it would only double
    the wait (see genie_ask)."""
    from databricks.sdk import WorkspaceClient
    out = []
    tok = request.headers.get("x-forwarded-access-token")
    host = os.environ.get("DATABRICKS_HOST")
    if tok and host:
        try:
            # auth_type="pat" forces the SDK to use the forwarded user token; without it the app
            # SP's ambient OAuth-M2M env (DATABRICKS_CLIENT_ID/SECRET) collides with the token and
            # the SDK raises "more than one authorization method configured" — which would silently
            # drop OBO and answer everything as the SP.
            out.append(WorkspaceClient(host=host, token=tok, auth_type="pat"))
        except Exception:
            pass
    out.append(WorkspaceClient())          # app SP
    return out

def _genie_payload(w, msg):
    """Flatten a GenieMessage into {answer, sql, columns, rows} for the UI. Rows are best-effort:
    the answer text and generated SQL always surface even if the result fetch fails."""
    answer, sql, columns, rows, truncated = None, None, [], [], False
    for att in (getattr(msg, "attachments", None) or []):
        t = getattr(att, "text", None)
        if t is not None and getattr(t, "content", None):
            answer = t.content
        query = getattr(att, "query", None)
        if query is not None:
            sql = getattr(query, "query", None) or sql
            if not answer and getattr(query, "description", None):
                answer = query.description
            try:
                res = w.genie.get_message_attachment_query_result(
                    GENIE_SPACE_ID, msg.conversation_id, msg.id, att.attachment_id)
                sr = getattr(res, "statement_response", None)
                if sr and sr.manifest and sr.manifest.schema:
                    columns = [c.name for c in sr.manifest.schema.columns]
                if sr and sr.result and sr.result.data_array:
                    data = sr.result.data_array
                    rows = data[:200]
                    # data_array is only the first chunk; trust the manifest's total when present
                    total = getattr(sr.manifest, "total_row_count", None) if sr and sr.manifest else None
                    truncated = (total is not None and total > len(rows)) or len(data) > len(rows)
            except Exception:
                pass
    return {"answer": answer, "sql": sql, "columns": columns, "rows": rows,
            "truncated": truncated, "conversation_id": getattr(msg, "conversation_id", None),
            "message_id": getattr(msg, "id", None)}

@app.post("/api/genie/ask")
def genie_ask(request: Request, body: dict = Body(...)):
    if not GENIE_SPACE_ID:
        raise HTTPException(503, "Genie space not configured. Run src/ops/02_create_genie_space.py "
                                 "and set GENIE_SPACE_ID in app.yaml.")
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "Ask a question about the wafer population, field returns, or shipments.")
    conversation_id = body.get("conversation_id")
    last_err = None
    for w in _genie_clients(request):
        try:
            if conversation_id:
                msg = w.genie.create_message_and_wait(GENIE_SPACE_ID, conversation_id, question, timeout=GENIE_TIMEOUT)
            else:
                msg = w.genie.start_conversation_and_wait(GENIE_SPACE_ID, question, timeout=GENIE_TIMEOUT)
            return _genie_payload(w, msg)
        except TimeoutError:
            # short-circuit: the SP fallback hits the same space/warehouse, so retrying won't help
            raise HTTPException(504, "Genie is taking longer than usual to answer — try a narrower question or retry in a moment.")
        except Exception as e:
            last_err = e
    raise HTTPException(502, f"Genie query failed: {str(last_err)[:300]}")

# ----------------------------------------------------------------- reset demo
SEED_SCHEMA = "trace_ops_seed"
# child-first for delete, parent-first for insert (FK-safe)
_RESET_ORDER = ["workflow_events", "approval_actions", "recommendations", "predictions",
                "online_features", "wafer_population", "case_genealogy", "wafer_die_map",
                "correlation_results", "engineering_cases"]
_SERIAL = {"recommendations": "rec_id", "approval_actions": "action_id",
           "workflow_events": "event_id", "case_genealogy": "id"}

@app.post("/api/reset")
def reset_demo():
    """Restore the Lakebase operational store to its pristine seeded state from trace_ops_seed."""
    with _connect() as c:
        with c.cursor() as cur:
            for t in _RESET_ORDER:                       # delete children first
                cur.execute(f"DELETE FROM {SCHEMA}.{t}")
            for t in reversed(_RESET_ORDER):             # insert parents first
                cur.execute(f"INSERT INTO {SCHEMA}.{t} SELECT * FROM {SEED_SCHEMA}.{t}")
            for t, col in _SERIAL.items():               # advance sequences past restored ids
                cur.execute(f"SELECT setval(pg_get_serial_sequence('{SCHEMA}.{t}','{col}'), "
                            f"COALESCE((SELECT MAX({col}) FROM {SCHEMA}.{t}), 1))")
            c.commit()
    return summary()

@app.post("/api/cases/{case_id}/dispose")
def mrb_dispose(case_id: str, body: dict = Body(...)):
    role = body.get("role"); disposition = body.get("disposition", "scrap"); rationale = body.get("rationale", "")
    if role != "MRB":
        raise HTTPException(403, "Only the Material Review Board can record a final disposition.")
    c = q("SELECT * FROM engineering_cases WHERE case_id=%s", (case_id,), one=True)
    rec = q("SELECT * FROM recommendations WHERE case_id=%s ORDER BY created_ts DESC LIMIT 1", (case_id,), one=True)
    q("""INSERT INTO approval_actions (rec_id,case_id,approver,approver_role,authority_level,decision,rationale)
         VALUES (%s,%s,%s,%s,'mrb_disposition',%s,%s)""",
      (rec["rec_id"] if rec else None, case_id, ROLES["MRB"]["who"], "Material Review Board", disposition, rationale), write=True)
    q("UPDATE engineering_cases SET state='Closed', updated_ts=now() WHERE case_id=%s", (case_id,), write=True)
    q("""INSERT INTO workflow_events (case_id,event_type,from_state,to_state,actor,actor_role,detail)
         VALUES (%s,'mrb_disposition',%s,'Closed',%s,'Material Review Board',%s)""",
      (case_id, c["state"], ROLES["MRB"]["who"], f"MRB disposition: {disposition}. {rationale}"), write=True)
    return {"state": "Closed", "disposition": disposition}

# ----------------------------------------------------------------- static SPA
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/{path:path}")
    def spa(path: str):
        f = os.path.join(STATIC_DIR, path)
        return FileResponse(f) if os.path.isfile(f) else FileResponse(os.path.join(STATIC_DIR, "index.html"))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", 8000)))
