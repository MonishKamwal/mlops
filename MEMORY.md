# MEMORY.md — working memory for Claude Code sessions

This file is the handoff between machines and sessions (personal + work laptop). `PLAN.md`
is the long-term what-and-why; this file is the current state and the exact next move.

**Maintenance rules:**

- Update "Current state" and "Immediate next step" in the same commit as the work they
  describe. Progress log stays newest-first and terse; prune freely.
- Concepts learned along the way (AWS mechanics, tool behaviors, wrong turns) go in
  **`LEARNING.md`**, not here — that file is the learning journal and feeds the
  portfolio's Journey/devlog section. This file stays operational.

## Facts not derivable from the repo

- **The AWS account is on the post-July-2025 free plan.** Created ~July 2026 → plan ends
  ~Jan 2027 or when credits run out, whichever is first. $100 credits + up to $100 earnable.
  The account *cannot incur charges*. **The EKS control plane is NOT blocked** (verified
  2026-07-21 with a control-plane-only cluster) — but **worker nodes are constrained three
  ways** (discovered 2026-07-22 on the first real node-group apply; details in LEARNING.md):
  (1) only a fixed list of instance types is free-tier-eligible (`t3.micro/small`,
  `t4g.micro/small`, `c7i-flex.large`, `m7i-flex.large`, 6 mo) — `t3.medium` is NOT, which is
  what failed; (2) new accounts get a default **1-vCPU service quota** ("Running On-Demand
  Standard … instances") that would block every eligible type (all ≥2 vCPU) until raised via
  the Service Quotas console — though *this* account was already at **16** (checked 2026-07-22),
  so it wasn't a blocker here; (3) the serving image is **arm64-only**, so nodes must be
  Graviton (`t4g`), not x86 `t3`. Resolution stays on the free plan, no paid upgrade: node
  group = **`t4g.small` Graviton on-demand** (us-east-2 vCPU quota already 16, ample). EKS still *draws down credits* (control plane + nodes + NAT), so
  the ephemeral teardown discipline is the real guardrail. (A direct upgrade to paid would
  carry remaining credits over; via Organizations/Control Tower it would forfeit them — moot
  unless we upgrade.)
- AWS Budgets ($10 / $25 / $50, email alerts) exist — created via Console 2026-07-03. The
  CloudWatch billing alarm is deliberately **deferred**: a free-plan account bills $0 by
  construction. It becomes mandatory the day the account upgrades to paid (PLAN.md
  Phase 3, Task 0).
- **Region: us-east-2 for the whole project** (state bucket + all resources). The
  hand-made state bucket landed in us-east-2 (2026-07-04) and nothing in the project
  is region-bound, so the project followed it — `infra/persistent/variables.tf` and
  `backend.tf` both say us-east-2. TF state bucket: `mlops-quickdraw-tfstate-k7f2`
  (hand-made in the S3 Console, versioning on).
- **Persistent infra is live** (applied 2026-07-04, account `152439497402`): data
  bucket `mlops-quickdraw-data-ab1b`, logs bucket `mlops-quickdraw-logs-ab1b`, ECR
  `152439497402.dkr.ecr.us-east-2.amazonaws.com/quickdraw-api`, CI role
  `arn:aws:iam::152439497402:role/gha-app`. GitHub Actions repo variables
  `AWS_REGION`, `GHA_APP_ROLE_ARN`, `MLFLOW_STATE_BUCKET`, `ECR_REPOSITORY_URL`,
  and `API_FUNCTION_URL` are all set. **The OIDC assume-role path is proven** — the
  train-deploy workflow assumed `gha-app` and deployed on 2026-07-20 (run
  29751206896).
- GitHub: `MonishKamwal/mlops`, trunk-based (feature branch → PR → main). Stale
  `develop`/`staging` remote branches were deleted 2026-07-03.
- Working style (PLAN.md preamble): one-time/admin actions happen via **web UI** by Monish —
  give click paths, not CLI commands. Anything that *is* the platform stays in Terraform /
  GitHub Actions.
- Monish is doing this for learning + job-search portfolio. When something teaches a
  concept, record it in `LEARNING.md` and explain the *why*, not just the commands.

## Load-bearing design decisions (full table: PLAN.md §2)

- One FastAPI + ONNX container image serves both Lambda and EKS (Lambda Web Adapter, no
  code fork).
- All preprocessing is server-side in one shared module → no train/serve skew; parity
  tests enforce it.
- Evidently owns **drift**; Prometheus + Grafana own **operational metrics** — installed
  per ephemeral EKS run (kube-prometheus-stack), dashboards-as-code in
  `deploy/grafana/dashboards/`. No always-on monitoring cost anywhere.
- Two Terraform roots (`infra/persistent/` vs `infra/ephemeral/`) so the weekly destroy
  physically cannot touch state/data/API.
- Every claim ends up as a public artifact on the evidence hub (this repo's GitHub Pages).
- Guardrails before resources; steady state < $5/mo; free tier + $100–200 credits total.

## Progress log

- **2026-07-22 (personal laptop)** — **First real `eks-demo` apply surfaced the free-plan
  node-group wall; fixed by moving to Graviton.** The IAM-fix loop (branch
  `phase3-eks-iam-fix`; PR #24 merged only its first commit `d3d9b5b`, while 3 later IAM
  commits `85a6d85`/`2c45b58`/`43c532e` got the apply through the control plane) ended at the
  node group: run 29865681424 died with `t3.medium is not eligible for Free Tier` →
  `CREATE_FAILED` (46 min in; the `if: always()` destroy then cleaned up — teardown net works).
  Corrected the stale "EKS not blocked" fact: the control plane is fine, but nodes hit
  eligibility + a default **1-vCPU quota** + our **arm64-only image** (see LEARNING). Monish
  chose to **stay on the free plan** (not upgrade to paid): node group → **`t4g.small`
  Graviton/arm64** (matches the image, free-plan-eligible, 2 GB headroom for task 5),
  `ami_type=AL2023_ARM_64_STANDARD`, `capacity_type=ON_DEMAND` (spot would bill against credits
  for nothing). `terraform fmt`+`validate` clean locally. The vCPU quota turned out to be a
  non-issue — this account's us-east-2 "Running On-Demand Standard" limit is already **16**
  (checked 2026-07-22), well above the node group's peak of 6. Not yet applied — one prereq:
  the branch lands on main.
- **2026-07-21 (personal laptop, night, later³)** — **Phase 3 task 4 (failsafe) built**
  (branch `phase3-failsafe`): `eks-failsafe.yml` (monthly 09:00 UTC ~3h after the demo +
  break-glass dispatch, **no** approval gate — it must run unattended) does an unconditional
  `terraform destroy` (`continue-on-error`) then runs `scripts/eks_sweep.py`. The sweeper
  deletes any surviving `tier=ephemeral` resources (EKS clusters+nodegroups → LBs → the
  tagged VPC's instances/NAT/EIPs/subnets/SGs/route-tables/IGW/VPC), writes a job-summary
  report, and exits nonzero on any deletion error (a real leak turns the run red). Safety
  invariant: only ever acts on `tier=ephemeral` (persistent is `tier=persistent`, out of
  reach) — pinned by a parametrized predicate test. Also moved eks-demo + eks-failsafe onto
  one shared concurrency group `eks-cluster` so a failsafe can't fire mid-demo. 6 new tests
  (125 total), ruff + workflow YAML clean. **The teardown net now exists → safe to create a
  real cluster.**
- **2026-07-21 (personal laptop, night, later²)** — **Phase 3 task 3 (eks-demo workflow) +
  the `gha-eks` IAM role built** (branch `phase3-eks-demo`). Prereq surfaced: `gha-app` has
  only S3/ECR/Lambda perms and can't create EKS, so added a **separate `gha-eks` role**
  (`infra/persistent/iam.tf`) for the ephemeral lifecycle: region-scoped `ec2/eks/elb/
  autoscaling/logs:*` (persistent has no EC2/EKS in-region to collide with) + IAM **bounded
  to `role/quickdraw-ephemeral*`** (so "can create IAM roles" can't escalate to admin) +
  oidc-provider/service-linked roles + the ephemeral tfstate prefix + `lambda:GetFunction`.
  Disabled EKS KMS encryption (`encryption_config = null`, since the module gates on
  `!= null`) to avoid pending-deletion key buildup and kms perms. `eks-demo.yml` (monthly
  cron + dispatch, `environment: eks-demo` approval gate, `concurrency`): OIDC → tf apply →
  resolve the digest the Lambda serves (`lambda get-function`) → `helm install` **by digest**
  → smoke (port-forward) → k6 (20 VU/3 min, HTML report) → capture kubectl evidence (artifact)
  → **`if: always()` tf destroy**. k6 script `deploy/k6/predict.js`. `fmt` + `validate` (both
  roots) + workflow YAML all green; **not applied** — blocked on 3 Monish admin steps (see
  next step) and Task 4 existing first.
- **2026-07-21 (personal laptop, night, later)** — **Phase 3 task 2 (Helm chart) built**
  (branch `phase3-helm-chart`): `deploy/helm/quickdraw-api` — Deployment (image **by
  digest**, `required` so a mutable tag can't sneak in), `/healthz` startup+liveness+
  readiness probes (a generous startupProbe covers the ONNX-load cold start so liveness
  won't kill a still-loading pod), CPU/mem requests+limits, ClusterIP Service (LoadBalancer
  documented but off — no ELB cost/teardown surface), optional HPA, and a
  `serviceMonitor.enabled` toggle for the task-5 Prometheus scrape. Same image as the Lambda
  tier (Lambda Web Adapter is inert without the Lambda runtime API). Validated locally with
  `helm lint` (clean) + `helm template` across all value paths (default → Deployment+Service;
  HPA-on and ServiceMonitor-on render those too). Not deployed — waits for the cluster.
- **2026-07-21 (personal laptop, night)** — **Phase 3 kicked off: Task 0 gate cleared +
  Task 1 (`infra/ephemeral`) built.** Task 0: created a test EKS cluster via the console →
  **EKS is NOT blocked on the free plan**, so no paid upgrade needed; deleted the cluster +
  a stray unassociated EIP and swept the account clean (no EKS/EC2/NAT/ELB/EIP). Task 1
  (branch `phase3-ephemeral-infra`): a second Terraform root mirroring `infra/persistent`
  conventions — S3 backend key `ephemeral/terraform.tfstate` (physically separate from
  persistent state), AWS provider `~> 6.0`, `tier=ephemeral` default tags for the failsafe
  sweeper; `terraform-aws-modules/vpc ~> 6.0` (2 AZ, single NAT) + `terraform-aws-modules/eks
  ~> 21.0` (K8s 1.33, one managed node group 2× t3.medium SPOT, `endpoint_public_access` for
  CI kubectl, `enable_cluster_creator_admin_permissions` → CI role gets an admin access
  entry). Pinned EKS v21 (first major to support AWS provider 6) after checking the registry.
  `init -backend=false` + `fmt` + `validate` all green locally — **not applied** (gated on
  Tasks 3+4 teardown existing). Cadence: **monthly** cron (not weekly) + on-demand dispatch,
  to conserve credits.
- **2026-07-21 (personal laptop, evening, later)** — **Phase 2 closed.** Task 7 merged
  (PR #18) → model card live on the hub; the `main` ruleset re-enabled (new active
  ruleset `branch-protection`: require PR + block deletion/force-push; no signed
  commits). The task-7 merge triggered evidence-pages (published the card) and a
  train-deploy (uv.lock bump → benign v6, gate held). **Phase 2 is done end-to-end** —
  DVC → validate → registry → gate → OIDC train-deploy → evidence hub + model card, all
  live and reproducible. Gap logged: the ruleset doesn't yet require the `test`/ci.yml
  status check (Monish to add). Next: Phase 3 (ephemeral EKS), Task 0 = free-plan EKS
  check.
- **2026-07-21 (personal laptop, evening)** — **Phase 2 task 7 (model card) built**
  (branch `phase2-model-card`): `MODEL_CARD.md` at the repo root — architecture,
  intended use, training data + procedure, evaluation, limitations, ethical notes,
  caveats — rendered into the evidence hub as a section via Python-Markdown and copied
  to the site raw so the portfolio can reuse the source. `evidence.json` untouched: the
  rendered card is presentation (added to `_PRESENTATION_KEYS`), not data. New
  `markdown` dep in the `evidence` group. 3 new tests (119 total); a default-arg
  monkeypatch gotcha surfaced + fixed (see LEARNING). **Only the `main` ruleset
  re-enable remains to close Phase 2.**
- **2026-07-21 (personal laptop, later)** — **Task 6 shipped live + narrowed
  train-deploy's path filter.** Enabling Pages + a dispatch published the hub at
  monishkamwal.github.io/mlops/ (index.html + evidence.json both serving); the
  `workflow_run` auto-refresh then proved itself — v5's deploy re-rendered the hub with
  no manual step (evidence-pages run 29836201467). **But merging PRs #15 (params.yaml
  comment) and #16 (evidence module + uv.lock) each tripped a full train-deploy** — the
  blanket `src/quickdraw/**` matched the new `evidence/` package, and doc-adjacent files
  sit in the filter — spending an ~8-min arm64 build + Lambda redeploy + a new registry
  version (v4 0.9153, v5 0.9170) each. All within-ε so the gate held (champion v2 @
  0.9170 unmoved), but wasteful and churny. Fix (branch `fix-train-deploy-paths`, PR
  pending): replaced `src/quickdraw/**` with the model's real inputs (`config.py`,
  `data/**`, `training/**`, `serving/**`), so evidence/monitoring/doc changes no longer
  retrain.
- **2026-07-21 (personal laptop)** — **Phase 2 task 6 (evidence hub) built** (branch
  `phase2-evidence-hub`, PR pending): `src/quickdraw/evidence/export.py` renders a
  static site **from the MLflow registry** — the source of truth for champion state,
  not the git-tracked metrics snapshot (which already reads 0.9157 vs the registry's
  v2 @ 0.9170). Output: champion cards, runs table, plotly charts (per-class F1 +
  test-accuracy-over-runs), confusion matrix, gate policy with a link to the
  blocked-gate run, per-class table. **Emits `evidence.json` as a styling-agnostic
  data contract** (Monish's ask — the portfolio site consumes the JSON, not the
  throwaway HTML/CSS): `build_data()` is pure JSON, `build_context()` layers
  presentation on top, and `render()` strips the presentation keys back out so the two
  never disagree. New `evidence` dep group (jinja2, plotly) in default-groups so
  ci.yml's pytest can import it; the serving image still excludes it
  (`--no-default-groups`). `.github/workflows/evidence-pages.yml`: OIDC →
  `mlflow_sync.sh pull` → best-effort `dvc pull` of the confusion matrix →
  `export --out _site` → `upload-pages-artifact` → `deploy-pages`; triggers on green
  train-deploy (`workflow_run`) + push to evidence sources + dispatch (`id-token:
  write` does double duty — AWS assume-role *and* the Pages deploy). 6 new tests (116
  total), ruff clean; previewed locally against a throwaway DB mirroring v1/v2/v3.
  **Pending: Monish enables Pages (UI) + first manual dispatch** → hub goes live at
  monishkamwal.github.io/mlops/.
- **2026-07-20 (personal laptop, evening, later)** — **Failing-gate demo: a bad
  model was blocked in CI** (Phase 2 DoD item met). Branch `phase2-failing-gate`
  (never merged) crippled training in `params.yaml` (1 epoch, lr 1e-5); a
  `workflow_dispatch` run trained a 0.5049-test-accuracy challenger (registered as
  **v3**), which the gate rejected on **both** rules — below the 0.85 floor AND a
  −0.4121 regression vs champion — exiting 1 with a metric-diff summary. Every
  deploy step skipped, the live Lambda untouched, champion held; `mlflow.db` still
  pushed (`if: always()`) so the blocked challenger's lineage persists. **Evidence
  run 29757829275** (kept linked for the task-6 hub). Discovered while reading the
  gate output: the green run below re-crowned **champion to v2 @ 0.9170** (its CI
  retrain drifted +0.13pp above the laptop's v1 0.9157 — cross-machine seed noise,
  a strict improvement → promoted). Production has now exercised both gate paths:
  re-crown-on-improvement and block-and-hold.
- **2026-07-20 (personal laptop, evening)** — **Phase 2 tasks 4 + 5 merged; first
  train-deploy run green.** PR #12 (`phase2-gate`) and PR #13
  (`phase2-train-deploy`) merged to main (CI green). Monish added the 3 repo
  variables (`MLFLOW_STATE_BUCKET`, `ECR_REPOSITORY_URL`, `API_FUNCTION_URL`) via
  the GitHub UI and fired the first `workflow_dispatch` — **train-deploy ran
  end-to-end and passed** (run 29751206896, 9m24s): OIDC assumed `gha-app`
  (first-ever use of the assume-role path) → `mlflow_sync.sh pull` → `dvc pull`
  reported raw missing as expected → `dvc repro` → gate passed (re-crowned champion
  to v2 @ 0.9170, a strict improvement over the laptop's v1 0.9157) → arm64 QEMU build
  pushed digest `sha256:400a4873…` → `update-function-code` + `wait
  function-updated` → smoke test confirmed live `/model-info.model_sha256` ==
  freshly built onnx → `mlflow_sync.sh push`. LEARNING.md entry written (OIDC
  keyless deploy, arm64-via-QEMU-cheap-without-torch, `--provenance=false` + digest
  deploy, smoke test as artifact-identity proof). **The merge→live path now has
  zero manual steps.**
- **2026-07-20 (personal laptop, later)** — **Phase 2 task 5 (train-deploy
  workflow) built, NOT yet run** (branch `phase2-train-deploy`):
  `.github/workflows/train-deploy.yml` — on push to `main` touching
  `src/quickdraw/**`/`dvc.yaml`/`dvc.lock`/`params.yaml`/`Dockerfile`/`uv.lock`
  (+ `workflow_dispatch`): OIDC assume `gha-app` → `mlflow_sync.sh pull` →
  `dvc pull` (best-effort; raw is push:false so it always reports raw missing) →
  `dvc repro` → `gate` → build **arm64** image (QEMU + buildx,
  `--provenance=false`, deploy **by digest**) → push ECR → `lambda
  update-function-code` + `wait function-updated` → smoke test (assert live
  `/model-info.model_sha256` == `sha256sum models/model.onnx`) → `mlflow_sync.sh
  push` (`if: always()` so a blocked challenger's lineage still persists).
  `concurrency: train-deploy`, `cancel-in-progress: false` (single writer for
  mlflow.db). `gha-app` IAM role already grants everything (S3 both buckets, ECR
  push, `lambda:UpdateFunctionCode` on `quickdraw-*`) — no Terraform change
  needed. Editing the workflow doesn't match the path filter, so it won't fire on
  its own merge → first run is a deliberate manual dispatch (safe OIDC smoke
  test). **BLOCKED on 3 repo variables Monish must add via GitHub UI** (see
  Immediate next step). YAML validated; the OIDC + arm64-build paths are
  unexercised until the first dispatch.
- **2026-07-20 (personal laptop)** — **Phase 2 task 4 (quality gate) built and
  verified** (branch `phase2-gate`): `training/gate.py` — reads champion's and
  challenger's `test_accuracy` from the registry (new `registry.alias_test_accuracy`
  + `registry.promote_to_champion` helpers), applies floor + no-regression policy
  (`decide()`, pure), exits nonzero with a markdown metric-diff summary on fail
  (also appended to `$GITHUB_STEP_SUMMARY` when set). Thresholds in new `gate:`
  params section (`min_test_accuracy 0.85`, `epsilon 0.005`) via
  `config.GateParams`/`load_gate_params`. NOT a DVC stage (mutates registry, reads
  S3-synced MLflow) — runs after `dvc repro` in task 5's workflow.
  **Deploy and re-crown are decoupled (PLAN task 4 amended 2026-07-20):** pass →
  ship the challenger; the `champion` alias moves ONLY on a strict improvement
  (`challenger > champion`), so champion = best-ever quality bar and the gate's
  baseline never ratchets down. `champion` no longer means "deployed"; nothing
  tracks the live version (the workflow's built image is the source of truth).
  `run_gate` reads champion before promoting; `GateDecision` carries both `passed`
  and `promoted`. Demoed 3 paths on throwaway DBs: strict improvement ships + re-
  crowns; within-ε ships without re-crowning (bar holds); crippled 0.71 challenger
  → FAIL (floor + regression), exit 1, champion unchanged. 19 new tests (110 total),
  ruff clean. Adding `gate:` to params.yaml invalidates no DVC stage (no stage deps
  on the whole file or that section).
- **2026-07-18 (personal laptop, late night)** — **Phase 2 task 3 (MLflow on S3)
  built and bootstrapped** (branch `phase2-mlflow`): `training/registry.py` —
  model `quickdraw`, every train run registers its checkpoint as a version +
  `challenger` alias (first version bootstraps `champion` too); evaluate logs
  test_accuracy/macro_f1 onto the challenger's run (what the task-4 gate
  compares). **Only `mlflow.db` syncs to S3** (`scripts/mlflow_sync.sh
  pull|push`); artifacts write to S3 natively via the experiment artifact root —
  MLflow records artifact roots as absolute URIs, so a synced `mlruns/` would
  bake laptop paths into the shared DB (PLAN.md task 3 amended). Env-presence
  switch `MLFLOW_STATE_BUCKET` (unset → local + AWS-free; autouse test fixture
  keeps the suite hermetic); `ensure_experiment` refuses local-rooted experiments
  when the bucket is set. **Laptop-era mlflow.db archived as `mlflow.local.db`**
  (never shared); fresh canonical DB bootstrapped: tracked repro → run
  `fc1582c3…` (val 0.9151 / test 0.9157 — model byte-identical again, export
  skipped), **v1 = champion = challenger**, artifact at
  `s3://…-data-ab1b/mlflow/artifacts/fc1582c3…/artifacts/model.pt`, DB pushed to
  `s3://…-data-ab1b/mlflow/mlflow.db`. 6 new tests (91 total).
- **2026-07-18 (personal laptop, night)** — **Phase 2 task 2 (Pandera validation)
  built and verified** (branch `phase2-validate`): `data/validate.py` reduces the
  npz to a per-(split, class) metadata dataframe (count, pixel min/max, mean ink)
  and a `DataFrameSchema` checks exact split sizes, full label set per split,
  label↔class order vs params.yaml, pixel/ink sanity (loose-then-calibrate bounds:
  ink peak ≥ 200, mean ink 0.01–0.40); tensor dtypes/shapes + artifact class list
  checked as ValueErrors first. **Stage placed between preprocess and train**
  (PLAN.md amended — all prescribed checks describe the processed artifact); train
  deps include the report file, so validation is a gate by DAG structure. Report
  is timestamp-free (deterministic DVC out, byte-identical run-to-run — tested).
  pandera+pandas → `train` group. Repro: validate passed (45 rows); train re-ran
  (dep list changed) → **byte-identical model.pt → DVC skipped evaluate+export**
  (same-machine seeded training is byte-deterministic). 7 new tests (85 total).
- **2026-07-18 (personal laptop, later)** — **Phase 2 task 1 (DVC) built and verified**
  (branch `phase2-dvc`): `dvc[s3]` added to the `train` group (stays out of the
  serving image); `dvc init`, remote `storage` = `s3://mlops-quickdraw-data-ab1b/dvc`
  (us-east-2, analytics off); `dvc.yaml` with 5 stages (download → preprocess →
  train → evaluate → export — `validate` arrives with task 2/Pandera), param-scoped
  deps (`data.classes` for download, `data` / `training` sections), fine-grained
  code deps. Design: raw is cached but `push: false` (Google GCS is canonical;
  S3 stores derived artifacts only ~42 MB); `metrics.json` is `cache: false` →
  git-tracked for `dvc metrics diff` (.gitignore negation dance); MLflow state
  deliberately NOT a DVC out (task 3's job). Full `dvc repro` ran: **val_accuracy
  0.9151 / test 0.9157 / macro F1 0.9162 — identical to the 07-04 run to 4
  decimals** (seeded reproducibility demonstrated); parity 1.9e-06; second repro =
  all-skip no-op. Learned: DVC deletes a stage's outs before running it, so the
  first repro re-downloaded all 1.7 GB (one-time; cache serves it now). 5 new
  pipeline-definition tests (78 total).
- **2026-07-18 (personal laptop)** — **Task 6 shipped → Phase 1 complete.** Ran the
  ship runbook end to end: PR #7 was already merged; `terraform apply` (1 add /
  2 change — inline `prediction-logs-put` policy, `PREDICTION_LOG_BUCKET` env var,
  role-description text); new arm64 image built + pushed (digest `51ee3bd6…`,
  +boto3, single-manifest via `--provenance=false`); deployed via Lambda Console
  "Deploy new image". Verified: `/healthz` + `/model-info` answer on the new image
  (model sha unchanged `a54dd404…`), Monish drew on the live site, and JSONL
  objects are accumulating under `predictions/dt=2026-07-18/` in the logs bucket —
  the last Phase 1 DoD. Shipping-day lessons (tag-vs-digest, safe rollout ordering,
  fail-open verification) added to LEARNING.md.
- **2026-07-06 (personal laptop, late night)** — **Phase 1 task 5 closed**: both PRs
  merged, Monish drew on the live site and got a real prediction — CORS, cold-start
  UX, and the "stranger can draw" DoD verified in production. **Task 6 code written**
  (branch `phase1-logging`): `serving/prediction_log.py` — one JSONL object per
  prediction to `predictions/dt=YYYY-MM-DD/<ts>-<uuid8>.jsonl` (Hive-partitioned for
  Athena/Phase 4); record = ts, input_sha256 (canonical (1,28,28) float32 input),
  source, top3, latency_ms (preprocess+inference), model_sha256, service_version.
  Synchronous write in the request path (Lambda freezes after the response;
  background tasks lose data), fail-open (S3 outage costs a log line, never a
  prediction — tested), boto3 lazy-imported with tight timeouts (1 s/3 s, 2
  attempts), enabled only when `PREDICTION_LOG_BUCKET` is set → docker run/tests
  stay AWS-free. Terraform: env var on the Lambda + append-only `s3:PutObject` on
  `predictions/*` for `quickdraw-api-exec`; boto3 added to runtime deps (image will
  grow ~30 MB). 73/73 tests, ruff + `terraform fmt`/`validate` clean. **Not live
  yet** — needs apply + image push + manual image deploy (runbook in "Immediate
  next step").
- **2026-07-06 (personal laptop, night, later)** — PR #5 merged → **Phase 1 task 4
  closed**. **Task 5 (frontend) built** in the portfolio repo (branch
  `phase1-canvas-live`, PR pending): mock swapped for the live Function URL.
  Strokes go as QuickDraw `[[xs],[ys]]`, raw canvas coords (server bbox-normalizes),
  **strokes only** — the API prefers strokes when a PNG is also sent, so the PNG
  would be dead weight (PORTFOLIO_PLAN amended). Cold-start UX: `GET /model-info`
  on page load is the warm-up ping *and* supplies the live class list + model
  sha/val-acc for the UI; predicts in flight > 2 s show a "model is waking up"
  state; status chip warming/live/unreachable. Top-3 of the full ranked response
  rendered. Verified: site lint+build green; node replay of the exact client flow
  against the live API (star sketch → 0.831). **Local-dev CORS resolved:** the
  Function URL allowlists localhost:3000 → `next dev` hits the live Lambda, no
  app-level CORS ever (task-3 open question closed; see LEARNING.md).
- **2026-07-06 (personal laptop, night)** — Phase 1 task 4 **executed** (Monish ran
  the AWS-touching steps): arm64 image pushed to ECR (`--provenance=false` build →
  single-manifest), `terraform apply` created the 6 resources, second apply = no-op
  (idempotence DoD), and the live Function URL answers: `/healthz` ok, `/model-info`
  sha256 `a54dd404…` — byte-identical to the local image — and the stroke-cat returns
  0.979, matching local `docker run` exactly. **The API is live:**
  `https://u4udjs3pbrr6xlaanmcpdb7bty0amoeh.lambda-url.us-east-2.on.aws/`
- **2026-07-06 (personal laptop, later)** — PR #4 merged → **Phase 1 task 3 closed**.
  Task 4 Terraform written on `phase1-deploy` (`infra/persistent/lambda.tf`):
  `quickdraw-api` Lambda (container image from ECR `:latest` var, **arm64**, 1024 MB /
  30 s, no env — image defaults rule), execution role (`quickdraw-api-exec`, basic
  logs policy), pre-created log group (14-day retention), public Function URL
  (explicit `aws_lambda_permission`, CORS: monishkamwal.github.io + localhost:3000),
  `ignore_changes = [image_uri]` so Phase 2 CI deploys don't get reverted; new output
  `api_function_url`. `terraform fmt`/`validate` clean (Monish ran them). **Not yet
  applied** — image must be pushed first.
- **2026-07-06 (personal laptop)** — Phase 1 task 3 (serving) built and verified
  (branch `phase1-serving`): `serving/predictor.py` (onnxruntime session, classes +
  val_accuracy read from ONNX metadata, input/output names from the graph — no
  training imports, subprocess test pins the app torch-free), `serving/app.py`
  (`POST /predict` strokes and/or base64 PNG through the shared preprocess module,
  strokes win if both; `/healthz`; `/model-info` incl. model sha256; `/metrics` via
  instrumentator, healthz/metrics excluded from histograms; model loads in lifespan;
  preprocess `ValueError`/PIL `OSError` → 400). Serving deps moved to
  `[project.dependencies]`; onnxruntime out of the `train` group; httpx added to dev.
  Dockerfile: uv multi-stage (`--no-default-groups`), python:3.12-slim both stages,
  Lambda Web Adapter 0.9.1, port 8080, `AWS_LWA_READINESS_CHECK_PATH=/healthz`;
  441 MB. Verified: 62/62 tests; `docker run` → healthz/model-info/predict/metrics
  all good; a stroke-drawn cat → cat 0.979 with the real model.
- **2026-07-04 (personal laptop, wrap-up)** — PR #3 merged → **Phase 1 task 2
  closed**; `phase1-serving` branch created and pushed (empty) for task 3.
- **2026-07-04 (personal laptop, night)** — Phase 1 task 2 **executed** (Monish ran
  the pipeline): 8 epochs, val_accuracy 0.859 → 0.9151, test 0.9157, macro F1 0.9162;
  worst classes dog/bird/cat (F1 0.77–0.84). `export_onnx` parity OK. Two gotchas,
  both in LEARNING.md: bare `mlflow ui` reads `./mlruns` and misses sqlite runs
  (needs `--backend-store-uri sqlite:///mlflow.db`); torch's exporter left an orphan
  `model.onnx.data` sidecar — export now deletes it (+ regression test, 47 total).
- **2026-07-04 (personal laptop, evening)** — Phase 1 task 2 code (branch
  `phase1-training`): `training:` section in params.yaml + typed loader;
  `QuickDrawCNN` (2 conv blocks + FC head, ~420k params); `train.py` (MLflow to
  sqlite, best-val-epoch checkpoint with the class list embedded); `evaluate.py`
  (pure-numpy confusion matrix + per-class metrics, PNG heatmap); `export_onnx.py`
  (dynamic batch axis, classes in ONNX metadata, PyTorch-vs-ONNX parity check).
  New `train` dependency group with torch pinned to the CPU wheel index;
  `default-groups` keeps CI's `uv sync --locked` working unchanged. 46/46 tests
  pass; **training itself not yet run**. Also verified Phase 0 task 5 was already
  complete (portfolio Pages deploys green) → Phase 0 closed.
- **2026-07-04 (personal laptop)** — Phase 1 task 1 **validated** (branch
  `phase1-validate`): `uv sync` regenerated `uv.lock` (numpy 2.5.1, pillow 12.3.0);
  `ruff format` reformatted only `data/preprocess.py`, `ruff check` clean; **30/30
  tests passed on the code's first-ever execution** — incl. exact stroke parity and
  the guessed IoU/mad closeness thresholds; smoke run clean: 15 class archives
  downloaded (~1.7 GB) → `preprocess` wrote `data/processed/quickdraw.npz`. CI on
  main goes green when this lands.
- **2026-07-04 (personal laptop)** — Phase 0 task 4 **done** (branch
  `phase0-apply-persistent`): merged `phase0-infra-persistent` + `phase1-data-layer`
  to main (clean fast-forwards, in that order); hand-made the state bucket
  `mlops-quickdraw-tfstate-k7f2` (landed in us-east-2 → whole project moved to
  us-east-2); AWS CLI + IAM-user auth set up (region typo `east-us-2` → STS endpoint
  connection error, see LEARNING.md); `terraform init/fmt/validate/apply` clean —
  12 resources created, second apply printed "No changes" (idempotency DoD met);
  GitHub Actions variables `AWS_REGION` + `GHA_APP_ROLE_ARN` added via UI.
- **2026-07-03 (work laptop)** — Phase 1 task 1 code (branch `phase1-data-layer`,
  stacked on `phase0-infra-persistent`): `params.yaml` + typed loader
  (`quickdraw/config.py`; class order = label index); `data/download.py` (GCS bitmap
  archives, atomic `.part`+rename, skip-existing); `data/preprocess.py` — serve-path
  transforms (strokes/PNG → 28×28 → `bitmap_to_model_input`, the single shared
  normalization), deterministic stratified split, uint8 `quickdraw.npz` artifact;
  tests incl. exact stroke-parity + loose stroke-vs-PNG closeness. numpy/pillow/pyyaml
  added to pyproject; **uv.lock NOT regenerated** (no uv here) → CI's
  `uv sync --locked` fails until `uv lock` runs at home. Nothing executed.
- **2026-07-03 (work laptop)** — Phase 0 task 4 code: `infra/persistent/` written —
  S3 backend (native locking, bucket name = `REPLACE_ME` placeholder), data + logs
  buckets (versioning / 180-day expiry), ECR repo (keep last 3 images), GitHub OIDC
  provider + `gha-app` role (trust pinned to this repo). Added `.gitattributes`
  (`* text=auto`) — Windows/WSL CRLF churn was showing every tracked file as
  modified. Not yet validated or applied: no terraform binary on the work laptop.
- **2026-07-03** — Phase 0: README rewritten to single-model scope; uv/ruff/pytest/
  pre-commit scaffold + `src/quickdraw` skeleton + smoke tests; `ci.yml` (ruff, format
  check, pytest); budgets created; free-plan discovery → billing alarm deferred + Phase 3
  account-plan gate added to PLAN.md; Prometheus + Grafana promoted from stretch goal to
  first-class (PLAN.md §2/§3/§4, Phases 1 + 3); MEMORY/LEARNING/CLAUDE docs added.
- **2026-07-02** — PLAN.md authored and committed.

## Current state

**Phase 0 is complete.** Tasks 1–4 as before (billing alarm deferred by design); task 5
(portfolio scaffold) turned out to be **already done** — the `monishkamwal.github.io`
repo has the Next.js static-export skeleton committed and its "Deploy to GitHub Pages"
workflow runs green (verified 2026-07-04; meets PORTFOLIO_PLAN.md's phase-0 done-when).
The earlier "not started" note here was wrong.

Phase 1 tasks 1 (data layer) and 2 (training) are **merged** (PRs #2, #3) — CI on main
is green. Training run 2026-07-04: best **val_accuracy 0.9151** (epoch 8/8, curve
still climbing; DoD ≥ 0.88 passed at epoch 2), **test_accuracy 0.9157**, macro F1
0.9162 — hardest classes are the animals (F1: dog 0.77, bird 0.79, cat 0.84).
`models/model.onnx` exported, parity OK, single self-contained file with the class
list in its metadata.

**Phase 1 task 3 (serving) is merged** (PR #4, 2026-07-06; CI on main green) — FastAPI +
onnxruntime app, Dockerfile with Lambda Web Adapter, 62 tests. CORS is deliberately not
in the app: the Function URL owns it (PLAN.md §2); local-dev CORS is a task-5 question.

**Phase 1 task 4 (deploy) is merged and live** (PR #5, 2026-07-06): `quickdraw-api`
Lambda (arm64 container image, 1024 MB) + public Function URL, CORS allowlist
monishkamwal.github.io + localhost:3000 —
**`https://u4udjs3pbrr6xlaanmcpdb7bty0amoeh.lambda-url.us-east-2.on.aws/`** answers
healthz/model-info/predict with outputs identical to local `docker run`. Terraform
ignores `image_uri` drift (Phase 2 CI deploys out-of-band); ECR image is the
`--provenance=false` arm64 build tagged `:latest`.

**Phase 1 task 5 (frontend) is done and verified in production** (2026-07-06): the
home-page canvas at monishkamwal.github.io calls the live Function URL — strokes
only, QuickDraw format; `/model-info` warm-up ping on load feeds the live class list
+ model sha/val-acc; "waking up" state past 2 s; top-3 confidence bars. Monish drew
on the public site and got a real prediction.

**Phase 1 task 6 (prediction logging v0) is live and verified** (shipped 2026-07-18):
every prediction writes one JSONL record to `predictions/dt=YYYY-MM-DD/` in
`mlops-quickdraw-logs-ab1b` — synchronous, fail-open, append-only via IAM, switched
on by `PREDICTION_LOG_BUCKET` (local `docker run`/tests stay AWS-free). DoD met:
objects visibly accumulating from real drawings on the public site.

**→ Phase 1 is complete.** The walking skeleton is fully live: data → training →
serving → Lambda deploy → public canvas → prediction logging. Real visitor data has
started accruing for Phase 4.

**Phase 2 task 1 (DVC) is merged** (PR #9, 2026-07-18, CI green): 6-stage
`dvc.yaml`, S3 remote on the data bucket (raw is `push: false`, metrics.json
git-tracked), repro reproduced training to 4 decimals, second repro = no-op.
`dvc repro` is now *the* way to run the pipeline.

**Phase 2 task 2 (Pandera validation) is merged** (PR #10, 2026-07-18, CI green):
`validate` stage between preprocess and train (PLAN.md amended), gating train via
a dep edge on the deterministic report.

**Phase 2 task 3 (MLflow on S3) is merged** (PR #11, 2026-07-18, CI green):
registry semantics live (v1 = champion = challenger, run `fc1582c3…` with test
metrics), canonical `mlflow.db` + artifacts on S3, `scripts/mlflow_sync.sh` for
the DB, `MLFLOW_STATE_BUCKET` env switch. The laptop-era DB is archived locally
as `mlflow.local.db`.

**Phase 2 task 4 (quality gate) is merged** (PR #12, 2026-07-20, CI green):
`training/gate.py` gates challenger vs champion on
`test_accuracy` — absolute floor 0.85 AND no regression beyond ε=0.005 (thresholds
in the new `gate:` params section). Pass → ship the challenger + exit 0; fail →
metric-diff summary (stdout + `$GITHUB_STEP_SUMMARY`) + exit 1. **Deploy and
re-crown decoupled:** the `champion` alias moves only on a strict improvement, so
champion = best-ever quality bar (not "deployed") and the gate baseline can't
ratchet down (PLAN task 4 amended 2026-07-20). CLI run after `dvc repro`, not a
DVC stage. 110 tests green, ruff clean. Three paths demoed on throwaway DBs; the
block-and-hold path is now also proven live in CI (failing-gate demo 2026-07-20,
run 29757829275 — a 0.5049 challenger rejected on floor + regression, deploy
skipped, champion held).

**Phase 2 task 5 (train-deploy workflow) is merged and proven in production**
(PR #13, 2026-07-20, CI green): `.github/workflows/train-deploy.yml` — fires on
push to main touching model files (+ `workflow_dispatch`), `concurrency:
train-deploy` (single writer for mlflow.db). Pipeline: OIDC assume `gha-app` →
`mlflow_sync.sh pull` → `dvc pull` (best-effort) → `dvc repro` → gate → arm64 QEMU
build (`--provenance=false`) → push ECR → Lambda `update-function-code` deploy **by
digest** → smoke test (live `/model-info.model_sha256` == freshly built onnx) →
`mlflow_sync.sh push` (`if: always()`). **First run green 2026-07-20** (manual
dispatch, run 29751206896, 9m24s) — the OIDC assume-role path exercised for the
first time. **The merge→live path now has zero manual steps** (Phase 2 DoD item met).

**Registry state (as of 2026-07-21):** champion = **v2 @ 0.9170** — still the quality
bar; no later challenger has *strictly* beaten it. v1 0.9157 (laptop), v3 0.5049
(blocked crippled demo), and v4 0.9153 / v5 0.9170 (challengers that shipped within-ε
from the PR #15 / #16 merges, before the path-filter fix below). Live Lambda serves the
newest shipped challenger (**v5**); by design no alias tracks "deployed" — the built
image is the source of truth for that.

**Phase 2 task 6 (evidence hub) is live** (merged PR #16; path-filter fix PR #17):
`quickdraw.evidence.export` renders the hub from the MLflow registry and writes
`index.html` + **`evidence.json`** (the styling-agnostic data contract the portfolio
site consumes) + `style.css` + confusion matrix; `evidence-pages.yml` publishes to
GitHub Pages after each green train-deploy (auto-refresh via `workflow_run` proven).
**Live at monishkamwal.github.io/mlops/.**

**Phase 2 task 7 (model card) is merged and live** (PR #18): `MODEL_CARD.md` at the
repo root — architecture, intended use, training data/procedure, evaluation,
limitations, ethical notes — rendered into the hub as a section (Python-Markdown) and
copied to the site raw so the portfolio can reuse the source. `evidence.json` unchanged
(the rendered card is presentation, not data). 119 tests.

**→ Phase 2 is COMPLETE (2026-07-21).** DVC → Pandera validation → MLflow-on-S3
champion/challenger registry → quality gate → OIDC train-deploy (merge→live, zero
manual steps) → evidence hub + model card, all live and reproducible. The `main`
ruleset is re-enabled (active ruleset `branch-protection`: require PR, block
deletion/force-push, no signed commits). **Known gap:** the ruleset does *not* yet
require the `test`/ci.yml status check, so a red-CI PR could still merge until that's
added.

## Immediate next step (rolling — keep this precise)

**Phase 2 complete; Phase 3 (ephemeral EKS) underway.** Task 0 resolved 2026-07-21: EKS
runs on the free plan (no upgrade needed); the test cluster + a stray EIP were cleaned up
→ account at zero billable EKS/EC2/NAT/ELB/EIP. **Task 1 (`infra/ephemeral`) built**
(branch `phase3-ephemeral-infra`, PR pending): second Terraform root (state key
`ephemeral/terraform.tfstate`), VPC module `~> 6.0` (2 AZ, single NAT) + EKS module
`~> 21.0` (K8s 1.33, one managed node group **2× t3.medium SPOT**, public endpoint for CI
kubectl, cluster-creator admin access entry); `init -backend=false` + `validate` pass
locally. **NOT applied** — by design, apply waits until the teardown path exists.
**Task 2 (Helm chart `deploy/helm/quickdraw-api`) built** (branch `phase3-helm-chart`, PR
pending): Deployment (image by digest, `required`), `/healthz` startup+liveness+readiness
probes, resources, ClusterIP Service (LB toggle documented/off), optional HPA, and a
`serviceMonitor` toggle for task 5; `helm lint` + `helm template` (all value paths) clean.

**Tasks 3 + 4 built and the `gha-eks` role/env/repo-var are all live** — the first real
`eks-demo` run assumed `gha-eks`, applied the VPC + EKS control plane, and got as far as the
node group before the free-plan wall (above). So OIDC → apply → **control plane** →
`if: always()` destroy is all *proven*; only the node group needs the Graviton fix. That fix
is written (`t4g.small` on-demand, branch below) but **not merged or applied**. Next, in order:

1. **Land the fix on main.** Current work is on branch `phase3-eks-iam-fix` (HEAD 43c532e),
   which carries the 3 unmerged IAM commits **plus** the new Graviton node change. PR #24 is
   already merged/closed, so this needs a **fresh PR** (or fast-forward) to main. (Claude has
   made the edits + local `fmt`/`validate`; commit/push/PR pending Monish's go-ahead.)
2. **vCPU service quota — already sufficient, no action.** Checked 2026-07-22: this account's
   "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances" quota (us-east-2) is
   **16 vCPU**, not the new-account default of 1 — well above the node group's peak (max_size
   3 × 2 vCPU = 6). Left here only so a future machine doesn't re-investigate it.
3. **Re-dispatch `eks-demo`** and approve it — watch OIDC → apply → **node group launches** →
   helm → smoke → k6 → **`if: always()` destroy**; confirm the account is clean after. Any
   remaining `gha-eks` IAM gap shows up as an apply AccessDenied (recoverable — add it, re-run).
4. **Task 5 — observability** (kube-prometheus-stack, ServiceMonitor → `/metrics`, Grafana
   dashboards-as-code) — the last Phase 3 task. `t4g.small`'s 2 GB was chosen partly to leave
   room for this; may still need 3 nodes / resource-tuned Prometheus.

Tail item (anytime): style the portfolio site's evidence section by consuming
`evidence.json` (the data contract).

Watch items: the evidence hub's confusion-matrix PNG is a laptop-era artifact (v1 @
0.9157) while headline metrics come from the registry (now ~v6, champion still v2 @
0.9170) — a later refinement could regenerate it from the champion; several actions in
the workflows still target Node 20 (GitHub forces Node 24 — bump majors when convenient);
markdownlint nits in PLAN.md are known and not CI-checked.
