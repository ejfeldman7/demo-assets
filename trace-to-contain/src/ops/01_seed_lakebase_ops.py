# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 2a — seed Lakebase operational store from the lakehouse
# MAGIC Creates schema `trace_ops` + PRD ops tables on the `app` branch and seeds
# MAGIC decision-relevant state: 400 RMA-derived cases, their genealogy path, sibling-wafer
# MAGIC population stats, per-die predictions, and an agent-generated pending recommendation
# MAGIC (tiered containment). Only decision-relevant slices land in Lakebase (thousands of rows),
# MAGIC not raw FAB history (billions) — the PRD split.

# COMMAND ----------
dbutils.widgets.text("pg_host", "")
dbutils.widgets.text("pg_token", "")
dbutils.widgets.text("pg_user", "")   # defaults to the notebook runner (current_user)
PG_HOST = dbutils.widgets.get("pg_host"); PG_TOKEN = dbutils.widgets.get("pg_token")
PG_USER = dbutils.widgets.get("pg_user") or spark.sql("SELECT current_user()").first()[0]
SCHEMA = "trace_ops"
FQ = "trace_to_contain.lakehouse"

# COMMAND ----------
import json, psycopg
from pyspark.sql import functions as F

conn = psycopg.connect(host=PG_HOST, user=PG_USER, password=PG_TOKEN,
                       dbname="databricks_postgres", sslmode="require", connect_timeout=60, autocommit=True)
cur = conn.cursor()
print("connected to Lakebase")

# COMMAND ----------
# MAGIC %md
# MAGIC ## DDL — ops tables (state machine, audit, evidence)

# COMMAND ----------
cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
cur.execute(f"SET search_path TO {SCHEMA}")
DDL = f"""
DROP TABLE IF EXISTS {SCHEMA}.workflow_events, {SCHEMA}.approval_actions, {SCHEMA}.recommendations,
                     {SCHEMA}.predictions, {SCHEMA}.online_features, {SCHEMA}.case_genealogy,
                     {SCHEMA}.wafer_population, {SCHEMA}.correlation_results, {SCHEMA}.engineering_cases CASCADE;

CREATE TABLE {SCHEMA}.engineering_cases (
  case_id text PRIMARY KEY, rma_id text, module_sn text, product text, customer text,
  failure_mode text, is_nff boolean, severity text, priority int,
  wafer_id text, lot_id text, dep_tool text, is_excursion_lineage boolean,
  state text NOT NULL DEFAULT 'New', assigned_role text, received_ts timestamptz,
  sla_due_ts timestamptz, created_ts timestamptz DEFAULT now(), updated_ts timestamptz DEFAULT now());

CREATE TABLE {SCHEMA}.online_features (
  case_id text PRIMARY KEY REFERENCES {SCHEMA}.engineering_cases(case_id),
  die_id text, fc_meas double precision, il_meas double precision, return_loss_db double precision,
  q_factor double precision, bw_mhz double precision, rejection_db double precision,
  radius double precision, site int, feature_as_of timestamptz);

CREATE TABLE {SCHEMA}.predictions (
  case_id text PRIMARY KEY REFERENCES {SCHEMA}.engineering_cases(case_id),
  die_id text, pred_ft_margin_db double precision, pred_escape_risk boolean,
  actual_ft_margin_db double precision, spec_limit double precision, guardband_limit double precision,
  model_version text, scored_as_of timestamptz);

CREATE TABLE {SCHEMA}.wafer_population (
  case_id text PRIMARY KEY REFERENCES {SCHEMA}.engineering_cases(case_id),
  wafer_id text, pop_grain text, n_population int, n_at_risk int, at_risk_rate double precision,
  mean_pred_margin double precision, worst_pred_margin double precision);

CREATE TABLE {SCHEMA}.case_genealogy (
  id bigserial PRIMARY KEY, case_id text REFERENCES {SCHEMA}.engineering_cases(case_id),
  hop int, level text, node_key text, trace_grain text, trace_confidence double precision, detail text);

CREATE TABLE {SCHEMA}.recommendations (
  rec_id bigserial PRIMARY KEY, case_id text REFERENCES {SCHEMA}.engineering_cases(case_id),
  action text, tier int, rationale text, evidence jsonb, correlation_refs jsonb,
  predicted_impact text, confidence double precision, rejected_alternatives jsonb,
  status text NOT NULL DEFAULT 'pending', model_version text,
  created_by text DEFAULT 'trace-agent', created_ts timestamptz DEFAULT now());

CREATE TABLE {SCHEMA}.approval_actions (
  action_id bigserial PRIMARY KEY, rec_id bigint REFERENCES {SCHEMA}.recommendations(rec_id),
  case_id text, approver text, approver_role text, authority_level text,
  decision text, edited_action text, rationale text, action_ts timestamptz DEFAULT now());

CREATE TABLE {SCHEMA}.workflow_events (
  event_id bigserial PRIMARY KEY, case_id text, event_type text, from_state text, to_state text,
  actor text, actor_role text, detail text, mes_instruction text, event_ts timestamptz DEFAULT now());

CREATE TABLE {SCHEMA}.correlation_results (
  param_id text, method text, corr double precision, n bigint, PRIMARY KEY (param_id, method));

CREATE INDEX ix_cases_state ON {SCHEMA}.engineering_cases(state);
CREATE INDEX ix_cases_priority ON {SCHEMA}.engineering_cases(priority DESC);
CREATE INDEX ix_rec_case ON {SCHEMA}.recommendations(case_id);
CREATE INDEX ix_events_case ON {SCHEMA}.workflow_events(case_id, event_ts);
"""
cur.execute(DDL)
print("DDL applied")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Assemble case rows from the lakehouse (RMA -> module -> lineage -> prediction)

# COMMAND ----------
SEV_BY_CUST = {"Automotive T1": ("Critical", 4), "Base Station T1": ("High", 3),
               "Handset OEM A": ("Medium", 2), "Handset OEM B": ("Medium", 2), "IoT Module Co": ("Low", 1)}

rma = spark.table(f"{FQ}.rma_history")
mod = spark.table(f"{FQ}.module").select("module_sn","die_id","product")
be  = spark.table(f"{FQ}.back_end_outcomes").select("die_id", F.col("ft_margin_db").alias("actual_margin"))
pred= spark.table(f"{FQ}.back_end_prediction").select("die_id","pred_ft_margin_db","pred_escape_risk","model_version","wafer_id","lot_id")
die = spark.table(f"{FQ}.die").select("die_id","dep_tool","is_excursion","radius","site")
feat= spark.table(f"{FQ}.die_features").select("die_id","fc_meas","il_meas","return_loss_db","q_factor","bw_mhz","rejection_db")

cases = (rma.join(mod, "module_sn")
    .join(die, "die_id", "left").join(pred, "die_id", "left").join(be, "die_id", "left").join(feat, "die_id", "left")
    .withColumn("case_id", F.concat(F.lit("CASE-"), F.col("rma_id"))))

# wafer-level population (siblings on same wafer): n, n_at_risk, mean/worst predicted margin
popw = (pred.groupBy("wafer_id").agg(
    F.count("*").alias("n_population"),
    F.sum(F.col("pred_escape_risk").cast("int")).alias("n_at_risk"),
    F.avg("pred_ft_margin_db").alias("mean_pred_margin"),
    F.min("pred_ft_margin_db").alias("worst_pred_margin")))

case_rows = (cases.join(popw, "wafer_id", "left")).collect()
print("cases assembled:", len(case_rows))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Deterministic tiered-containment recommendation engine (reused in the app backend)

# COMMAND ----------
TIERS = ["No action","Sample screen","100% screen","Partial wafer hold","Full lot hold"]
def recommend(is_nff, at_risk_rate, n_pop, mean_margin, is_exc, severity, priority):
    if is_nff or (at_risk_rate is not None and at_risk_rate < 0.02 and not is_exc):
        tier = 0
    elif at_risk_rate is None:
        tier = 1
    elif at_risk_rate < 0.10: tier = 1
    elif at_risk_rate < 0.25: tier = 2
    elif at_risk_rate < 0.45: tier = 3
    else: tier = 4
    if priority and priority >= 3 and tier > 0 and tier < 4:   # escalate one tier for Critical/High customers
        tier += 1
    tier = min(tier, 4)
    conf = 0.55
    if n_pop: conf = min(0.97, 0.6 + (n_pop/400.0)*0.3 + (0.1 if is_exc else 0))
    ar = f"{100*at_risk_rate:.1f}%" if at_risk_rate is not None else "n/a"
    rationale = (f"No fault found on the returned unit and the sibling wafer population is within guardband — "
                 f"analysis only, no containment." if tier == 0 else
                 f"{ar} of the {n_pop or 0} sibling die on this wafer are predicted below the guardband "
                 f"(mean predicted margin {mean_margin:.2f} dB). "
                 f"{'Excursion lineage (DEP-02) confirmed. ' if is_exc else ''}"
                 f"Recommending the least-disruptive effective containment: {TIERS[tier]}"
                 + (f" (escalated one tier for {severity} customer)." if priority and priority>=3 and 0<tier<=4 else "."))
    impact = ("No units held." if tier == 0 else
              f"~{int((at_risk_rate or 0)*(n_pop or 0))} at-risk die contained before module assembly.")
    # rejected alternatives: adjacent tiers with why-not
    rej = []
    if tier > 0: rej.append({"action": TIERS[tier-1], "why_not": "Under-contains the predicted at-risk population; escape risk to customer."})
    if tier < 4: rej.append({"action": TIERS[tier+1], "why_not": "Over-contains healthy material; unnecessary scrap/expedite cost."})
    return tier, rationale, impact, round(conf,2), rej

# COMMAND ----------
# MAGIC %md
# MAGIC ## Insert cases, features, predictions, population, genealogy, recommendations, events

# COMMAND ----------
import random
random.seed(7)
# a realistic spread of states so the queue/metrics look alive
STATE_MIX = (["New"]*140 + ["Under FA Review"]*90 + ["Recommendation Pending"]*90 +
             ["MES Hold Applied"]*30 + ["MRB Disposition"]*20 + ["Closed"]*30)
n_rec = 0; n_evt = 0
for i, r in enumerate(case_rows):
    sev, prio = SEV_BY_CUST.get(r["customer"], ("Medium", 2))
    at_risk_rate = (r["n_at_risk"]/r["n_population"]) if r["n_population"] else None
    state = STATE_MIX[i % len(STATE_MIX)]
    sla_hours = {4:24, 3:48, 2:72, 1:120}[prio]
    cur.execute(f"""INSERT INTO {SCHEMA}.engineering_cases
      (case_id,rma_id,module_sn,product,customer,failure_mode,is_nff,severity,priority,wafer_id,lot_id,
       dep_tool,is_excursion_lineage,state,assigned_role,received_ts,sla_due_ts)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, %s::timestamptz + make_interval(hours => %s))""",
      (r["case_id"], r["rma_id"], r["module_sn"], r["product"], r["customer"], r["failure_mode"], r["is_nff"],
       sev, prio, r["wafer_id"], r["lot_id"], r["dep_tool"], bool(r["is_excursion"]), state, "FA Engineer",
       r["received_ts"], r["received_ts"], sla_hours))

    cur.execute(f"""INSERT INTO {SCHEMA}.online_features
      (case_id,die_id,fc_meas,il_meas,return_loss_db,q_factor,bw_mhz,rejection_db,radius,site,feature_as_of)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())""",
      (r["case_id"], r["die_id"], r["fc_meas"], r["il_meas"], r["return_loss_db"], r["q_factor"], r["bw_mhz"],
       r["rejection_db"], r["radius"], r["site"]))

    cur.execute(f"""INSERT INTO {SCHEMA}.predictions
      (case_id,die_id,pred_ft_margin_db,pred_escape_risk,actual_ft_margin_db,spec_limit,guardband_limit,model_version,scored_as_of)
      VALUES (%s,%s,%s,%s,%s,0.0,0.5,%s, now())""",
      (r["case_id"], r["die_id"], r["pred_ft_margin_db"], bool(r["pred_escape_risk"]) if r["pred_escape_risk"] is not None else None,
       r["actual_margin"], r["model_version"]))

    cur.execute(f"""INSERT INTO {SCHEMA}.wafer_population
      (case_id,wafer_id,pop_grain,n_population,n_at_risk,at_risk_rate,mean_pred_margin,worst_pred_margin)
      VALUES (%s,%s,'wafer',%s,%s,%s,%s,%s)""",
      (r["case_id"], r["wafer_id"], r["n_population"], r["n_at_risk"], at_risk_rate,
       r["mean_pred_margin"], r["worst_pred_margin"]))

    # genealogy path (module -> die -> wafer -> lot); BAW die-grain, high confidence
    grain, conf = ("die", 0.97)
    hops = [("module", r["module_sn"], "Assembled RF filter module"),
            ("die", r["die_id"], f"Die at wafer site {r['site']}, radius {r['radius']:.2f}" if r["radius"] is not None else "Die"),
            ("wafer", r["wafer_id"], f"Wafer on {r['dep_tool']}"),
            ("lot", r["lot_id"], "Production lot")]
    for h,(lvl,key,det) in enumerate(hops):
        cur.execute(f"""INSERT INTO {SCHEMA}.case_genealogy (case_id,hop,level,node_key,trace_grain,trace_confidence,detail)
          VALUES (%s,%s,%s,%s,%s,%s,%s)""", (r["case_id"], h, lvl, key, grain if lvl in ("die","wafer") else "lot", conf, det))

    # recommendation (pending) — for cases that have reached review or beyond
    if state in ("Recommendation Pending","MES Hold Applied","MRB Disposition","Closed"):
        tier, rationale, impact, conf_r, rej = recommend(r["is_nff"], at_risk_rate, r["n_population"],
                                                          r["mean_pred_margin"] or 0.0, bool(r["is_excursion"]), sev, prio)
        evidence = {"predicted_margin_db": round(r["pred_ft_margin_db"],3) if r["pred_ft_margin_db"] is not None else None,
                    "n_population": r["n_population"], "n_at_risk": r["n_at_risk"],
                    "at_risk_rate": round(at_risk_rate,3) if at_risk_rate is not None else None,
                    "trace_grain": grain, "trace_confidence": conf, "excursion_lineage": bool(r["is_excursion"])}
        corr_refs = {"top_param": "fc_meas", "within_site_corr": 0.59, "raw_pooled_corr": 0.538,
                     "note": "il_meas raw -0.41 collapses to -0.17 within week (probe-card drift artifact)"}
        rec_status = "pending" if state == "Recommendation Pending" else ("approved" if state in ("MES Hold Applied","MRB Disposition","Closed") else "pending")
        cur.execute(f"""INSERT INTO {SCHEMA}.recommendations
          (case_id,action,tier,rationale,evidence,correlation_refs,predicted_impact,confidence,rejected_alternatives,status,model_version)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING rec_id""",
          (r["case_id"], TIERS[tier], tier, rationale, json.dumps(evidence), json.dumps(corr_refs), impact,
           conf_r, json.dumps(rej), rec_status, r["model_version"]))
        rec_id = cur.fetchone()[0]; n_rec += 1
        # approval + downstream events for cases past the gate
        if state in ("MES Hold Applied","MRB Disposition","Closed"):
            cur.execute(f"""INSERT INTO {SCHEMA}.approval_actions
              (rec_id,case_id,approver,approver_role,authority_level,decision,rationale)
              VALUES (%s,%s,%s,%s,%s,'approved',%s)""",
              (rec_id, r["case_id"], "q.rivera@example.com", "Quality Engineer", "lot_hold",
               "Evidence supports containment; excursion lineage confirmed."))
            cur.execute(f"""INSERT INTO {SCHEMA}.workflow_events (case_id,event_type,from_state,to_state,actor,actor_role,detail,mes_instruction)
              VALUES (%s,'containment_applied','Recommendation Pending','MES Hold Applied','q.rivera@example.com','Quality Engineer',%s,%s)""",
              (r["case_id"], f"{TIERS[tier]} approved", f"HOLD wafer {r['wafer_id']} :: instruction issued to MES"))
            n_evt += 1
    # case-open event for every case
    cur.execute(f"""INSERT INTO {SCHEMA}.workflow_events (case_id,event_type,from_state,to_state,actor,actor_role,detail)
      VALUES (%s,'case_opened',NULL,'New','system','system',%s)""",
      (r["case_id"], f"RMA {r['rma_id']} pushed from QMS ({r['customer']})"))
    n_evt += 1

# correlation snapshot for the app
for row in spark.table(f"{FQ}.correlation_results").collect():
    cur.execute(f"""INSERT INTO {SCHEMA}.correlation_results (param_id,method,corr,n)
      VALUES (%s,%s,%s,%s) ON CONFLICT (param_id,method) DO UPDATE SET corr=EXCLUDED.corr, n=EXCLUDED.n""",
      (row["param_id"], row["method"], row["corr"], row["n"]))

# ---- wafer_die_map: per-die predicted margin for the wafers referenced by cases (spatial view)
cur.execute(f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.wafer_die_map (
  wafer_id text, die_x int, die_y int, radius double precision,
  pred_ft_margin_db double precision, pred_escape_risk boolean, is_case_die boolean DEFAULT false,
  PRIMARY KEY (wafer_id, die_x, die_y))""")
cur.execute(f"TRUNCATE {SCHEMA}.wafer_die_map")
case_wafers = [r["wafer_id"] for r in spark.table(f"{FQ}.rma_history").select("wafer_id").distinct().collect()]
case_dies = set(r["die_id"] for r in spark.table(f"{FQ}.rma_history").join(
    spark.table(f"{FQ}.module").select("module_sn","die_id"), "module_sn").select("die_id").collect())
wdm = (spark.table(f"{FQ}.back_end_prediction")
       .join(spark.table(f"{FQ}.die").select("die_id","die_x","die_y","radius"), "die_id")
       .filter(F.col("wafer_id").isin(case_wafers))
       .select("die_id","wafer_id","die_x","die_y","radius","pred_ft_margin_db","pred_escape_risk").collect())
for r in wdm:
    cur.execute(f"""INSERT INTO {SCHEMA}.wafer_die_map (wafer_id,die_x,die_y,radius,pred_ft_margin_db,pred_escape_risk,is_case_die)
      VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (wafer_id,die_x,die_y) DO NOTHING""",
      (r["wafer_id"], r["die_x"], r["die_y"], r["radius"], r["pred_ft_margin_db"],
       bool(r["pred_escape_risk"]) if r["pred_escape_risk"] is not None else None, r["die_id"] in case_dies))
conn.commit()

# ---- pristine snapshot schema for the Reset Demo button (read-only copy of trace_ops)
SEED = "trace_ops_seed"
cur.execute(f"DROP SCHEMA IF EXISTS {SEED} CASCADE")
cur.execute(f"CREATE SCHEMA {SEED}")
_snap_tables = ["engineering_cases","online_features","predictions","wafer_population","case_genealogy",
                "recommendations","approval_actions","workflow_events","correlation_results","wafer_die_map"]
for t in _snap_tables:
    cur.execute(f"CREATE TABLE {SEED}.{t} (LIKE {SCHEMA}.{t} INCLUDING DEFAULTS)")
    cur.execute(f"INSERT INTO {SEED}.{t} SELECT * FROM {SCHEMA}.{t}")
conn.commit()
print(f"seed snapshot {SEED} created with {len(_snap_tables)} tables")

counts = {}
for t in ["engineering_cases","online_features","predictions","wafer_population","case_genealogy","recommendations","approval_actions","workflow_events","correlation_results"]:
    cur.execute(f"SELECT count(*) FROM {SCHEMA}.{t}"); counts[t] = cur.fetchone()[0]
cur.close(); conn.close()
print(json.dumps(counts, indent=2))
dbutils.notebook.exit(json.dumps({"tables": counts, "recommendations": n_rec, "events": n_evt}))
