# Cost & Efficiency Study: Local Validation vs. Cloud-Only Validation on Azure Databricks

**Scope:** what it actually costs, in Azure/Databricks line items, to find out a Python
Databricks Job is broken *on the cloud cluster* — versus finding out with
`databricks-local-ci`, before any cluster ever spins up. Covers Compute, Storage,
Network, and Licensing, using a multi-node Azure `D`-family cluster as the reference
architecture.

**Pricing basis:** Azure public, pay-as-you-go, on-demand list prices, East US region,
gathered while writing this document. **Cloud pricing changes and varies by region,
negotiated/enterprise agreement, and reserved-capacity discounts (Azure Databricks
offers ~33%/37% off for 1-/3-year commitments).** Every number below is cited to its
source category and marked either "confirmed" (found a specific published rate) or
"industry-typical estimate" (a widely-cited pattern I could not pull a live, specific
number for — Databricks' own DBU-per-instance-type table is served from an interactive
calculator this document's author could not scrape). **Re-verify all rates against the
[Azure Databricks pricing calculator](https://azure.microsoft.com/en-us/pricing/details/databricks/)
and the [Azure pricing calculator](https://azure.microsoft.com/en-us/pricing/calculator/)
for your own region and commitment tier before using this for a real budget.** The
point of this document is the *shape* of the trade-off and a reusable formula, not a
guaranteed-current invoice.

## Reference architecture

A small-to-medium job cluster, multi-node, `D`-family (general purpose — the family
this study was asked to use, and the default recommendation for non-GPU, non-memory-
optimized ETL/Spark workloads):

| Role | Count | VM size | vCPU | RAM |
|---|---|---|---|---|
| Driver | 1 | `Standard_D4s_v5` | 4 | 16 GiB |
| Worker | 4 | `Standard_D4s_v5` | 4 | 16 GiB |
| **Total** | **5 nodes** | | **20** | **80 GiB** |

A scaled-up variant (`Standard_D8s_v5` ×5, double the size) is used later for
sensitivity analysis.

## 1. Compute + Licensing (the dominant cost, billed together per node-hour)

Every Databricks node on Azure generates **two separate charges on the same
Azure invoice**: Microsoft bills the VM (infrastructure), and Databricks bills the DBU
consumption (the software license fee, sold through Azure Marketplace metered
billing). This is why "Compute" and "Licença" are really one number in practice —
splitting them out here because the question asked for both:

| Component | Rate | Source |
|---|---|---|
| `Standard_D4s_v5` on-demand, East US | **$0.192/hour** | Confirmed — Azure retail pricing, per-VM listings (Vantage/CloudPrice), September 2026 |
| Jobs Compute DBU price, Azure Premium tier | **$0.15/DBU** | Confirmed — published Azure Databricks Jobs Compute rate, September 2026 |
| DBU consumption for a 4 vCPU/16 GiB general-purpose node (`D4s_v5`-class) | **≈1 DBU/hour** | Industry-typical estimate — Databricks assigns a fixed DBU/hour figure per instance type, roughly proportional to vCPU/RAM; ~1 DBU/hour is the long-standing, widely-cited figure for this node class. **Confirm the exact current figure for `D4s_v5` specifically in the calculator** — Databricks revises these tables per generation. |

**Per-node hourly cost** = VM ($0.192) + DBU (1 × $0.15) = **$0.342/node/hour**

**5-node reference cluster** = 5 × $0.342 = **$1.71/hour**

This matches the commonly-cited pattern that the DBU (license) charge on Azure adds
roughly another 30–80% on top of the raw VM cost, depending on instance type and
tier — here it's +78% ($0.15 DBU vs. $0.192 VM).

Two things Databricks *also* bills as DBUs that aren't captured in a raw hourly rate
and are not modeled further in this document because they don't change the local-vs-
cloud comparison: Delta/photon acceleration multipliers (Photon roughly doubles the
effective DBU rate per node when enabled) and Unity Catalog governance overhead
(negligible per-job, billed separately as its own small DBU line item). If your real
jobs use Photon, multiply the DBU line above accordingly.

## 2. Cold-start overhead — the part that makes a *failed* cloud attempt expensive

A Databricks job cluster is billed from the moment it starts provisioning, not from
when your code starts executing. Cluster creation — VM boot, network setup, Spark/
runtime initialization — is widely documented to take **roughly 4–8 minutes** before
task code runs. This study uses **6 minutes (0.1 hour)** as the representative
cold-start figure.

This means: **a job that crashes in the first 10 seconds after cluster startup still
bills the full ~6 minutes of provisioning**, on top of whatever the crash itself took.
This is the core economic asymmetry this framework exploits — catching the bug before
it reaches the cluster means the cluster's cold-start cost is never paid at all, not
even partially.

## 3. Cost of one cloud validation attempt

A representative scenario: a job runs for **10 minutes** of actual Spark execution
(reasonable for a small-to-medium ETL task) on the 5-node reference cluster.

| | Time billed | Cost |
|---|---|---|
| Cold start | 6 min | 6/60 × $1.71 = **$0.171** |
| Job execution | 10 min | 10/60 × $1.71 = **$0.285** |
| **Total per attempt** | **16 min** | **$0.456** |

**A failed attempt (bug caught by the cluster instead of by you) costs the same
$0.456 and 16 minutes — for zero usable output.** The compute doesn't know or care
that the job crashed; you pay for the wall-clock time the cluster was alive either
way.

## 4. Cost of one local validation cycle (this framework)

`databricks-ci.yml`'s `build-and-test` job was timed for real, repeatedly, against
`databricks-job-example` during this project's own development (see that repo's
GitHub Actions history): consistently **~2–2.5 minutes** wall-clock for the full
sequence — build the DBR-based image (cached after the first run), install the
framework and dev deps, build the wheel with `uv`, install it, and run the real
subprocess-based test.

| Where it runs | Cost |
|---|---|
| Local machine (Docker Desktop) | **$0** marginal cloud cost — developer hardware already paid for |
| GitHub Actions, public repo | **$0** — GitHub Actions is free for public repositories regardless of minutes used |
| GitHub Actions, private repo, within free tier | **$0** — GitHub Free/Team plans include 2,000–3,000 free Linux minutes/month |
| GitHub Actions, private repo, beyond free tier | 2.3 min × **$0.008/min** (published GitHub Actions Linux runner rate) ≈ **$0.018** |

**Worst case, a private repo already past its free CI minutes: ~$0.02 per validation
cycle** — roughly **23x cheaper** than one cloud attempt, and that's the pessimistic
case; for a public repo or a private repo within its free tier, it's **exactly $0**.

## 5. Storage

This is the axis worth being precise about, because it's easy to reason about
incorrectly (see the design-doc discussion this study grew out of: a Job's *definition*
is Databricks control-plane metadata, not billed storage — only actual bytes written
to managed storage cost anything).

What a `databricks bundle deploy` actually writes to managed/default storage: a
snapshot of the bundle's files (wheel + small YAML/config) at
`/Workspace/.../.snapshots/<bundle-id>/<deployment-id>/...`, once per deploy.

| | Value |
|---|---|
| Typical wheel + bundle config snapshot size | ~5 MB (generous estimate for a small Python job package) |
| Azure Data Lake Storage Gen2, Hot tier, LRS | **$0.021/GB/month** (confirmed, first 50 TB tier, September 2026) |
| Cost of one snapshot | 0.0049 GB × $0.021 ≈ **$0.0001/month** |
| Cost of 365 snapshots (one deploy every day for a year, never cleaned up) | 1.825 GB × $0.021 ≈ **$0.038/month** |

**Storage cost from deploy artifacts is not a meaningful line item at realistic job
sizes and deploy cadences** — even a full year of daily deploys, never garbage
collected, costs under 4 cents a month. This directly quantifies the answer from the
earlier design discussion: the number of Job *resources* materialized in a workspace
does not drive storage cost; deploy/run *frequency* combined with data *volume* does
— and for typical CI/CD-triggered deploys of small wheels, that volume is trivial.
(Geo-redundant storage, GRS, roughly doubles the per-GB rate — still trivial at this
scale.)

Where storage cost actually matters in a real Databricks project: the Delta
tables/checkpoints your jobs read and write as their actual business data. That cost
exists identically whether or not you adopt `databricks-local-ci` — it's a data
architecture decision, not a testing-strategy one, so it's out of scope for this
comparison.

## 6. Network

Same-region traffic between Databricks compute and Azure Storage (ADLS Gen2) is not
billed as egress — this is the default, common case for a job cluster reading/writing
its own workspace's storage. Egress charges apply to traffic leaving Azure entirely:

| Zone | Rate after first 100 GB/month free | Region examples |
|---|---|---|
| Zone 1 | **$0.087/GB** | US, Canada, EU, UK |
| Zone 3 | **$0.181/GB** | South America, Africa, Middle East |

(Confirmed, September 2026; tiers decrease further past 10 TB/month.)

For the failure-mode this framework targets — a job that crashes on a fresh cluster —
there is no meaningful *network* cost difference between catching it locally or on the
cloud: dependency downloads (PyPI, Maven Central) are *inbound* to Azure, which Azure
does not charge for, and same-region storage reads/writes aren't egress either.
Network cost is dominated by cross-region architecture and external data delivery,
neither of which this comparison touches. One concrete thing this framework already
does to reduce redundant *time* (not $, since inbound traffic is free) spent on this:
`docker/Dockerfile` pre-warms Delta's Maven/Ivy dependency resolution at image build
time specifically so repeated local test runs don't re-resolve it — the same
optimization a well-tuned cluster init script would apply for production, just moved
local.

## 7. Licensing, restated

Section 1 already prices the DBU (license) line explicitly. One structural note worth
recording here: **Azure Databricks' Standard tier is being retired** — new Standard
workspaces stopped being creatable April 1, 2026, and all remaining Standard
workspaces are being force-upgraded to Premium by October 1, 2026. Practically, this
means there is no "downgrade to a cheaper tier" lever left on Azure — Premium-tier DBU
rates (used throughout this document) are becoming the only rate that exists.

## 8. Side-by-side: one validation cycle

| | Cloud (5-node `D4s_v5` cluster) | Local (`databricks-local-ci`) |
|---|---|---|
| Compute + license | $0.456 | $0 |
| Storage (this cycle) | ~$0 (see §5) | $0 |
| Network | ~$0 (see §6) | $0 |
| Wall-clock time | ~16 minutes | ~2–2.5 minutes |
| **Total $** | **$0.456** | **$0–$0.02** |
| **Savings per cycle** | | **~$0.44 (≈96–100%), ~14 minutes** |

## 9. Extrapolating to a team's real cadence

The $0.44/cycle number looks small in isolation. It compounds with volume, and — more
importantly — **every cycle that would have failed on the cloud is pure waste
avoided, not just a discount**. Worked example, explicitly labeled as an assumption
you should replace with your own team's numbers:

**Assumption:** a small data team pushes 15 job changes/week; historically ~30% of
first attempts have a bug a "real run" would catch (import error, Spark/Delta
misconfiguration, a broken transform — exactly what Tasks 2–7 of this framework's own
development journal show it actually catching).

| | Without local validation | With `databricks-local-ci` |
|---|---|---|
| Attempts reaching the cloud cluster | 15/week | ~10.5/week (only ones that already passed locally) |
| Of those, wasted on a bug that crashes the cluster | 4.5/week × $0.456 = **$2.05/week wasted** | 0 — caught locally first |
| Local validation cost for all 15 attempts | — | 15 × ~$0.02 (worst case) = **$0.30/week** |
| **Net weekly compute spend on this pattern** | **$6.84/week** (15 × $0.456, success and failure alike) | **$0.30/week** local + **$4.79/week** cloud (10.5 genuinely-ready attempts) = **$5.09/week** |

Even in this conservative framing (still deploying every locally-passing attempt to
the cloud, no change there), **the pure-waste slice — $2.05/week, ~$106/year at this
volume — disappears entirely**, plus ~4.5 × 14 minutes ≈ **63 minutes/week** of
wall-clock waiting-then-fixing cycle time is eliminated. Scale the assumptions
(attempts/week, failure rate, cluster size) linearly for your own team.

## 10. Beyond infrastructure: engineer time (directionally important, not a hard number)

The dollar figures above are genuinely small in absolute terms for a modest cluster —
that's an honest finding of this study, not a sales pitch. The bigger lever most
teams actually feel is **engineer wall-clock time**: 14 minutes lost to a cloud
round-trip on a bug that a 2-minute local run would have caught is 14 minutes of
context-switch/waiting, repeated per bug, per engineer. At a illustrative (highly
variable, not researched — pick your own team's fully-loaded hourly cost) rate of
$50–100/hour, that's **$12–23 of engineer time per late-caught bug**, an order of
magnitude larger than the $0.456 of compute it also wasted. This document deliberately
keeps that number out of the "confirmed" cost tables above because it depends entirely
on team composition and location — but it's very likely the dominant real-world
saving, not the compute bill.

## 11. Sensitivity: bigger clusters

Doubling every node to `Standard_D8s_v5` (8 vCPU/32 GiB, ~2 DBU/hour/node — industry-
typical estimate, same caveat as §1):

| | `D4s_v5` ×5 (this study's reference) | `D8s_v5` ×5 |
|---|---|---|
| VM cost/node/hour | $0.192 | ~$0.384 (industry-typical: roughly linear with vCPU doubling) |
| DBU/node/hour | 1 | ~2 |
| Node/hour total | $0.342 | ~$0.684 |
| 5-node cluster/hour | $1.71 | ~$3.42 |
| Cost of one 16-minute cloud attempt | $0.456 | **~$0.912** |

**The savings from catching a bug locally scale linearly with cluster size** — on a
bigger cluster, the same local validation still costs $0–0.02, while the avoided
cloud attempt gets proportionally more expensive. Bigger, more expensive clusters make
this framework's case stronger, not weaker.

## 12. Recomputing this for your own numbers

```
cloud_attempt_cost = (cold_start_hours + job_runtime_hours) × Σ(node_vm_rate + node_dbu_rate × dbu_price)
local_attempt_cost = ci_minutes × github_actions_per_minute_rate   # often $0
savings_per_avoided_failure = cloud_attempt_cost - local_attempt_cost
weekly_savings = attempts_per_week × failure_rate × savings_per_avoided_failure
```

Pull `node_vm_rate` and `dbu_price` from the calculators linked at the top of this
document for your actual region, instance size, and commitment tier; pull
`ci_minutes` from your own `databricks-ci.yml` run history
(`gh run list --repo <you>/<your-job-repo>`).

## Sources

- [Azure Databricks pricing](https://azure.microsoft.com/en-us/pricing/details/databricks/) — Jobs Compute DBU rate, Premium/Standard tier status
- [Azure Virtual Machines pricing](https://azure.microsoft.com/en-us/pricing/details/virtual-machines/series/) — `Dsv5`-series on-demand rates
- [Azure Data Lake Storage Gen2 pricing](https://azure.microsoft.com/en-us/pricing/details/storage/data-lake/) — Hot tier, LRS/GRS
- [Azure Bandwidth pricing](https://azure.microsoft.com/en-us/pricing/details/bandwidth/) — egress by zone
- [GitHub Actions billing](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions) — per-minute Linux runner rate, free-tier minutes, free for public repos
- This project's own `databricks-ci.yml` run history against `databricks-job-example` — observed real wall-clock timings
