from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession

builder = (
    SparkSession.builder.appName("smoke-test")
    .master("local[2]")
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config("spark.ui.enabled", "false")
)
spark = configure_spark_with_delta_pip(builder).getOrCreate()

df = spark.createDataFrame([(1, "a"), (2, "b")], ["id", "value"])
df.write.format("delta").mode("overwrite").save("/tmp/smoke-delta-table")

result = spark.read.format("delta").load("/tmp/smoke-delta-table")
row_count = result.count()
assert row_count == 2, f"expected 2 rows, got {row_count}"

spark.stop()
print("SMOKE_OK")
