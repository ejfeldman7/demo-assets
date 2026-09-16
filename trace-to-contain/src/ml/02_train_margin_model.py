# Databricks notebook source
# MAGIC %md
# MAGIC # FT-Margin Predictor — reusable Optuna HPO training notebook
# MAGIC Point-in-time FeatureLookup training set → Optuna hyperparameter search (each trial a
# MAGIC nested MLflow run) → `fe.log_model` with feature lineage → register to UC → `score_batch`
# MAGIC using the same feature definition served from Lakebase. Parameterized via widgets so it
# MAGIC re-runs for any product / target / feature table.

# COMMAND ----------
dbutils.widgets.text("catalog", "trace_to_contain")
dbutils.widgets.text("schema", "lakehouse")
dbutils.widgets.text("feature_table", "die_features")
dbutils.widgets.text("model_name", "ft_margin_predictor")
dbutils.widgets.text("label_table", "back_end_outcomes")
dbutils.widgets.text("target", "ft_margin_db")
dbutils.widgets.text("split_week", "38")
dbutils.widgets.text("n_trials", "25")

CATALOG   = dbutils.widgets.get("catalog")
SCHEMA    = dbutils.widgets.get("schema")
FQ        = f"{CATALOG}.{SCHEMA}"
FEATURE_TABLE = f"{FQ}.{dbutils.widgets.get('feature_table')}"
MODEL     = f"{FQ}.{dbutils.widgets.get('model_name')}"
TARGET    = dbutils.widgets.get("target")
SPLIT_WEEK= int(dbutils.widgets.get("split_week"))
N_TRIALS  = int(dbutils.widgets.get("n_trials"))
_USER = spark.sql("SELECT current_user()").first()[0]
EXPERIMENT= f"/Users/{_USER}/trace_to_contain/mlflow_experiment"

# COMMAND ----------
import json, numpy as np, pandas as pd, optuna, mlflow
from mlflow.tracking import MlflowClient
from pyspark.sql import functions as F
from databricks.feature_engineering import FeatureEngineeringClient, FeatureLookup
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

fe = FeatureEngineeringClient()
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(EXPERIMENT)

# Pure-numeric feature matrix -> no ColumnTransformer/OneHotEncoder (avoids skops-untrusted
# _RemainderColsList at fe.log_model). site stays a numeric code; tree splits handle it.
NUMERIC = ["fc_meas","il_meas","return_loss_db","q_factor","bw_mhz","rejection_db","leakage_na","static_cap_pf","temp_coeff_ppm","radius","temp_c","site"]

# COMMAND ----------
# MAGIC %md
# MAGIC ## Point-in-time training set via FeatureLookup (label time = final-test time)

# COMMAND ----------
labels = (spark.table(f"{FQ}.{dbutils.widgets.get('label_table')}")
          .select("die_id", F.col("meas_ts").alias("label_ts"), TARGET)
          .join(spark.table(f"{FQ}.die").select("die_id","start_week"), "die_id"))
train_labels = labels.filter(F.col("start_week") <= SPLIT_WEEK).select("die_id","label_ts",TARGET)
test_labels  = labels.filter(F.col("start_week") >  SPLIT_WEEK).select("die_id","label_ts",TARGET)

# Restrict lookup to numeric features so score_batch also returns a pure-numeric frame
lookups = [FeatureLookup(table_name=FEATURE_TABLE, lookup_key="die_id",
                         feature_names=NUMERIC, timestamp_lookup_key="label_ts")]

training_set = fe.create_training_set(df=train_labels, feature_lookups=lookups,
                                      label=TARGET, exclude_columns=["die_id","label_ts"])
test_set     = fe.create_training_set(df=test_labels,  feature_lookups=lookups,
                                      label=TARGET, exclude_columns=["die_id","label_ts"])
train_pd = training_set.load_df().toPandas()
test_pd  = test_set.load_df().toPandas()
print(f"train rows {len(train_pd):,} | point-in-time holdout rows {len(test_pd):,}")

Xtr_all, ytr_all = train_pd[NUMERIC], train_pd[TARGET].values
Xte, yte = test_pd[NUMERIC], test_pd[TARGET].values
Xt, Xv, yt, yv = train_test_split(Xtr_all, ytr_all, test_size=0.2, random_state=7)

def make_pipe(params):
    return HistGradientBoostingRegressor(random_state=7, **params)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Optuna search — each trial is a nested MLflow run (visible in the Experiments UI)

# COMMAND ----------
def objective(trial):
    params = dict(
        max_iter      = trial.suggest_int("max_iter", 150, 500),
        max_depth     = trial.suggest_int("max_depth", 3, 10),
        learning_rate = trial.suggest_float("learning_rate", 0.02, 0.3, log=True),
        l2_regularization = trial.suggest_float("l2_regularization", 1e-3, 10.0, log=True),
        max_leaf_nodes = trial.suggest_int("max_leaf_nodes", 15, 63),
    )
    with mlflow.start_run(nested=True):
        mlflow.log_params(params)
        pipe = make_pipe(params).fit(Xt, yt)
        rmse = float(np.sqrt(mean_squared_error(yv, pipe.predict(Xv))))
        mlflow.log_metric("val_rmse", rmse)
    return rmse

with mlflow.start_run(run_name="ft_margin_optuna") as parent:
    mlflow.log_params({"n_trials": N_TRIALS, "split_week": SPLIT_WEEK, "feature_table": FEATURE_TABLE})
    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=N_TRIALS)
    mlflow.log_metric("best_val_rmse", study.best_value)
    mlflow.log_params({f"best_{k}": v for k, v in study.best_params.items()})
    print("best val_rmse:", round(study.best_value, 4), "| best params:", study.best_params)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Retrain best on full training window, log with feature lineage, register @prod

# COMMAND ----------
with mlflow.start_run(run_name="ft_margin_best") as best_run:
    mlflow.log_params(study.best_params)
    final = make_pipe(study.best_params).fit(Xtr_all, ytr_all)
    pred = final.predict(Xte)
    rmse = float(np.sqrt(mean_squared_error(yte, pred))); mae = float(mean_absolute_error(yte, pred)); r2 = float(r2_score(yte, pred))
    mlflow.log_metrics({"pit_rmse": rmse, "pit_mae": mae, "pit_r2": r2})
    fe.log_model(model=final, artifact_path="model", flavor=mlflow.sklearn,
                 training_set=training_set, registered_model_name=MODEL)

client = MlflowClient(registry_uri="databricks-uc")
ver = max(client.search_model_versions(f"name='{MODEL}'"), key=lambda v: int(v.version)).version
client.set_registered_model_alias(MODEL, "prod", ver)
print(f"registered {MODEL} v{ver}  pit_rmse={rmse:.3f} pit_mae={mae:.3f} pit_r2={r2:.3f}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Score all die via feature lineage (keys only) → back_end_prediction

# COMMAND ----------
keys = spark.table(f"{FQ}.die").select("die_id","wafer_id","lot_id").withColumn("label_ts", F.current_timestamp())
scored = fe.score_batch(model_uri=f"models:/{MODEL}@prod", df=keys, result_type="double")
pred_tbl = (scored.withColumnRenamed("prediction","pred_ft_margin_db")
            .withColumn("pred_escape_risk", (F.col("pred_ft_margin_db") > 0.0) & (F.col("pred_ft_margin_db") <= 0.5))
            .withColumn("model_version", F.lit(str(ver))).withColumn("scored_as_of", F.current_timestamp())
            .select("die_id","wafer_id","lot_id","pred_ft_margin_db","pred_escape_risk","model_version","scored_as_of"))
pred_tbl.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(f"{FQ}.back_end_prediction")

# escape-risk detection quality vs actual outcomes
ev = (spark.table(f"{FQ}.back_end_prediction").join(
        spark.table(f"{FQ}.back_end_outcomes").select("die_id","escape_risk"), "die_id"))
tp = ev.filter("pred_escape_risk AND escape_risk").count()
fp = ev.filter("pred_escape_risk AND NOT escape_risk").count()
fn = ev.filter("NOT pred_escape_risk AND escape_risk").count()
prec = tp/(tp+fp) if (tp+fp) else None; rec = tp/(tp+fn) if (tp+fn) else None

me = spark.createDataFrame(
    [(MODEL, str(ver), "point_in_time", "rmse", rmse),
     (MODEL, str(ver), "point_in_time", "mae", mae),
     (MODEL, str(ver), "point_in_time", "r2", r2),
     (MODEL, str(ver), "escape_detection", "precision", prec or 0.0),
     (MODEL, str(ver), "escape_detection", "recall", rec or 0.0)],
    "model_name string, model_version string, split string, metric string, value double") \
    .withColumn("evaluated_as_of", F.current_timestamp())
me.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(f"{FQ}.model_evaluations")
print(f"escape detection precision={prec} recall={rec}")

dbutils.notebook.exit(json.dumps({"model_version": str(ver), "pit_rmse": round(rmse,3),
                                  "pit_r2": round(r2,3), "escape_precision": prec, "escape_recall": rec,
                                  "best_val_rmse": round(study.best_value,4)}))
