from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from databricks_local_ci.subprocess_runner import run_entrypoint

CONFIG_KEY = "databricks-local-ci"


def load_job_config(rootpath: Path) -> dict | None:
    """Reads the [tool.databricks-local-ci] table from a project's pyproject.toml.

    Args:
        rootpath: Directory expected to contain the project's pyproject.toml.

    Returns:
        The config table, or None if pyproject.toml or the section is missing.
    """
    pyproject_path = rootpath / "pyproject.toml"
    if not pyproject_path.exists():
        return None
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return data.get("tool", {}).get(CONFIG_KEY)


def make_auto_real_run_test(job_config: dict):
    """Builds the auto-generated real-run test function for a given job config.

    Args:
        job_config: The `[tool.databricks-local-ci]` table, with `entry_point`
            (dotted module path) and `sample_input` (`columns` list and `rows`
            list of lists).

    Returns:
        A pytest test function taking `local_spark_session` and `tmp_path`.
    """
    entry_point = job_config["entry_point"]
    columns = job_config["sample_input"]["columns"]
    rows = [tuple(row) for row in job_config["sample_input"]["rows"]]

    def test_databricks_local_ci_auto_real_run(local_spark_session, tmp_path):
        input_path = str(tmp_path / "auto-real-run-input")
        output_path = str(tmp_path / "auto-real-run-output")

        input_df = local_spark_session.createDataFrame(rows, columns)
        input_df.write.format("delta").mode("overwrite").save(input_path)

        result = run_entrypoint(
            entry_point,
            args=["--input-path", input_path, "--output-path", output_path],
            timeout=120,
        )

        assert result.returncode == 0, result.stderr

        output_df = local_spark_session.read.format("delta").load(output_path)
        assert output_df.count() > 0, "entry point wrote an empty output table"

    return test_databricks_local_ci_auto_real_run


def pytest_collection_modifyitems(session, config, items) -> None:
    """Injects an automatic real-run test from pyproject.toml config, if present.

    Args:
        session: The current pytest session.
        config: The current pytest config.
        items: The list of collected test items, appended to in place.

    Returns:
        None.
    """
    job_config = load_job_config(config.rootpath)
    if job_config is None:
        return

    module = pytest.Module.from_parent(session, path=Path(__file__))
    test_func = make_auto_real_run_test(job_config)
    items.append(
        pytest.Function.from_parent(
            module, name="test_databricks_local_ci_auto_real_run", callobj=test_func
        )
    )
