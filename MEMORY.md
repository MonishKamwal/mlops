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
  The account *cannot incur charges*; some credit-hungry services are blocked (EKS likely
  among them — unverified). A direct upgrade to paid carries remaining credits over
  (upgrading via Organizations/Control Tower would forfeit them).
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
  `AWS_REGION` and `GHA_APP_ROLE_ARN` are set. The OIDC assume-role path has never
  been exercised — first real use comes with the Phase 2 workflows.
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

**Phase 1 task 4 (deploy) is applied and verified live** (2026-07-06, branch
`phase1-deploy`, PR #5): `quickdraw-api` Lambda (arm64 container image, 1024 MB) +
public Function URL, CORS allowlist monishkamwal.github.io + localhost:3000 —
**`https://u4udjs3pbrr6xlaanmcpdb7bty0amoeh.lambda-url.us-east-2.on.aws/`** answers
healthz/model-info/predict with outputs identical to local `docker run`. Terraform
ignores `image_uri` drift (Phase 2 CI deploys out-of-band); ECR image is the
`--provenance=false` arm64 build tagged `:latest`.

## Immediate next step (rolling — keep this precise)

Merge PR #5 (`phase1-deploy` → main). Then **Phase 1 task 5 (frontend)**: canvas demo
page on the portfolio home (`monishkamwal.github.io` repo, per PORTFOLIO_PLAN.md there)
calling the Function URL — send strokes (and optionally the PNG) as JSON to `/predict`;
handle cold-start UX: warm-up ping (`GET /healthz`) on page load + "model waking up…"
state. Then task 6 (prediction logging v0): FastAPI middleware → JSONL to the logs
bucket (timestamp, input digest, top-3, confidence, latency; no PII) + `s3:PutObject`
on the logs bucket for the `quickdraw-api-exec` role.

Watch items: the GitHub OIDC assume-role path is untested until the first workflow
uses it (Phase 2); EKS-on-free-plan question parked until Phase 3 Task 0; markdownlint
style nits in PLAN.md are known and not CI-checked.
