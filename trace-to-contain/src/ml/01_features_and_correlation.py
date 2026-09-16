# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 1a — Feature table (offline+online) + confounder-aware correlation
# MAGIC ONE feature definition (`die_features`) used for training and serving. Published to a
# MAGIC Lakebase online store for low-latency scoring. Plus `correlation_results`: raw vs
# MAGIC confounder-stripped correlation (the demo's "condition out the tool/site/time" story).

# COMMAND ----------
import json, math
from pyspark.sql import functions as F
from databricks.feature_engineering import FeatureEngineeringClient

CATALOG, SCHEMA = "trace_to_contain", "lakehouse"
FQ = f"{CATALOG}.{SCHEMA}"
FEATURE_TABLE = f"{FQ}.die_features"
ONLINE_STORE = "trace-online"          # DNS-compliant (no underscores)
ONLINE_TABLE = f"{FQ}.die_features_online"
PARAMS = ["fc_meas","il_meas","return_loss_db","q_factor","bw_mhz","rejection_db","leakage_na","static_cap_pf","temp_coeff_ppm"]
TARGET = "ft_margin_db"
fe = FeatureEngineeringClient()

# COMMAND ----------
# MAGIC %md
# MAGIC ## Shared feature transform (first-pass; Fc name reconciled across program revs)

# COMMAND ----------
def build_die_features(spark):
    meas = spark.table(f"{FQ}.fab_measurements").filter(F.col("insertion") == "FIRST")
    piv = (meas.groupBy("die_id")
           .pivot("param_id", ["center_freq_mhz","fc_center_mhz","il_probe_db","return_loss_db",
                               "q_factor","bw_mhz","rejection_db","leakage_na","static_cap_pf","temp_coeff_ppm"])
           .agg(F.first("value")))
    piv = (piv.withColumn("fc_meas", F.coalesce("center_freq_mhz","fc_center_mhz"))
              .withColumnRenamed("il_probe_db","il_meas")
              .drop("center_freq_mhz","fc_center_mhz"))
    die = spark.table(f"{FQ}.die").select(
        "die_id","radius","site","dep_tool","temp_c","test_program_rev",
        F.col("start_date").cast("timestamp").alias("event_ts"))
    return (piv.join(die, "die_id")
               .select("die_id","event_ts", *PARAMS, "radius","temp_c","site","dep_tool","test_program_rev"))

feat_df = build_die_features(spark)
print("feature rows:", feat_df.count())

# COMMAND ----------
# MAGIC %md
# MAGIC ## Register UC feature table (point-in-time keyed on event_ts)

# COMMAND ----------
spark.sql(f"DROP TABLE IF EXISTS {FEATURE_TABLE}")
fe.create_table(
    name=FEATURE_TABLE,
    primary_keys=["die_id","event_ts"],   # timeseries col must be part of the PK
    timeseries_column="event_ts",     # enables point-in-time FeatureLookup at training time
    schema=feat_df.schema,
    description="Wafer-probe features per BAW die (ONE definition, offline+online). event_ts = wafer-sort time.")
fe.write_table(name=FEATURE_TABLE, df=feat_df, mode="merge")   # table freshly created above; merge = insert
print("feature table written:", spark.table(FEATURE_TABLE).count())

# COMMAND ----------
# MAGIC %md
# MAGIC ## Publish to Lakebase online store (low-latency serving for the app/endpoint)

# COMMAND ----------
# NOTE: online serving is consolidated into the single operational Lakebase project
# (trace-to-contain). The current per-case feature vector is upserted into
# trace_ops.online_features by the Phase-2 seed and read live by the app. We intentionally
# do NOT call fe.create_online_store() here — that provisions a separate managed Lakebase
# project, and we keep the demo to one project.
online_msg = "online features served from trace_ops.online_features (single Lakebase project)"
print(online_msg)

# COMMAND ----------
# MAGIC %md
# MAGIC ## correlation_results — raw (pooled) vs confounder-stripped (within-stratum)

# COMMAND ----------
af = (feat_df.join(spark.table(f"{FQ}.back_end_outcomes").select("die_id", TARGET), "die_id")
             .join(spark.table(f"{FQ}.die").select("die_id","start_week"), "die_id"))
af.write.mode("overwrite").saveAsTable(f"{FQ}._af_corr")
af = spark.table(f"{FQ}._af_corr")

def fisher_agg(per_df, pcol):
    d = per_df.filter(F.col(pcol).isNotNull() & (F.col("n") > 15) & (F.abs(F.col(pcol)) < 0.999))
    z = d.withColumn("z", 0.5*F.log((1+F.col(pcol))/(1-F.col(pcol)))).withColumn("w", F.col("n")-3)
    agg = z.agg((F.sum(F.col("z")*F.col("w"))/F.sum("w")).alias("zbar"), F.sum("n").alias("n")).first()
    if agg is None or agg["zbar"] is None:
        return None, 0
    return math.tanh(agg["zbar"]), int(agg["n"])

rows = []
raw = af.agg(*[F.corr(p, TARGET).alias(p) for p in PARAMS], F.count(F.lit(1)).alias("n")).first()
for p in PARAMS:
    rows.append(("BAW-B7", p, TARGET, "raw_pooled", float(raw[p]) if raw[p] is not None else None, int(raw["n"])))
for method, gcols in [("within_site", ["site"]), ("within_week", ["start_week"]),
                      ("within_stratum", ["site","dep_tool","start_week","test_program_rev"])]:
    per = af.groupBy(*gcols).agg(*[F.corr(p, TARGET).alias(p) for p in PARAMS], F.count(F.lit(1)).alias("n"))
    for p in PARAMS:
        r, n = fisher_agg(per.select(*gcols, p, "n"), p)
        rows.append(("BAW-B7", p, TARGET, method, r, n))

cr = spark.createDataFrame(rows, "product string, param_id string, target string, method string, corr double, n long") \
          .withColumn("computed_as_of", F.current_timestamp())
cr.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(f"{FQ}.correlation_results")
spark.sql(f"DROP TABLE IF EXISTS {FQ}._af_corr")
print("correlation_results written:", cr.count())

dbutils.notebook.exit(json.dumps({"feature_rows": spark.table(FEATURE_TABLE).count(),
                                  "online": online_msg, "correlation_rows": cr.count()}))
