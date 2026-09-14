from databricks_local_ci.subprocess_runner import run_entrypoint


def test_example_job_real_run_writes_expected_summary(local_spark_session, tmp_path):
    input_path = str(tmp_path / "sales")
    output_path = str(tmp_path / "summary")

    sales_df = local_spark_session.createDataFrame(
        [("books", 10.0), ("books", 5.0), ("toys", 20.0)],
        ["category", "amount"],
    )
    sales_df.write.format("delta").mode("overwrite").save(input_path)

    result = run_entrypoint(
        "example_job.main",
        args=["--input-path", input_path, "--output-path", output_path],
    )

    assert result.returncode == 0, result.stderr
    assert "Wrote summary to" in result.stdout

    summary_df = local_spark_session.read.format("delta").load(output_path)
    rows = {row["category"]: row["total_amount"] for row in summary_df.collect()}
    assert rows == {"books": 15.0, "toys": 20.0}
