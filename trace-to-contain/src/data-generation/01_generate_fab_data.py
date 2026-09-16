"""
'Trace to Contain' — Phase 0 synthetic data generator.

Story: a piezo film-thickness excursion on deposition tool DEP-02 during weeks 25-34
shifts BAW filter center frequency (Fc down as film thickens), worst at the wafer edge
(radial gradient). That true front-end signature propagates to final-test insertion-loss
MARGIN loss and, downstream, to field RMAs. Two confounders are deliberately baked in so
a naive correlation is misleading and must be conditioned away:
  A) ATE test-site offset  -> site 7 reads Fc high (pollutes measured Fc ~ fail).
  B) probe-card time drift  -> measured probe IL drifts up over the calendar year, and an
     UNRELATED back-end tester-aging ramp also rises over the year, so a time-pooled
     correlation between measured IL and fail is inflated but spurious.
Ground truth (true_fc_shift_mhz, true_il_excess_db, severity) is stored on `die` so the
model layer can later be validated and so all tables stay physically coherent.
"""
import os
from databricks.connect import DatabricksSession
from pyspark.sql import functions as F, types as T
from pyspark.sql.window import Window

CATALOG = "trace_to_contain"
SCHEMA = "lakehouse"
FQ = f"{CATALOG}.{SCHEMA}"

spark = DatabricksSession.builder.serverless(True).getOrCreate()
print("Spark session up:", spark.version)

FC_NOMINAL = 1960.0          # MHz, Band-7-ish BAW filter center
BASE_IL = 0.8                # dB, nominal probe insertion loss
EXC_LO, EXC_HI = 25, 34      # excursion week window on DEP-02
REV_CUTOFF_WEEK = 20         # schema-drift: param renamed at program rev R3

# Confounder A: per-ATE-site Fc measurement offset (MHz). Site 7 is the polluter.
SITE_FC = [0.05,-0.03,0.08,-0.06,0.02,-0.09,0.04,0.45,-0.02,0.07,-0.05,0.03,-0.08,0.06,-0.04,0.01]
SITE_IL = [0.00,0.01,-0.01,0.00,0.01,-0.01,0.00,0.03,0.00,0.01,-0.01,0.00,0.01,-0.01,0.00,0.00]
site_fc_arr = F.array(*[F.lit(v) for v in SITE_FC])
site_il_arr = F.array(*[F.lit(v) for v in SITE_IL])

# ---------------------------------------------------------------- parameter_dim
param_rows = [
    ("fc_center_mhz","Center frequency","frequency","MHz",["center_freq_mhz","fc_center_mhz"],1955.0,1965.0,1957.0,1963.0,"Series-resonance center frequency; primary film-thickness indicator"),
    ("il_probe_db","Insertion loss (probe)","loss","dB",["il_probe_db"],None,1.5,None,1.2,"In-band insertion loss at wafer probe"),
    ("return_loss_db","Return loss","match","dB",["return_loss_db"],14.0,None,16.0,None,"Input return loss"),
    ("q_factor","Quality factor","resonator","ratio",["q_factor"],1000.0,None,1050.0,None,"Resonator Q"),
    ("bw_mhz","Bandwidth","frequency","MHz",["bw_mhz"],56.0,64.0,57.0,63.0,"3 dB bandwidth"),
    ("rejection_db","Out-of-band rejection","filter","dB",["rejection_db"],38.0,None,40.0,None,"Stopband rejection"),
    ("leakage_na","Leakage current","dc","nA",["leakage_na"],None,50.0,None,40.0,"DC leakage"),
    ("static_cap_pf","Static capacitance","dc","pF",["static_cap_pf"],2.0,2.4,2.05,2.35,"C0 static capacitance"),
    ("temp_coeff_ppm","Temp coefficient","stability","ppm/C",["temp_coeff_ppm"],-30.0,-15.0,-28.0,-17.0,"Temperature coefficient of frequency"),
]
pdim = spark.createDataFrame(
    param_rows,
    schema=T.StructType([
        T.StructField("param_id",T.StringType()), T.StructField("canonical_name",T.StringType()),
        T.StructField("family",T.StringType()), T.StructField("unit",T.StringType()),
        T.StructField("aliases",T.ArrayType(T.StringType())),
        T.StructField("lo_spec",T.DoubleType()), T.StructField("hi_spec",T.DoubleType()),
        T.StructField("lo_guardband",T.DoubleType()), T.StructField("hi_guardband",T.DoubleType()),
        T.StructField("description",T.StringType()),
    ]),
)
pdim.write.mode("overwrite").saveAsTable(f"{FQ}.parameter_dim")
print("parameter_dim written")

# ---------------------------------------------------------------- lot (180)
N_LOTS = 180
lot = (spark.range(0, N_LOTS, numPartitions=8)
    .withColumn("lot_id", F.concat(F.lit("LOT-"), F.lpad(F.col("id").cast("string"),4,"0")))
    .withColumn("product", F.when(F.col("id") < 150, F.lit("BAW-B7")).otherwise(F.lit("FEM-8T")))
    .withColumn("fab", F.when(F.rand(11) < 0.55, F.lit("FAB-A")).otherwise(F.lit("FAB-B")))
    .withColumn("dep_tool", F.when(F.rand(12) < 0.40, F.lit("DEP-02"))
                             .when(F.rand(13) < 0.55, F.lit("DEP-01")).otherwise(F.lit("DEP-03")))
    .withColumn("start_week", (F.floor(F.rand(14)*52)+1).cast("int"))
    .withColumn("test_program_rev", F.when(F.col("start_week") < REV_CUTOFF_WEEK, F.lit("R2")).otherwise(F.lit("R3")))
    .withColumn("start_date", F.date_add(F.lit("2025-01-06").cast("date"), (F.col("start_week")-1)*7))
    .drop("id"))
lot.write.mode("overwrite").saveAsTable(f"{FQ}.lot")
print("lot written")
lot = spark.table(f"{FQ}.lot")

# ---------------------------------------------------------------- wafer (25/lot)
wafer_no = spark.range(1, 26, numPartitions=1).withColumnRenamed("id","wafer_no")
wafer = (lot.crossJoin(wafer_no)
    .withColumn("wafer_id", F.concat(F.col("lot_id"), F.lit("-W"), F.lpad(F.col("wafer_no").cast("string"),2,"0")))
    .withColumn("wafer_fc_offset", F.randn(21)*0.10)
    .withColumn("is_excursion",
        (F.col("dep_tool")==F.lit("DEP-02")) & F.col("start_week").between(EXC_LO,EXC_HI)))
wafer.write.mode("overwrite").saveAsTable(f"{FQ}.wafer")
print("wafer written")
wafer = spark.table(f"{FQ}.wafer")

# ---------------------------------------------------------------- die (BAW only, ~1.1M)
GX = GY = 20
CX = CY = 9.5
RMAX = ((GX-1-CX)**2 + (GY-1-CY)**2) ** 0.5   # corner radius for normalization
grid = (spark.range(0, GX*GY, numPartitions=1)
    .withColumn("die_x", (F.col("id") % GX).cast("int"))
    .withColumn("die_y", (F.floor(F.col("id")/GX)).cast("int"))
    .withColumn("pos", F.col("id").cast("int"))
    .withColumn("radius", F.sqrt(F.pow(F.col("die_x")-CX,2)+F.pow(F.col("die_y")-CY,2))/F.lit(RMAX))
    .filter(F.col("radius") <= 1.0)   # circular wafer
    .drop("id"))

wafers_baw = wafer.filter(F.col("product")=="BAW-B7")
die = (wafers_baw.crossJoin(grid)
    .withColumn("die_id", F.concat(F.col("wafer_id"), F.lit("-D"), F.lpad(F.col("pos").cast("string"),3,"0")))
    .withColumn("site", (F.col("pos") % 16).cast("int"))
    .withColumn("temp_c", F.when(F.abs(F.hash(F.col("die_id"))) % 4 == 0, F.lit(85)).otherwise(F.lit(25)))
    .withColumn("die_uid", F.col("die_id"))   # BAW has die-level id
    # ground-truth latent physics
    .withColumn("severity", F.when(F.col("is_excursion"), (0.35 + 0.65*F.col("radius"))).otherwise(F.lit(0.0)))
    .withColumn("true_fc_shift_mhz",
        -1.2*F.col("severity") - 0.08*F.col("radius") + F.col("wafer_fc_offset") + F.randn(31)*0.12)
    .withColumn("true_il_excess_db", F.greatest(F.lit(0.0), 0.30*F.col("severity") + F.randn(32)*0.02))
    .withColumn("fc_param_id", F.when(F.col("test_program_rev")=="R2", F.lit("center_freq_mhz")).otherwise(F.lit("fc_center_mhz")))
    .select("die_id","wafer_id","lot_id","product","fab","dep_tool","start_week","start_date",
            "test_program_rev","is_excursion","die_x","die_y","radius","site","temp_c","die_uid",
            "fc_param_id","severity","true_fc_shift_mhz","true_il_excess_db"))
die.write.mode("overwrite").saveAsTable(f"{FQ}.die")
print("die written")
die = spark.table(f"{FQ}.die")

# ---------------------------------------------------------------- fab_measurements (long)
site_fc_off = F.element_at(site_fc_arr, F.col("site")+1)
site_il_off = F.element_at(site_il_arr, F.col("site")+1)
meas_ts = F.col("start_date").cast("timestamp")

def mrow(param_id_col, value_col, lo_spec, hi_spec, lo_gb, hi_gb, unit, insertion="FIRST"):
    return die.select(
        F.col("die_id"), F.col("wafer_id"), F.col("lot_id"),
        param_id_col.alias("param_id"),
        value_col.cast("double").alias("value"), F.lit(unit).alias("unit"),
        F.lit(lo_spec).cast("double").alias("lo_spec"), F.lit(hi_spec).cast("double").alias("hi_spec"),
        F.lit(lo_gb).cast("double").alias("lo_guardband"), F.lit(hi_gb).cast("double").alias("hi_guardband"),
        F.col("site"), F.col("dep_tool"), F.col("temp_c"), F.col("test_program_rev"),
        F.lit(insertion).alias("insertion"), meas_ts.alias("meas_ts"))

# Confounder A on measured Fc (site offset); Confounder B on measured IL (probe-card time drift)
measured_fc = FC_NOMINAL + F.col("true_fc_shift_mhz") + site_fc_off + F.randn(41)*0.05
measured_il = BASE_IL + F.col("true_il_excess_db") + 0.006*F.col("start_week") + site_il_off + F.randn(42)*0.02

sev = F.col("severity")
m_fc  = mrow(F.col("fc_param_id"), measured_fc, 1955.0,1965.0,1957.0,1963.0,"MHz")
m_il  = mrow(F.lit("il_probe_db"), measured_il, None,1.5,None,1.2,"dB")
m_rl  = mrow(F.lit("return_loss_db"), 18.0 - 1.0*sev + F.randn(43)*1.2, 14.0,None,16.0,None,"dB")
m_q   = mrow(F.lit("q_factor"), 1200.0 - 90.0*sev + F.randn(44)*80.0, 1000.0,None,1050.0,None,"ratio")
m_bw  = mrow(F.lit("bw_mhz"), 60.0 + F.randn(45)*1.5, 56.0,64.0,57.0,63.0,"MHz")
m_rej = mrow(F.lit("rejection_db"), 42.0 - 3.0*sev + F.randn(46)*2.0, 38.0,None,40.0,None,"dB")
m_leak= mrow(F.lit("leakage_na"), F.exp(F.randn(47)*0.4)*5.0, None,50.0,None,40.0,"nA")
m_cap = mrow(F.lit("static_cap_pf"), 2.2 + F.randn(48)*0.05, 2.0,2.4,2.05,2.35,"pF")
m_tc  = mrow(F.lit("temp_coeff_ppm"), -22.0 + F.randn(49)*3.0, -30.0,-15.0,-28.0,-17.0,"ppm/C")

meas = m_fc.unionByName(m_il).unionByName(m_rl).unionByName(m_q).unionByName(m_bw) \
          .unionByName(m_rej).unionByName(m_leak).unionByName(m_cap).unionByName(m_tc)

# Retest/rebin rows: ~3% of die get a 2nd Fc insertion (later ts, pulled toward nominal)
retest = (die.filter(F.abs(F.hash(F.concat(F.col("die_id"),F.lit("rt")))) % 33 == 0)
    .select(
        F.col("die_id"), F.col("wafer_id"), F.col("lot_id"),
        F.col("fc_param_id").alias("param_id"),
        (FC_NOMINAL + F.col("true_fc_shift_mhz")*0.5 + F.randn(51)*0.05).cast("double").alias("value"),
        F.lit("MHz").alias("unit"),
        F.lit(1955.0).alias("lo_spec"), F.lit(1965.0).alias("hi_spec"),
        F.lit(1957.0).alias("lo_guardband"), F.lit(1963.0).alias("hi_guardband"),
        F.col("site"), F.col("dep_tool"), F.col("temp_c"), F.col("test_program_rev"),
        F.lit("RETEST").alias("insertion"),
        (F.col("start_date").cast("timestamp") + F.expr("INTERVAL 6 HOURS")).alias("meas_ts")))
meas = meas.unionByName(retest)
meas.write.mode("overwrite").saveAsTable(f"{FQ}.fab_measurements")
print("fab_measurements written")

# ---------------------------------------------------------------- modules + back_end_outcomes
# BAW: assemble ~90% of die (good AND bad -> escapes are possible)
baw_assembled = die.filter(F.abs(F.hash(F.concat(F.col("die_id"),F.lit("asm")))) % 10 != 0)
baw_mod = (baw_assembled
    .withColumn("module_sn", F.concat(F.lit("MOD-B-"), F.col("die_id")))
    .withColumn("assy_site", (F.abs(F.hash(F.col("die_id"))) % 8).cast("int"))
    .withColumn("assy_ts", (F.col("start_date").cast("timestamp") + F.expr("INTERVAL 21 DAYS")))
    .select("module_sn", F.lit("BAW-B7").alias("product"), "die_id","wafer_id","lot_id",
            "assy_site","assy_ts","true_fc_shift_mhz","true_il_excess_db","is_excursion"))

# FEM: 40k modules, each traced to a source filter wafer (wafer grain, lower confidence)
w_idx = wafers_baw.select("wafer_id","lot_id").withColumn("widx", F.row_number().over(Window.orderBy("wafer_id"))-1)
N_W = w_idx.count()
wafer_sev = (die.groupBy("wafer_id").agg(F.avg("true_fc_shift_mhz").alias("wafer_avg_fc_shift"),
                                         F.max("is_excursion").alias("wafer_excursion")))
fem_mod = (spark.range(0, 40000, numPartitions=8)
    .withColumn("module_sn", F.concat(F.lit("MOD-F-"), F.lpad(F.col("id").cast("string"),6,"0")))
    .withColumn("widx", (F.abs(F.hash(F.col("id"))) % N_W))
    .join(w_idx, "widx")
    .join(wafer_sev, "wafer_id")
    .withColumn("assy_site", (F.abs(F.hash(F.col("id"))) % 8).cast("int"))
    .withColumn("assy_ts", F.date_add(F.lit("2025-02-01").cast("date"), (F.abs(F.hash(F.col("id"))) % 300)).cast("timestamp"))
    .select("module_sn", F.lit("FEM-8T").alias("product"), F.lit(None).cast("string").alias("die_id"),
            "wafer_id","lot_id","assy_site","assy_ts",
            F.col("wafer_avg_fc_shift").alias("true_fc_shift_mhz"),
            F.lit(0.0).alias("true_il_excess_db"), F.col("wafer_excursion").alias("is_excursion")))

modules = baw_mod.unionByName(fem_mod)
modules.select("module_sn","product","die_id","wafer_id","lot_id","assy_site","assy_ts") \
       .write.mode("overwrite").saveAsTable(f"{FQ}.module")
print("module written")

# back-end outcomes: ft_margin driven by TRUE physics; back-end tester-aging ramp is an
# unrelated calendar trend that helps make the probe-IL time-drift correlation spurious.
week_of_assy = F.weekofyear(F.col("assy_ts"))
be = (modules
    .withColumn("ft_insertion_loss_db",
        F.lit(0.9) + 0.6*F.col("true_il_excess_db") + 0.25*F.abs(F.col("true_fc_shift_mhz")) + F.randn(61)*0.05)
    .withColumn("ft_margin_db",
        F.lit(2.0) - 0.9*F.abs(F.col("true_fc_shift_mhz")) - 1.4*F.col("true_il_excess_db")
        - 0.004*week_of_assy + F.randn(62)*0.25)
    .withColumn("ft_pass_spec", F.col("ft_margin_db") > 0.0)
    .withColumn("ft_pass_guardband", F.col("ft_margin_db") > 0.5)
    .withColumn("escape_risk", (F.col("ft_margin_db") > 0.0) & (F.col("ft_margin_db") <= 0.5))
    .withColumn("bin", F.when(F.col("ft_margin_db") > 0.5, F.lit(1)).when(F.col("ft_margin_db") > 0.0, F.lit(3)).otherwise(F.lit(5)))
    .withColumn("meas_ts", (F.col("assy_ts") + F.expr("INTERVAL 2 DAYS")))
    .withColumn("outcome_id", F.concat(F.lit("FT-"), F.col("module_sn")))
    .select("outcome_id","module_sn","product","die_id","wafer_id",
            "ft_insertion_loss_db","ft_margin_db","ft_pass_spec","ft_pass_guardband","escape_risk","bin","meas_ts"))
be.write.mode("overwrite").saveAsTable(f"{FQ}.back_end_outcomes")
print("back_end_outcomes written")

# ---------------------------------------------------------------- die_to_module bridge
baw_bridge = (spark.table(f"{FQ}.module").filter(F.col("product")=="BAW-B7")
    .select("module_sn","die_id","wafer_id","lot_id",
            F.lit("die").alias("trace_grain"), (F.lit(0.95)+F.rand(71)*0.05).alias("trace_confidence")))
fem_bridge = (spark.table(f"{FQ}.module").filter(F.col("product")=="FEM-8T")
    .select("module_sn", F.lit(None).cast("string").alias("die_id"),"wafer_id","lot_id",
            F.lit("wafer").alias("trace_grain"), (F.lit(0.60)+F.rand(72)*0.15).alias("trace_confidence")))
baw_bridge.unionByName(fem_bridge).write.mode("overwrite").saveAsTable(f"{FQ}.die_to_module")
print("die_to_module written")

# rma_history is generated in 02_generate_rma.py (kept separate; run it next).

print("=== ROW COUNTS ===")
for t in ["parameter_dim","lot","wafer","die","fab_measurements","module","back_end_outcomes","die_to_module"]:
    print(f"{t:22s} {spark.table(f'{FQ}.{t}').count():>12,}")
print("DONE — now run 02_generate_rma.py for rma_history")
