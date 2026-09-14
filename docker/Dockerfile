ARG DBR_TAG=15.4-LTS
FROM databricksruntime/python:${DBR_TAG}

RUN pip install --no-cache-dir pyspark==3.5.3 delta-spark==3.2.1 "pytest>=8,<10" uv \
    && ln -sf "$(command -v python3.11)" /usr/local/bin/python

RUN python -c "from delta import configure_spark_with_delta_pip; from pyspark.sql import SparkSession; b = SparkSession.builder.appName('warm-cache').master('local[1]').config('spark.sql.extensions', 'io.delta.sql.DeltaSparkSessionExtension').config('spark.sql.catalog.spark_catalog', 'org.apache.spark.sql.delta.catalog.DeltaCatalog'); s = configure_spark_with_delta_pip(b).getOrCreate(); s.stop()"

WORKDIR /workspace
