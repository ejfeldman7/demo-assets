# Databricks notebook source
# MAGIC %md
# MAGIC # What-if twin — raw-feature margin model for live scoring
# MAGIC The @prod model is feature-store-logged (serves via online lookup by key). For interactive
# MAGIC what-if we need a model that accepts the raw feature vector directly. Same data, same
# MAGIC estimator, logged as plain sklearn and served on a real-time endpoint.

# COMMAND ----------
import json, numpy as np, pandas as pd, mlflow
from mlflow.tracking import MlflowClient
from mlflow.models.signature import infer_signature
from pyspark.sql import functions as F
from sklearn.ensemble import HistGradientBoostingRegressor

CATALOG, SCHEMA = "trace_to_contain", "lakehouse"
FQ = f"{CATALOG}.{SCHEMA}"
MODEL = f"{FQ}.ft_margin_whatif"
NUMERIC = ["fc_meas","il_meas","return_loss_db","q_factor","bw_mhz","rejection_db",
           "leakage_na","static_cap_pf","temp_coeff_ppm","radius","temp_c","site"]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(f"/Users/{spark.sql('SELECT current_user()').first()[0]}/trace_to_contain/mlflow_experiment")

# COMMAND ----------
df = (spark.table(f"{FQ}.die_features").select("die_id", *NUMERIC)
        .join(spark.table(f"{FQ}.back_end_outcomes").select("die_id", "ft_margin_db"), "die_id")
        .select(*NUMERIC, "ft_margin_db").sample(0.3, seed=11).toPandas())
X = df[NUMERIC].astype(float); y = df["ft_margin_db"].values
print("train rows:", len(df))

with mlflow.start_run(run_name="ft_margin_whatif") as run:
    model = HistGradientBoostingRegressor(max_iter=300, max_depth=6, learning_rate=0.08, random_state=11).fit(X, y)
    sig = infer_signature(X, model.predict(X))
    info = mlflow.sklearn.log_model(model, name="model", signature=sig,
                                    input_example=X.iloc[:3], registered_model_name=MODEL)
client = MlflowClient(registry_uri="databricks-uc")
client.set_registered_model_alias(MODEL, "prod", info.registered_model_version)
print(json.dumps({"model": MODEL, "version": info.registered_model_version}))
dbutils.notebook.exit(json.dumps({"model": MODEL, "version": info.registered_model_version}))
