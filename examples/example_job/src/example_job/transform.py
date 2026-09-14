from pyspark.sql import DataFrame
from pyspark.sql import functions as F


def summarize_sales_by_category(sales_df: DataFrame) -> DataFrame:
    """Aggregates total sale amount per category.

    Args:
        sales_df: DataFrame with columns `category` (string) and `amount` (double).

    Returns:
        DataFrame with columns `category` and `total_amount`.
    """
    return sales_df.groupBy("category").agg(F.sum("amount").alias("total_amount"))
