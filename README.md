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
    uses: <org>/databricks-local-ci/.github/workflows/databricks-ci.yml@master
    with:
      dbr_version: "15.4-LTS"
      package_dir: "."
      bundle_target: "prod"
    secrets:
      databricks_host: ${{ secrets.DATABRICKS_HOST }}
```

Pin `@master` to a tagged release once this framework has one.

## Known limitations (read before adopting)

- **Not yet validated for external consumer repos.** The `build-and-test` job
  installs `databricks-local-ci` via `pip install --no-deps -e
  <checked-out-repo>` — proven so far only against this monorepo testing its
  own bundled `examples/example_job`, where the framework's source and the
  example live in the same checkout. A real external repo doesn't have this
  framework's source in its own checkout, so this install step won't resolve
  correctly there yet. Until `databricks-local-ci` is published (PyPI, or a
  pinned git URL consumers can add to their own `pyproject.toml` dev extra),
  treat this workflow as proven for in-repo dogfooding only.
- **Don't declare `databricks-local-ci` as a relative `file://` path
  dependency.** An earlier draft of `examples/example_job/pyproject.toml` tried
  `databricks-local-ci @ file://../..` in its `dev` extra — pip rejects
  relative `file://` URIs outright (`non-local file URIs are not supported on
  this platform`). If you need a local, editable install for development,
  install it as a separate `pip install -e <path>` command, not as a
  dependency string.
- **The `.master()` omission in your job's `SparkSession` builder depends on
  the DBR image defaulting to local mode.** `example_job/main.py` never calls
  `.master(...)` — production Databricks Jobs get that from the platform, and
  `databricksruntime/python:15.4-LTS` happens to default to local mode when
  none is set. If you pin a different `dbr_version`, sanity-check that it
  still defaults to local mode (copy the pattern from `docker/smoke_test.py`
  in this repo) before trusting your own job's tests — a tag that doesn't
  default to local mode will hang or fail with a confusing "must set a master
  URL" error instead of an obvious one.
- **Run tests inside the Docker container, not directly on a Windows host.**
  PySpark's Java gateway fails to launch on Windows when the checkout path
  contains non-ASCII characters (confirmed during this project's own
  development — its repo happened to live under a path with an accented
  character). This has nothing to do with your code; it's a PySpark-on-Windows
  classpath quirk. Always run `pytest` inside the `databricksruntime`-based
  container, never on the bare host, and this never comes up.
- **`docker build` needs network access to Maven Central.** The image's second
  build layer resolves Delta Lake's JAR dependencies via Ivy/Maven so later
  `docker run`s don't need network access — but that means the *build* itself
  does. A restrictive corporate proxy or an air-gapped self-hosted runner will
  make `docker build` fail at that step; there's no offline fallback in v1.
- **The `deploy` job's OIDC setup is unverified against a real Databricks
  workspace.** It needs a Service Principal already federated to your GitHub
  repo's OIDC issuer (Databricks-side setup, not something this workflow does
  for you), and a `databricks_host` secret on the *calling* repo. Confirm the
  exact `DATABRICKS_AUTH_TYPE` value and `databricks/setup-cli` usage (pinned
  to `v1.12.1` here — bump deliberately) against Databricks's current OIDC
  docs before relying on this in production.
- **No Unity Catalog, secrets, cluster policies, or endpoints are exercised
  anywhere in this framework** — the local container has none of the
  Databricks control plane. This is by design (see the linked design doc), not
  a gap to file an issue about.
