from __future__ import annotations

import argparse

from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession

from example_job.transform import summarize_sales_by_category


def build_arg_parser() -> argparse.ArgumentParser:
    """Builds the CLI argument parser for this job.

    Args:
        None.

    Returns:
        Configured ArgumentParser with `--input-path` and `--output-path`.
    """
    parser = argparse.ArgumentParser(description="Summarize sales by category")
    parser.add_argument("--input-path", required=True, help="Delta table location to read sales from")
    parser.add_argument("--output-path", required=True, help="Delta table location to write the summary to")
    return parser


def run(input_path: str, output_path: str) -> None:
    """Reads sales from a Delta table, summarizes them, and writes the result.

    Args:
        input_path: Delta table location to read sales from.
        output_path: Delta table location to write the summary to.

    Returns:
        None.
    """
    builder = (
        SparkSession.builder.appName("example-job")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    )
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    try:
        sales_df = spark.read.format("delta").load(input_path)
        summary_df = summarize_sales_by_category(sales_df)
        summary_df.write.format("delta").mode("overwrite").save(output_path)
        print(f"Wrote summary to {output_path}")
    finally:
        spark.stop()


def main(argv: list[str] | None = None) -> None:
    """Parses CLI arguments and runs the job.

    Args:
        argv: Command-line arguments, or None to use `sys.argv`.

    Returns:
        None.
    """
    args = build_arg_parser().parse_args(argv)
    run(args.input_path, args.output_path)


if __name__ == "__main__":
    main()
