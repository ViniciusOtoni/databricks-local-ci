from __future__ import annotations

import shutil
import tempfile

import pytest
from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession


@pytest.fixture(scope="session")
def local_spark_session():
    """Provides a Delta-enabled local SparkSession for a test session.

    Args:
        None.

    Returns:
        A SparkSession configured with Delta Lake support.
    """
    warehouse_dir = tempfile.mkdtemp(prefix="databricks-local-ci-warehouse-")
    builder = (
        SparkSession.builder.appName("databricks-local-ci")
        .master("local[2]")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.warehouse.dir", warehouse_dir)
        .config("spark.ui.enabled", "false")
    )
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    try:
        yield spark
    finally:
        spark.stop()
        shutil.rmtree(warehouse_dir, ignore_errors=True)


@pytest.fixture()
def local_delta_table_path(tmp_path):
    """Provides a fresh local path to use as a Delta table location.

    Args:
        tmp_path: Pytest's built-in per-test temporary directory.

    Returns:
        String path to an unused directory under `tmp_path`.
    """
    return str(tmp_path / "delta-table")
