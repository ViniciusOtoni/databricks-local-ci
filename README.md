# databricks-local-ci

Reusable framework that runs a real execution of a Databricks Python Job
inside the exact Databricks Runtime (DBR) version used in production, and
only deploys via Databricks Asset Bundles if that run succeeds. See
`docs/superpowers/specs/2026-09-13-databricks-local-ci-design.md` for the
full design rationale, scope, and known limitations (no Unity Catalog,
notebooks, or Lakeflow pipelines in v1).

## What your project must provide

- A single CLI entry point (invoked as `python -m your_package.main
  --input-path ... --output-path ...`) — the same one your Databricks Job
  task invokes in production. `--input-path`/`--output-path` are a fixed
  convention this framework relies on, not just a suggestion.
- Table/data locations passed as CLI parameters, never hardcoded — in
  production they're Unity Catalog-governed paths, in tests they're local
  Delta paths.

That's it — with those two things in place, you get a real "run the actual
packaged wheel" test **for free, with no test file to write**. Add this to
your `pyproject.toml`:

```toml
[tool.databricks-local-ci]
entry_point = "your_package.main"

[tool.databricks-local-ci.sample_input]
columns = ["region", "amount"]
rows = [
    ["us-east", 100.0],
    ["eu-west", 200.0],
]
```

Once `databricks-local-ci` is installed, its pytest plugin reads this block
and injects a test automatically: it builds a Delta table from `sample_input`,
invokes your entry point as a genuine subprocess with `--input-path`/
`--output-path` pointing at local temp paths, asserts it exits `0`, and
asserts it actually wrote a non-empty output table. No `test_integration.py`,
no manual `run_entrypoint` call — see `examples/example_job/pyproject.toml`
for the reference. This only proves the packaged artifact *runs*; it can't
know what "correct" means for your business logic.

For that, keep testing your transform functions directly with the
`local_spark_session` fixture (also auto-registered) the same way you'd unit
test any other function — see `examples/example_job/tests/test_transform.py`.
And if you need an integration test with *custom* assertions beyond "ran and
wrote something" — e.g. checking exact aggregated values end to end — use
`databricks_local_ci.subprocess_runner.run_entrypoint` directly in a
hand-written test; it's the same primitive the auto-generated test uses under
the hood, just available for you to call yourself.

## Consuming the workflows

Two separate reusable workflows: `databricks-ci.yml` builds the DBR image,
installs `databricks-local-ci`, builds your wheel with `uv`, runs your tests
inside the container, and uploads the wheel as a build artifact.
`databricks-cd.yml` downloads that artifact and runs `databricks bundle deploy`
— it never runs unless CI succeeded first. It supports two authentication
methods (`auth_method` input): `oidc` (default — a Service Principal federated
to GitHub via OIDC, no long-lived secret, but **requires account console
access**) and `pat` (a personal access token — works on **Databricks Free
Edition**, which has no account console and so cannot use Service
Principal/OIDC federation at all). Wire them together in your project's own
`.github/workflows/ci-cd.yml`:

```yaml
name: CI/CD
on: [pull_request, push]

jobs:
  test:
    uses: <org>/databricks-local-ci/.github/workflows/databricks-ci.yml@master
    with:
      dbr_version: "15.4-LTS"
      package_dir: "."

  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    permissions:
      id-token: write
      contents: read
    uses: <org>/databricks-local-ci/.github/workflows/databricks-cd.yml@master
    with:
      package_dir: "."
      bundle_target: "prod"
      auth_method: "oidc"  # or "pat" — see below
    secrets:
      databricks_host: ${{ secrets.DATABRICKS_HOST }}
      databricks_client_id: ${{ secrets.DATABRICKS_CLIENT_ID }}  # oidc only
      databricks_token: ${{ secrets.DATABRICKS_TOKEN }}          # pat only
```

**`auth_method: "oidc"`** (default, recommended when you have it): a Service
Principal federated to your GitHub repo's OIDC issuer. `databricks_client_id`
is the Service Principal's application (client) ID — Databricks's GitHub OIDC
federation resolves the SPN from this ID plus the `github-oidc` auth type;
`databricks_host` alone isn't enough to authenticate. **This requires account
console access, which Databricks Free Edition does not have** — trying this
on Free Edition fails with `TOKEN_INVALID (Ensure a valid federation policy
has been configured)` no matter how the policy is configured, because Free
Edition cannot create federation policies at all (confirmed: no account
console, no account-level APIs, no Service Principal OAuth).

**`auth_method: "pat"`** (works on Free Edition): a classic personal access
token, generated per-user from **workspace** Settings → Developer → Access
tokens (a workspace-level feature, unlike Service Principals) and stored as
the `databricks_token` secret. Less secure than OIDC (a real long-lived bearer
credential sitting in GitHub Secrets, not a short-lived federated token) — use
`oidc` whenever your workspace tier supports it, and treat `pat` as the Free
Edition / no-account-console fallback, not the default recommendation.

**The `permissions: id-token: write` block on the `deploy` job above is not
optional, even when using `auth_method: "pat"`.** Without it, GitHub Actions
fails the whole run before any job starts, with: `The nested job 'deploy' is
requesting 'id-token: write', but is only allowed 'id-token: none'.` A
reusable workflow's job can only be granted permissions up to what the
*calling* job already has — `databricks-cd.yml` requesting `id-token: write`
internally isn't enough on its own; your caller job needs to grant it too.
(Confirmed by actually hitting this exact failure wiring up a real consumer
repo.)

Pin `@master` to a tagged release once this framework has one.

## Known limitations (read before adopting)

- **Add `databricks-local-ci` to your own project's `dev` extra as a git
  dependency** — `databricks-ci.yml` only runs `pip install -e ".[dev]"`
  inside the container; it doesn't install the framework separately. See
  `examples/example_job/pyproject.toml` for the exact syntax:
  `databricks-local-ci @ git+https://github.com/<org>/databricks-local-ci.git@master`.
  This is genuinely proven to work for a repo other than this one's own bundled
  example (`example_job`'s own CI now installs it this same way — via a real
  `git clone` of this public repo — not via a local path shortcut), and for a
  genuinely separate consumer repo
  ([`databricks-job-example`](https://github.com/ViniciusOtoni/databricks-job-example))
  wiring these workflows up over `uses:` for real.
- **Don't declare `databricks-local-ci` as a relative `file://` path
  dependency.** An earlier draft of `examples/example_job/pyproject.toml` tried
  `databricks-local-ci @ file://../..` in its `dev` extra — pip rejects
  relative `file://` URIs outright (`non-local file URIs are not supported on
  this platform`). Use a `git+https://` URL (see above) instead.
- **The Docker image needs `git` on `PATH` to resolve a `git+https://` dev
  dependency.** `databricksruntime/python` doesn't ship it — the Dockerfile
  installs it via `apt-get install git`. If you customize the Dockerfile,
  don't drop this layer.
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
- **OIDC needs a Service Principal already federated to your GitHub repo's
  OIDC issuer** (Databricks-side setup, not something this workflow does for
  you) — and, as covered above, that setup is simply unavailable on Free
  Edition. Verified for real against `databricks-job-example`: the workflow
  mechanics (checkout, `databricks/setup-cli@v1.12.1`, `id-token: write`
  propagation) all work correctly — the run reaches Databricks's OIDC token
  endpoint and gets back a real, specific `TOKEN_INVALID` response, not a
  workflow-level failure. Use `auth_method: "pat"` if you don't have account
  console access.
- **The CD workflow assumes your bundle config expects the wheel at
  `<package_dir>/dist/*.whl`.** `databricks-cd.yml` downloads the artifact
  straight into that path before running `databricks bundle deploy`. If your
  `databricks.yml` references the wheel from a different location, adjust
  where you point your bundle's `artifacts` block, not this workflow.
- **No Unity Catalog, secrets, cluster policies, or endpoints are exercised
  anywhere in this framework** — the local container has none of the
  Databricks control plane. This is by design (see the linked design doc), not
  a gap to file an issue about.
