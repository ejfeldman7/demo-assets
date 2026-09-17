"""
Trace to Contain — create the curated **population Q&A** Genie space.

This space sits over the analytical lakehouse and answers the open-ended, population- and
portfolio-level questions the guided case app can't pre-answer: blast radius ("how many wafers
on DEP-02 in the excursion window?"), shipment exposure ("how many sibling die already shipped
in modules?"), and cross-case patterns ("which lots have the highest field-return rate?"). The
Case Queue drills into one return; this steps back to the whole population. Paste the printed
space id into app/app.yaml (GENIE_SPACE_ID).

Curation follows the databricks-solutions/databricks-genie-workbench IQ-Scanner checklist:
table/column descriptions (set as Unity Catalog COMMENTs here), a single multi-section
instruction block, join specs, example question->SQL pairs, and a >=10-question benchmark set.
The simulator's ground-truth latent columns (true_*, severity, wafer_fc_offset, die_uid,
fc_param_id) are excluded from column_configs and explicitly forbidden in the instructions, so
Genie answers from observable data, not the answer key (integrity + noise reduction).

The `serialized_space` here matches the platform's **version 2** schema (verified against a live
`genie get-space --include-serialized-space`). Runs two ways:
  • as a Databricks notebook/job (uses dbutils widgets), or
  • locally:  python 02_create_genie_space.py --profile <profile> [--catalog trace_to_contain]
"""
import json

# ---------------------------------------------------------------- params (notebook or CLI)
try:
    dbutils  # noqa: F821  (defined in the notebook runtime)
    _p = lambda k, d: (dbutils.widgets.get(k) or d)  # noqa: E731
    for _k, _d in [("catalog", "trace_to_contain"), ("schema", "lakehouse"),
                   ("warehouse_id", "7a9ff721ec35d76f"),
                   ("parent_path", "/Shared/trace-to-contain"),
                   ("title", "Trace to Contain — Population Explorer")]:
        dbutils.widgets.text(_k, _d)
    CAT, SCH = _p("catalog", "trace_to_contain"), _p("schema", "lakehouse")
    WAREHOUSE_ID = _p("warehouse_id", "7a9ff721ec35d76f")
    PARENT_PATH, TITLE = _p("parent_path", "/Shared/trace-to-contain"), _p("title", "Trace to Contain — Population Explorer")
    PROFILE = None
except NameError:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile")
    ap.add_argument("--catalog", default="trace_to_contain")   # live analytical catalog
    ap.add_argument("--schema", default="lakehouse")
    ap.add_argument("--warehouse-id", default="7a9ff721ec35d76f")
    ap.add_argument("--parent-path", default="/Shared/trace-to-contain")
    ap.add_argument("--title", default="Trace to Contain — Population Explorer")
    a = ap.parse_args()
    CAT, SCH, WAREHOUSE_ID = a.catalog, a.schema, a.warehouse_id
    PARENT_PATH, TITLE, PROFILE = a.parent_path, a.title, a.profile

FQ = f"{CAT}.{SCH}"

# ---------------------------------------------------------------- Unity Catalog descriptions
# Genie reads table/column descriptions from UC COMMENTs (not from serialized_space), so set them
# here. Best-effort: creation still proceeds if the warehouse can't run these.
TABLE_COMMENTS = {
    "rma_history": "Field returns (RMAs): one returned module per row, the trigger for a containment case. "
                   "~240 real excursion-linked returns plus ~160 no-fault-found (is_nff=true). Trace to wafer/lot.",
    "back_end_outcomes": "Final-test results per module. ft_margin_db is the key outcome: >0.5 dB passes guardband, "
                         "0 to 0.5 dB is an at-risk escape (escape_risk), <=0 fails spec.",
    "die": "Per-die front-end record for BAW filters (~1.1M), with wafer position (die_x, die_y, radius), ATE site, "
           "and lineage (wafer/lot/tool/week). The excursion signature is radial, edge-worst.",
    "wafer": "Wafer master (25 wafers/lot): resolves a wafer to its lot, deposition tool, fab start week, product, "
             "and excursion flag.",
    "lot": "Lot master (180 lots): lot to deposition tool, fab, fab start week, product, and test-program rev.",
    "die_to_module": "Genealogy bridge module->die/wafer. trace_grain is 'die' (BAW, high confidence) or 'wafer' "
                     "(FEM, lower confidence). Use to go from a wafer population to the modules that shipped.",
    "module": "Assembled/shipped modules. Join to back_end_outcomes for test results and rma_history for returns.",
}
COLUMN_COMMENTS = {
    "back_end_outcomes": {"ft_margin_db": "Final-test insertion-loss margin (dB); higher is healthier.",
                          "escape_risk": "True when 0 < ft_margin_db <= 0.5: passed spec but at risk (a field escape)."},
    "die": {"is_excursion": "True if on excursion lineage (DEP-02, weeks 25-34).",
            "dep_tool": "Deposition tool (DEP-01/02/03); DEP-02 is the excursion tool.",
            "radius": "Normalized distance from wafer center (0 center, 1 edge)."},
    "wafer": {"is_excursion": "True for DEP-02 wafers started in weeks 25-34.", "dep_tool": "Deposition tool."},
    "rma_history": {"is_nff": "True when retest found no fault (a non-excursion return).",
                    "customer": "Customer that returned the unit."},
}

# ---------------------------------------------------------------- data sources (7 tables, v2 shape)
# column_configs carry entity/format-matching toggles (matched columns for NL grounding).
def tbl(name, entity=(), fmt=()):
    cfgs = []
    for c in sorted(set(entity) | set(fmt)):
        cfg = {"column_name": c}
        if c in entity:
            cfg["enable_entity_matching"] = True
        if c in fmt:
            cfg["enable_format_assistance"] = True
        cfgs.append(cfg)
    return {"identifier": f"{FQ}.{name}", "column_configs": cfgs}

TABLES = [
    tbl("rma_history", entity=("customer", "failure_mode")),
    tbl("back_end_outcomes", entity=("product",)),
    tbl("die", entity=("dep_tool",)),
    tbl("wafer", entity=("dep_tool", "product")),
    tbl("lot", entity=("dep_tool", "fab", "product")),
    tbl("die_to_module", entity=("trace_grain",)),
    tbl("module", entity=("product",)),
]

# ---------------------------------------------------------------- instructions (single block, sectioned)
INSTRUCTION_LINES = [
    "## PURPOSE",
    "Answer population- and portfolio-level questions about the FAB-to-field flow for RF filters: wafers and lots "
    "by deposition tool and fab week, final-test margin and escapes, field returns (RMAs), and the die/module "
    "genealogy connecting them. This complements the case app, which handles one return at a time.",
    "## DISAMBIGUATION",
    "- 'The excursion' / 'excursion window' = deposition tool DEP-02, fab start_week between 25 and 34. Prefer the "
    "is_excursion flag on die/wafer; otherwise dep_tool='DEP-02' AND start_week BETWEEN 25 AND 34.",
    "- 'Escape' / 'at risk' = back_end_outcomes.escape_risk (0 < ft_margin_db <= 0.5). 'Fail' = ft_margin_db <= 0.",
    "- 'Field return' / 'RMA' = a row in rma_history; a 'real' return has is_nff=false, 'no fault found' is is_nff=true.",
    "- dep_tool and start_week live on lot/wafer/die, NOT on back_end_outcomes/module/rma_history — join via wafer_id or lot_id.",
    "## DATA QUALITY NOTES",
    "- Never use true_fc_shift_mhz, true_il_excess_db, severity, wafer_fc_offset, die_uid, or fc_param_id: they are "
    "hidden simulator ground truth, not observable fab data.",
    "- FEM modules have die_id=null and trace_grain='wafer' (wafer-level lineage only); BAW is die-level.",
    "- fab_measurements is intentionally out of scope for this space; reason over die/wafer/lot aggregates instead.",
    "## CONSTRAINTS",
    "- Aggregate by default (counts, rates, averages by tool/lot/wafer/customer/week); never return raw million-row die scans.",
    "- A 'rate' is a fraction of a stated denominator (e.g. real returns / modules shipped).",
    "## Instructions you must follow when providing summaries",
    "State the population/filter you applied (tool, week window, product), give the number with its denominator, and "
    "call out when a result is driven by the DEP-02 excursion lineage.",
]

# ---------------------------------------------------------------- example question -> SQL
EXAMPLES = [
    {"id": "ex01", "question": ["How many wafers ran on DEP-02 during the excursion window (weeks 25-34)?"],
     "sql": [f"SELECT count(*) AS wafers FROM {FQ}.wafer WHERE dep_tool='DEP-02' AND start_week BETWEEN 25 AND 34"],
     "usage_guidance": ["Blast radius by tool + fab week. Use wafer/lot for tool/week, not back-end tables."]},
    {"id": "ex02", "question": ["Which lots have the highest real field-return rate?"],
     "sql": [f"SELECT m.lot_id, count(r.rma_id) AS returns, count(r.rma_id)*1.0/count(DISTINCT m.module_sn) AS return_rate "
             f"FROM {FQ}.module m LEFT JOIN {FQ}.rma_history r ON r.module_sn=m.module_sn AND r.is_nff=false "
             f"GROUP BY m.lot_id ORDER BY return_rate DESC LIMIT 20"],
     "usage_guidance": ["Cross-lot pattern; exclude no-fault-found; a rate needs a denominator."]},
    {"id": "ex03", "question": ["How many modules from excursion wafers already shipped?"],
     "sql": [f"SELECT count(*) AS shipped_from_excursion FROM {FQ}.module m JOIN {FQ}.wafer w ON w.wafer_id=m.wafer_id "
             f"WHERE w.is_excursion=true"],
     "usage_guidance": ["Shipment exposure via genealogy (module -> wafer)."]},
    {"id": "ex04", "question": ["Average final-test margin by deposition tool."],
     "sql": [f"SELECT w.dep_tool, avg(b.ft_margin_db) AS avg_margin_db FROM {FQ}.back_end_outcomes b "
             f"JOIN {FQ}.wafer w ON w.wafer_id=b.wafer_id GROUP BY w.dep_tool ORDER BY avg_margin_db"],
     "usage_guidance": ["dep_tool comes from wafer; join back_end_outcomes -> wafer on wafer_id."]},
]

# ---------------------------------------------------------------- joins (v2 shape: left/right {identifier,alias},
# sql = [backtick-quoted ON condition, "--rt=FROM_RELATIONSHIP_TYPE_<TYPE>--"])
_RT = "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--"
def join(jid, lt, la, rt, ra, cond, comment):
    return {"id": jid, "left": {"identifier": f"{FQ}.{lt}", "alias": la},
            "right": {"identifier": f"{FQ}.{rt}", "alias": ra}, "sql": [cond, _RT], "comment": [comment]}

JOINS = [
    join("j01", "back_end_outcomes", "b", "wafer", "w", "`b`.`wafer_id` = `w`.`wafer_id`", "Final-test outcome to its wafer (tool/week)."),
    join("j02", "rma_history", "r", "module", "m", "`r`.`module_sn` = `m`.`module_sn`", "Field return to the shipped module."),
    join("j03", "module", "m", "wafer", "w", "`m`.`wafer_id` = `w`.`wafer_id`", "Shipped module to its source wafer."),
    join("j04", "wafer", "w", "lot", "l", "`w`.`lot_id` = `l`.`lot_id`", "Wafer to its lot."),
    join("j05", "die", "d", "wafer", "w", "`d`.`wafer_id` = `w`.`wafer_id`", "Die to its wafer."),
    join("j06", "die_to_module", "g", "module", "m", "`g`.`module_sn` = `m`.`module_sn`", "Genealogy bridge to the module."),
]

# ---------------------------------------------------------------- benchmarks (>=10, answer as [{format,content}])
BENCH = [
    ("bm01", "How many wafers ran on DEP-02 in weeks 25 to 34?",
     f"SELECT count(*) FROM {FQ}.wafer WHERE dep_tool='DEP-02' AND start_week BETWEEN 25 AND 34"),
    ("bm02", "How many field returns are real versus no-fault-found?",
     f"SELECT is_nff, count(*) FROM {FQ}.rma_history GROUP BY is_nff"),
    ("bm03", "What is the overall escape rate at final test?",
     f"SELECT avg(CASE WHEN escape_risk THEN 1 ELSE 0 END) FROM {FQ}.back_end_outcomes"),
    ("bm04", "Average final-test margin by deposition tool.",
     f"SELECT w.dep_tool, avg(b.ft_margin_db) FROM {FQ}.back_end_outcomes b JOIN {FQ}.wafer w ON w.wafer_id=b.wafer_id GROUP BY w.dep_tool"),
    ("bm05", "How many modules shipped from excursion wafers?",
     f"SELECT count(*) FROM {FQ}.module m JOIN {FQ}.wafer w ON w.wafer_id=m.wafer_id WHERE w.is_excursion=true"),
    ("bm06", "Which 10 lots have the most real field returns?",
     f"SELECT m.lot_id, count(*) AS returns FROM {FQ}.rma_history r JOIN {FQ}.module m ON m.module_sn=r.module_sn WHERE r.is_nff=false GROUP BY m.lot_id ORDER BY returns DESC LIMIT 10"),
    ("bm07", "How many RMAs came from each customer?",
     f"SELECT customer, count(*) FROM {FQ}.rma_history GROUP BY customer ORDER BY count(*) DESC"),
    ("bm08", "Escape rate on DEP-02 versus other tools.",
     f"SELECT w.dep_tool, avg(CASE WHEN b.escape_risk THEN 1 ELSE 0 END) AS escape_rate FROM {FQ}.back_end_outcomes b JOIN {FQ}.wafer w ON w.wafer_id=b.wafer_id GROUP BY w.dep_tool"),
    ("bm09", "How many die on excursion wafers are at the wafer edge (radius > 0.8)?",
     f"SELECT count(*) FROM {FQ}.die WHERE is_excursion=true AND radius > 0.8"),
    ("bm10", "How many distinct wafers are implicated across all real field returns?",
     f"SELECT count(DISTINCT wafer_id) FROM {FQ}.rma_history WHERE is_nff=false"),
    ("bm11", "Average final-test margin for excursion wafers versus the rest.",
     f"SELECT w.is_excursion, avg(b.ft_margin_db) FROM {FQ}.back_end_outcomes b JOIN {FQ}.wafer w ON w.wafer_id=b.wafer_id GROUP BY w.is_excursion"),
    ("bm12", "How many modules were assembled from FEM wafer-grain lineage versus die-grain?",
     f"SELECT trace_grain, count(*) FROM {FQ}.die_to_module GROUP BY trace_grain"),
]

import uuid
_uid = lambda: uuid.uuid4().hex   # platform requires lowercase 32-hex ids (no hyphens)
for _e in EXAMPLES:
    _e["id"] = _uid()
for _j in JOINS:
    _j["id"] = _uid()

serialized = {
    "version": 2,
    "data_sources": {"tables": sorted(TABLES, key=lambda t: t["identifier"])},
    "instructions": {
        "text_instructions": [{"id": _uid(), "content": INSTRUCTION_LINES}],
        "example_question_sqls": sorted(EXAMPLES, key=lambda e: e["id"]),
        "join_specs": sorted(JOINS, key=lambda j: j["id"]),
    },
    "benchmarks": {"questions": sorted(
        [{"id": _uid(), "question": [q], "answer": [{"format": "SQL", "content": [sql]}]}
         for (_bid, q, sql) in BENCH], key=lambda x: x["id"])},
}
for t in serialized["data_sources"]["tables"]:
    t["column_configs"] = sorted(t["column_configs"], key=lambda c: c["column_name"])

# ---------------------------------------------------------------- create
from databricks.sdk import WorkspaceClient
w = WorkspaceClient(profile=PROFILE) if PROFILE else WorkspaceClient()

# Best-effort UC descriptions (Genie reads these). Never blocks space creation.
try:
    def _sql(stmt):
        w.statement_execution.execute_statement(warehouse_id=WAREHOUSE_ID, statement=stmt, wait_timeout="30s")
    for _t, _c in TABLE_COMMENTS.items():
        _sql(f"COMMENT ON TABLE {FQ}.{_t} IS '{_c.replace(chr(39), chr(39)+chr(39))}'")
    for _t, _cols in COLUMN_COMMENTS.items():
        for _col, _cc in _cols.items():
            _sql(f"ALTER TABLE {FQ}.{_t} ALTER COLUMN {_col} COMMENT '{_cc.replace(chr(39), chr(39)+chr(39))}'")
    print("UC comments applied.")
except Exception as e:
    print("UC comments skipped:", str(e)[:160])

resp = w.api_client.do("POST", "/api/2.0/genie/spaces", body={
    "title": TITLE,
    "description": "Trace to Contain — population & portfolio Q&A over the analytical lakehouse (synthetic data).",
    "parent_path": PARENT_PATH,
    "warehouse_id": WAREHOUSE_ID,
    "serialized_space": json.dumps(serialized),
})
space_id = resp.get("space_id") or resp.get("id")
print("=== Genie space created ===")
print("space_id :", space_id, "| tables:", len(TABLES), "| benchmarks:", len(BENCH))
print("NEXT: set app/app.yaml GENIE_SPACE_ID =", space_id)
print("      grant the app SP CAN RUN on the space + SELECT on the 7 lakehouse tables.")
