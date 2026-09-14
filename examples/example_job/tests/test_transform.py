from example_job.transform import summarize_sales_by_category


def test_summarize_sales_by_category(local_spark_session):
    sales_df = local_spark_session.createDataFrame(
        [("books", 10.0), ("books", 5.0), ("toys", 20.0)],
        ["category", "amount"],
    )

    result = summarize_sales_by_category(sales_df).orderBy("category")

    rows = {row["category"]: row["total_amount"] for row in result.collect()}
    assert rows == {"books": 15.0, "toys": 20.0}
