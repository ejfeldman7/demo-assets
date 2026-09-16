import traceback
from databricks.connect import DatabricksSession
from pyspark.sql import functions as F

FQ = "trace_to_contain.lakehouse"
spark = DatabricksSession.builder.serverless(True).getOrCreate()

try:
    be_full = spark.table(f"{FQ}.back_end_outcomes").join(
        spark.table(f"{FQ}.module").select("module_sn","assy_ts","lot_id"), "module_sn")
    cust = F.element_at(F.array(F.lit("Handset OEM A"),F.lit("Handset OEM B"),F.lit("Base Station T1"),
                                F.lit("Automotive T1"),F.lit("IoT Module Co")),
                        (F.abs(F.hash(F.col("module_sn"))) % 5)+1)

    real = (be_full.filter((F.col("product")=="BAW-B7") & ((F.col("escape_risk")) | (~F.col("ft_pass_spec"))))
            .join(spark.table(f"{FQ}.die").select("die_id","is_excursion"), "die_id")
            .filter(F.col("is_excursion"))
            .orderBy(F.rand(81)).limit(240)
            .withColumn("is_nff", F.lit(False))
            .withColumn("failure_mode", F.element_at(F.array(F.lit("Insertion loss high"),F.lit("Out-of-band emission"),F.lit("Frequency out of spec")),(F.abs(F.hash(F.col("module_sn")))%3)+1)))
    nff = (be_full.filter((F.col("product")=="BAW-B7") & (F.col("ft_margin_db") > 0.9))
            .orderBy(F.rand(82)).limit(160)
            .withColumn("is_nff", F.lit(True))
            .withColumn("failure_mode", F.lit("No fault found on retest")))
    rma = (real.unionByName(nff, allowMissingColumns=True)
        .withColumn("offset_days", (30 + (F.abs(F.hash(F.col("module_sn")))%90)).cast("int"))
        .withColumn("received_ts", F.expr("timestampadd(DAY, offset_days, assy_ts)"))
        .withColumn("customer", cust)
        .withColumn("rma_id", F.concat(F.lit("RMA-"), F.lpad((F.abs(F.hash(F.col("module_sn")))%1000000).cast("string"),6,"0")))
        .select("rma_id","module_sn","customer","failure_mode","is_nff","received_ts","lot_id","wafer_id"))
    rma.write.mode("overwrite").saveAsTable(f"{FQ}.rma_history")
    n = spark.table(f"{FQ}.rma_history").count()
    nff_n = spark.table(f"{FQ}.rma_history").filter("is_nff").count()
    print(f"rma_history written: {n} rows ({nff_n} NFF, {n-nff_n} real)")
    print("DONE")
except Exception as e:
    print("PYERROR:", repr(e)[:1200])
    traceback.print_exc()
