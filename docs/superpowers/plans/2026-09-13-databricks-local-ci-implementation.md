# Databricks Local CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable framework — a Dockerfile based on `databricksruntime/python`, a shared pytest testkit, an example Job/wheel project, and a GitHub Actions reusable workflow — that runs a *real* execution of a Databricks Python Job inside the exact Databricks Runtime (DBR) version used in production, and only deploys via Databricks Asset Bundles if that run succeeds.

**Architecture:** A `databricks_local_ci` Python package provides two pieces of test infrastructure: a `subprocess_runner` that invokes a job's real CLI entry point as an OS process (not an in-process import), and a `local_spark_session` pytest fixture (Delta-enabled) used only to read back and assert on the Delta table the job wrote. An `examples/example_job` package demonstrates the contract every client project must follow: a single CLI entry point, table locations passed as parameters (never hardcoded), tested end-to-end inside the DBR container. A GitHub Actions reusable workflow wires `build-and-test` (build wheel + run pytest inside the container) and `deploy` (`databricks bundle deploy` via OIDC, gated on tests passing).

**Tech Stack:** Python 3.12, PySpark 3.5.3, delta-spark 3.2.1, pytest, Docker (`databricksruntime/python:15.4-LTS`), GitHub Actions, Databricks CLI / Asset Bundles.

**Known environment constraint:** this repo lives under a path containing an accented character (`Área de Trabalho`). PySpark's Java gateway fails to launch on Windows when installed/run from a path with non-ASCII characters (confirmed by testing in this session — see Task 3 note). Any step that starts a real `SparkSession` **must run inside the Docker container** (Linux path, unaffected), never directly via the host venv on this machine. Steps that don't touch Spark (Task 2) are safe to run directly on the host.

---

## Task 1: Repo scaffolding and host venv

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `src/databricks_local_ci/__init__.py`

- [ ] **Step 1: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
*.egg-info/
spark-warehouse/
metastore_db/
derby.log
.pytest_cache/
dist/
build/
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "databricks-local-ci"
version = "0.1.0"
description = "Local CI testkit for validating Databricks Python jobs before deploy"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = [
    "pytest>=8,<10",
    "pyspark==3.5.3",
    "delta-spark==3.2.1",
]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

Note: the `pytest11` entry point that registers `databricks_local_ci.fixtures` as a
plugin is added later, in Task 4, once that module actually exists. Registering it
here would break every `pytest` invocation in this venv with a plugin-loading
`ModuleNotFoundError` before `fixtures.py` exists.

- [ ] **Step 3: Create the package init file**

`src/databricks_local_ci/__init__.py`:
```python
```
(empty file — just marks the directory as a package)

- [ ] **Step 4: Create the host venv and install in editable mode**

Run (from the repo root, in `bash`):
```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```
Expected: no errors; `pip show databricks-local-ci` afterward shows `Version: 0.1.0` and
`Editable project location` pointing at the repo.

- [ ] **Step 5: Commit**

```bash
git add .gitignore pyproject.toml src/databricks_local_ci/__init__.py
git commit -m "chore: scaffold databricks-local-ci package"
```

---

## Task 2: Subprocess entry-point runner (host-runnable, no Spark)

This is the core primitive behind the "real run" pattern: it launches a job's CLI entry
point as a real OS process (`python -m <module>`), the same way a Databricks Job would
invoke it, and captures the result. It has no Spark dependency, so — unlike every other
task in this plan — it can be developed and tested directly on the host venv.

**Files:**
- Create: `src/databricks_local_ci/subprocess_runner.py`
- Test: `tests/test_subprocess_runner.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_subprocess_runner.py`:
```python
from pathlib import Path

from databricks_local_ci.subprocess_runner import run_entrypoint


def test_run_entrypoint_captures_stdout_and_args(tmp_path: Path):
    (tmp_path / "dummy_entry.py").write_text(
        "import sys\n"
        "print('hello from dummy entrypoint')\n"
        "print(sys.argv[1:])\n"
        "sys.exit(0)\n"
    )

    result = run_entrypoint("dummy_entry", args=["--foo", "bar"], cwd=tmp_path)

    assert result.returncode == 0
    assert "hello from dummy entrypoint" in result.stdout
    assert "['--foo', 'bar']" in result.stdout


def test_run_entrypoint_captures_nonzero_exit_code(tmp_path: Path):
    (tmp_path / "dummy_fail.py").write_text("import sys\nsys.exit(3)\n")

    result = run_entrypoint("dummy_fail", cwd=tmp_path)

    assert result.returncode == 3


def test_run_entrypoint_captures_stderr(tmp_path: Path):
    (tmp_path / "dummy_stderr.py").write_text(
        "import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n"
    )

    result = run_entrypoint("dummy_stderr", cwd=tmp_path)

    assert result.returncode == 1
    assert "boom" in result.stderr


def test_run_entrypoint_reports_missing_module_as_data_not_exception(tmp_path: Path):
    result = run_entrypoint("this_module_does_not_exist", cwd=tmp_path)

    assert result.returncode != 0
    assert "No module named" in result.stderr
```

(Added after code review: `timeout`, UTF-8-explicit decoding, and two regression
tests — stderr capture, and pinning that a missing module comes back as data in
`EntrypointResult` rather than raising, so a future `check=True` accident is caught.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_subprocess_runner.py -v`
Expected: `ModuleNotFoundError: No module named 'databricks_local_ci.subprocess_runner'`
(collection error — the module doesn't exist yet).

- [ ] **Step 3: Write the implementation**

`src/databricks_local_ci/subprocess_runner.py`:
```python
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EntrypointResult:
    """Result of running a module's entry point as a subprocess."""

    returncode: int
    stdout: str
    stderr: str


def run_entrypoint(
    module: str,
    args: list[str] | None = None,
    cwd: str | Path | None = None,
    timeout: float | None = None,
) -> EntrypointResult:
    """Runs a Python module as a subprocess and captures its result.

    Args:
        module: Dotted module path to execute via `python -m`.
        args: Command-line arguments to pass to the module.
        cwd: Working directory to run the subprocess in.
        timeout: Seconds to wait before raising `subprocess.TimeoutExpired`,
            or None to wait indefinitely.

    Returns:
        The subprocess's exit code, stdout, and stderr.
    """
    completed = subprocess.run(
        [sys.executable, "-m", module, *(args or [])],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        timeout=timeout,
    )
    return EntrypointResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_subprocess_runner.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add src/databricks_local_ci/subprocess_runner.py tests/test_subprocess_runner.py
git commit -m "feat: add subprocess-based entry-point runner"
```

---

## Task 3: Dockerfile and DBR smoke test

**This task requires Docker Desktop running.** Confirm first with `docker info` —
if that fails, stop and get Docker running before continuing; every remaining task
depends on it.

**Files:**
- Create: `docker/Dockerfile`
- Create: `.dockerignore`
- Create: `docker/smoke_test.py`

- [ ] **Step 0: Write the build-context ignore file**

`.dockerignore` at the **repo root** (Docker only auto-applies a `.dockerignore`
found in the build context directory — Step 3's build command uses `-f
docker/Dockerfile .`, so the context is the repo root, not `docker/`; a
`docker/.dockerignore` would silently do nothing). Without this, the whole repo —
including the multi-hundred-MB host `.venv/` — gets sent to the Docker daemon on
every build:
```
.venv/
.git/
.worktrees/
docs/
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
dist/
build/
```

- [ ] **Step 1: Write the Dockerfile**

`docker/Dockerfile`:
```dockerfile
FROM databricksruntime/python:15.4-LTS

RUN pip install --no-cache-dir pyspark==3.5.3 delta-spark==3.2.1 "pytest>=8,<10" "build>=1.0,<2" \
    && ln -sf "$(command -v python3.11)" /usr/local/bin/python

RUN python -c "from delta import configure_spark_with_delta_pip; from pyspark.sql import SparkSession; b = SparkSession.builder.appName('warm-cache').master('local[1]').config('spark.sql.extensions', 'io.delta.sql.DeltaSparkSessionExtension').config('spark.sql.catalog.spark_catalog', 'org.apache.spark.sql.delta.catalog.DeltaCatalog'); s = configure_spark_with_delta_pip(b).getOrCreate(); s.stop()"

WORKDIR /workspace
```

(The second `RUN` pre-warms Delta's Ivy/Maven dependency cache into the image layer
at build time. Without it, `configure_spark_with_delta_pip` re-resolves the Delta
JAR from Maven Central on every single `docker run` — a real reliability and speed
cost for a framework whose whole point is fast, dependency-free local testing.
`pytest`/`build` are pinned to match `pyproject.toml`'s dev extras, so the container
never silently drifts from what Task 1 validated on the host.)

(Corrected after Task 3 implementation surfaced this empirically: unlike the base
Databricks Runtime service, the `databricksruntime/python` *Docker image* does not
bundle PySpark at all — it's a bare Python base image for Databricks Container
Services, which normally has Spark injected by the Databricks control plane at
cluster boot. So we install `pyspark==3.5.3` ourselves to match DBR 15.4 LTS's
Spark 3.5.0, rather than relying on `--no-deps` to preserve a preinstalled copy
that doesn't exist. The image also has no unversioned `python` on `PATH` — only
`python3`, which resolves to Python 3.10, while the image's `pip` installs into
Python 3.11's site-packages — so we symlink `python` to `python3.11` to keep the
interpreter that runs code consistent with the one `pip` installs into.)

- [ ] **Step 2: Write the smoke test script**

`docker/smoke_test.py`:
```python
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
```

- [ ] **Step 3: Build the image**

Run (from the repo root):
```bash
docker build -t databricks-local-ci:15.4-lts -f docker/Dockerfile .
```
Expected: build completes successfully, ending with
`Successfully tagged databricks-local-ci:15.4-lts` (or the buildkit equivalent
`naming to docker.io/library/databricks-local-ci:15.4-lts`).

- [ ] **Step 4: Run the smoke test inside the container**

```bash
docker run --rm -v "$(pwd)/docker:/workspace/docker" databricks-local-ci:15.4-lts \
  python docker/smoke_test.py
```
Expected: last line of output is `SMOKE_OK`.

(On Windows Git Bash, `-v` bind-mount paths can get mangled by MSYS path
conversion — if you see something like `ls: cannot access '/workspace/docker'`,
prefix the command with `MSYS_NO_PATHCONV=1`.)

- [ ] **Step 4b: Confirm the Delta JAR cache warm-up actually removed the network dependency**

```bash
docker run --rm --network none -v "$(pwd)/docker:/workspace/docker" databricks-local-ci:15.4-lts \
  python docker/smoke_test.py
```
Expected: still prints `SMOKE_OK`, with no network access at all (`--network none`).
If this fails but Step 4 (with network) passed, the Step 1 cache warm-up isn't
actually caching what `smoke_test.py` resolves at runtime — investigate before
moving on, since every later task's pytest run depends on this working offline.

- [ ] **Step 5: Commit**

```bash
git add docker/Dockerfile .dockerignore docker/smoke_test.py
git commit -m "feat: add DBR-based Docker image and smoke test"
```

---

## Task 4: Shared pytest fixtures (Spark/Delta) — run inside Docker

**Files:**
- Create: `src/databricks_local_ci/fixtures.py`
- Modify: `pyproject.toml`
- Test: `tests/test_fixtures.py`

- [ ] **Step 0a: Create a placeholder `fixtures.py`**

`src/databricks_local_ci/fixtures.py`: an empty file for now. It must exist
*before* the pytest plugin entry point below is registered, or every `pytest`
invocation in this venv crashes at plugin-loading time with a
`ModuleNotFoundError` instead of a clean test failure.

- [ ] **Step 0b: Register the pytest plugin entry point**

Add this section to `pyproject.toml` (deferred from Task 1 specifically until
this module exists):
```toml
[project.entry-points.pytest11]
databricks_local_ci = "databricks_local_ci.fixtures"
```
Then reinstall so the entry point takes effect: `pip install -e ".[dev]"`.

- [ ] **Step 1: Write the failing test**

`tests/test_fixtures.py`:
```python
def test_local_spark_session_reads_and_writes_delta(local_spark_session, local_delta_table_path):
    df = local_spark_session.createDataFrame([(1, "a"), (2, "b")], ["id", "value"])
    df.write.format("delta").mode("overwrite").save(local_delta_table_path)

    result = local_spark_session.read.format("delta").load(local_delta_table_path)

    assert result.count() == 2
    assert sorted(row["value"] for row in result.collect()) == ["a", "b"]
```

- [ ] **Step 2: Run the test inside the container to verify it fails**

```bash
docker run --rm -v "$(pwd):/workspace/project" -w /workspace/project \
  databricks-local-ci:15.4-lts bash -c "pip install --no-deps -e . && pytest tests/test_fixtures.py -v"
```
Expected: `fixture 'local_spark_session' not found`.

- [ ] **Step 3: Write the implementation (replacing the placeholder)**

`src/databricks_local_ci/fixtures.py`:
```python
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
```

- [ ] **Step 4: Run the test inside the container to verify it passes**

```bash
docker run --rm -v "$(pwd):/workspace/project" -w /workspace/project \
  databricks-local-ci:15.4-lts bash -c "pip install --no-deps -e . && pytest tests/test_fixtures.py -v"
```
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add src/databricks_local_ci/fixtures.py pyproject.toml tests/test_fixtures.py
git commit -m "feat: add local_spark_session and local_delta_table_path fixtures"
```

---

## Task 5: Example job — transform logic (TDD, run inside Docker)

**Files:**
- Create: `examples/example_job/pyproject.toml`
- Create: `examples/example_job/src/example_job/__init__.py`
- Create: `examples/example_job/src/example_job/transform.py`
- Test: `examples/example_job/tests/test_transform.py`

- [ ] **Step 1: Create the example project's `pyproject.toml`**

`examples/example_job/pyproject.toml`:
```toml
[project]
name = "example-job"
version = "0.1.0"
description = "Reference Databricks Job used to demonstrate databricks-local-ci"
requires-python = ">=3.10"
dependencies = []

[project.scripts]
example-job = "example_job.main:main"

[project.optional-dependencies]
dev = [
    "pytest>=8,<10",
    "databricks-local-ci @ file://../..",
]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Create the package init file**

`examples/example_job/src/example_job/__init__.py`:
```python
```
(empty file — just marks the directory as a package)

- [ ] **Step 3: Write the failing test**

`examples/example_job/tests/test_transform.py`:
```python
from example_job.transform import summarize_sales_by_category


def test_summarize_sales_by_category(local_spark_session):
    sales_df = local_spark_session.createDataFrame(
        [("books", 10.0), ("books", 5.0), ("toys", 20.0)],
        ["category", "amount"],
    )

    result = summarize_sales_by_category(sales_df).orderBy("category")

    rows = {row["category"]: row["total_amount"] for row in result.collect()}
    assert rows == {"books": 15.0, "toys": 20.0}
```

- [ ] **Step 4: Run the test inside the container to verify it fails**

```bash
docker run --rm -v "$(pwd):/workspace/project" -w /workspace/project/examples/example_job \
  databricks-local-ci:15.4-lts bash -c "pip install --no-deps -e /workspace/project && pip install -e . && pytest tests/test_transform.py -v"
```
Expected: `ModuleNotFoundError: No module named 'example_job.transform'`.

- [ ] **Step 5: Write the implementation**

`examples/example_job/src/example_job/transform.py`:
```python
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
```

- [ ] **Step 6: Run the test inside the container to verify it passes**

```bash
docker run --rm -v "$(pwd):/workspace/project" -w /workspace/project/examples/example_job \
  databricks-local-ci:15.4-lts bash -c "pip install --no-deps -e /workspace/project && pip install -e . && pytest tests/test_transform.py -v"
```
Expected: `1 passed`

- [ ] **Step 7: Commit**

```bash
git add examples/example_job/pyproject.toml examples/example_job/src/example_job/__init__.py \
        examples/example_job/src/example_job/transform.py examples/example_job/tests/test_transform.py
git commit -m "feat: add example job transform logic"
```

---

## Task 6: Example job — CLI entry point (the thing the "real run" actually invokes)

**Files:**
- Create: `examples/example_job/src/example_job/main.py`

- [ ] **Step 1: Write the entry point**

This is intentionally not covered by its own unit test — it's thin argument-parsing
wiring around `transform.py` (already tested in Task 5) and is exercised end-to-end
by the integration test in Task 7.

`examples/example_job/src/example_job/main.py`:
```python
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
```

- [ ] **Step 2: Commit**

```bash
git add examples/example_job/src/example_job/main.py
git commit -m "feat: add example job CLI entry point"
```

---

## Task 7: Integration test — the "real run" (run inside Docker)

This is the flagship test: it writes a local input Delta table, then invokes the
job's real entry point via subprocess (not an import), and asserts on the Delta
table the subprocess wrote — a genuine execution of the shipped artifact.

**Files:**
- Test: `examples/example_job/tests/test_integration.py`

- [ ] **Step 1: Write the failing test**

`examples/example_job/tests/test_integration.py`:
```python
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
```

- [ ] **Step 2: Run the test inside the container to verify it fails**

```bash
docker run --rm -v "$(pwd):/workspace/project" -w /workspace/project/examples/example_job \
  databricks-local-ci:15.4-lts bash -c "pip install --no-deps -e /workspace/project && pip install -e . && pytest tests/test_integration.py -v"
```
Expected: fails at the `run_entrypoint(...)` assertion (`result.returncode == 0`) —
`example_job.main` isn't installed as a console script/module the subprocess's
Python can see unless the previous `pip install -e .` step succeeded; if it errors
with `No module named example_job.main` instead, confirm Task 5–6 were committed.

- [ ] **Step 3: Fix forward if needed, then run again to verify it passes**

```bash
docker run --rm -v "$(pwd):/workspace/project" -w /workspace/project/examples/example_job \
  databricks-local-ci:15.4-lts bash -c "pip install --no-deps -e /workspace/project && pip install -e . && pytest tests/test_integration.py -v"
```
Expected: `1 passed`

- [ ] **Step 4: Run the full example test suite together as a final check**

```bash
docker run --rm -v "$(pwd):/workspace/project" -w /workspace/project/examples/example_job \
  databricks-local-ci:15.4-lts bash -c "pip install --no-deps -e /workspace/project && pip install -e . && pytest tests/ -v"
```
Expected: `2 passed` (`test_transform.py` and `test_integration.py`).

- [ ] **Step 5: Commit**

```bash
git add examples/example_job/tests/test_integration.py
git commit -m "test: add real-run integration test for example job"
```

---

## Task 8: Reusable GitHub Actions workflow

**Files:**
- Create: `.github/workflows/databricks-ci.yml`

- [ ] **Step 1: Write the reusable workflow**

`.github/workflows/databricks-ci.yml`:
```yaml
name: Databricks CI

on:
  workflow_call:
    inputs:
      dbr_version:
        description: "databricksruntime/python tag to test against, e.g. 15.4-LTS"
        required: true
        type: string
      package_dir:
        description: "Path (relative to the caller repo root) to the job package"
        required: true
        type: string
      bundle_target:
        description: "Databricks Asset Bundle target to deploy, e.g. prod"
        required: true
        type: string
    secrets:
      databricks_host:
        required: true

jobs:
  build-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build DBR-based test image
        run: |
          docker build -t databricks-local-ci:${{ inputs.dbr_version }} \
            --build-arg DBR_TAG=${{ inputs.dbr_version }} \
            -f docker/Dockerfile .

      - name: Build wheel and run the real-run integration tests inside the container
        run: |
          docker run --rm -v "${{ github.workspace }}:/workspace/project" \
            -w "/workspace/project/${{ inputs.package_dir }}" \
            databricks-local-ci:${{ inputs.dbr_version }} \
            bash -c "python -m build --wheel && pip install dist/*.whl && pytest tests/ -v"

  deploy:
    needs: build-and-test
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v4

      - uses: databricks/setup-cli@main

      - name: Deploy bundle
        working-directory: ${{ inputs.package_dir }}
        env:
          DATABRICKS_HOST: ${{ secrets.databricks_host }}
          DATABRICKS_AUTH_TYPE: github-oidc
        run: databricks bundle deploy --target ${{ inputs.bundle_target }}
```

- [ ] **Step 2: Make the Dockerfile's base tag configurable via build-arg**

The workflow passes `--build-arg DBR_TAG=...`, so the Dockerfile needs to accept it.

Modify `docker/Dockerfile` (from Task 3) — replace the first line:
```dockerfile
ARG DBR_TAG=15.4-LTS
FROM databricksruntime/python:${DBR_TAG}
```

- [ ] **Step 3: Verify the workflow file is valid YAML**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/databricks-ci.yml'))" && echo VALID_YAML
```
Expected: `VALID_YAML`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/databricks-ci.yml docker/Dockerfile
git commit -m "feat: add reusable GitHub Actions workflow (build-and-test + deploy)"
```

**Not verifiable from this repo alone:** the `deploy` job needs a Databricks
workspace with OIDC federation to a Service Principal already configured
(Databricks side), and a `databricks_host` secret set on the *calling* repo.
Before relying on this in production, confirm the exact `DATABRICKS_AUTH_TYPE`
value and `databricks/setup-cli` usage against Databricks's current OIDC setup
docs — this integration evolves and this draft was not tested against a real
workspace.

---

## Task 9: README for consuming projects

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the README**

`README.md`:
```markdown
# databricks-local-ci

Reusable framework that runs a real execution of a Databricks Python Job
inside the exact Databricks Runtime (DBR) version used in production, and
only deploys via Databricks Asset Bundles if that run succeeds. See
`docs/superpowers/specs/2026-09-13-databricks-local-ci-design.md` for the
full design rationale, scope, and known limitations (no Unity Catalog,
notebooks, or Lakeflow pipelines in v1).

## What your project must provide

- A single CLI entry point (a `console_scripts` entry, invoked as
  `python -m your_package.main ...`) — the same one your Databricks Job task
  invokes in production.
- Table/data locations passed as CLI parameters, never hardcoded — in
  production they're Unity Catalog-governed paths, in tests they're local
  Delta paths.
- Tests under `tests/`, using the `local_spark_session` and
  `local_delta_table_path` fixtures (auto-registered once
  `databricks-local-ci` is installed) plus
  `databricks_local_ci.subprocess_runner.run_entrypoint` to invoke your real
  entry point. See `examples/example_job` for a complete reference.

## Consuming the workflow

In your project's own `.github/workflows/ci.yml`:

```yaml
name: CI
on: [pull_request]

jobs:
  databricks-ci:
    uses: <org>/databricks-local-ci/.github/workflows/databricks-ci.yml@main
    with:
      dbr_version: "15.4-LTS"
      package_dir: "."
      bundle_target: "prod"
    secrets:
      databricks_host: ${{ secrets.DATABRICKS_HOST }}
```

Pin `@main` to a tagged release once this framework has one.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README for consuming projects"
```

---

## Verification (end-to-end)

Run once Docker Desktop is confirmed working:

1. `docker info` succeeds.
2. Task 3 Steps 3–4 (`docker build` + smoke test) print `SMOKE_OK`.
3. Task 7 Step 4 (`pytest tests/ -v` inside the container, from
   `examples/example_job`) prints `2 passed`.
4. Task 8 Step 3 confirms the workflow YAML parses.
5. Manually trigger the workflow from a throwaway consumer repo (or a branch
   of this repo with a dummy caller workflow) with `bundle_target` pointed at
   a sandbox workspace, and confirm `build-and-test` runs before `deploy` is
   attempted, and that `deploy` fails cleanly on missing OIDC setup rather
   than silently no-opping — expected until the Databricks-side OIDC
   federation is configured.
