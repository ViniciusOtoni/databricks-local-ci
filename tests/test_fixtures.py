def test_local_spark_session_reads_and_writes_delta(local_spark_session, local_delta_table_path):
    df = local_spark_session.createDataFrame([(1, "a"), (2, "b")], ["id", "value"])
    df.write.format("delta").mode("overwrite").save(local_delta_table_path)

    result = local_spark_session.read.format("delta").load(local_delta_table_path)

    assert result.count() == 2
    assert sorted(row["value"] for row in result.collect()) == ["a", "b"]
