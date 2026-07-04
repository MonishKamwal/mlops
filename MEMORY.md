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

Phase 0: tasks 1–4 done (billing alarm deferred by design) — `infra/persistent` is
applied and idempotent in us-east-2; GitHub repo variables set. Task 5 (portfolio repo
scaffold) not started. Phase 1 task 1 (data layer) is **validated** on branch
`phase1-validate`: lock regenerated, lint clean, 30/30 tests passed first run, pipeline
smoke-tested end to end (~1.7 GB raw → `data/processed/quickdraw.npz`). Branch not yet
committed/merged — CI on main stays red until it lands.

## Immediate next step (rolling — keep this precise)

**Land `phase1-validate`:** commit `uv.lock` + `src/quickdraw/data/preprocess.py`
(formatter-only diff) + this file + `LEARNING.md` → push → PR → main. The PR's CI run
is the first with the regenerated lock and must be green; main goes green on merge.

**Then Phase 0 task 5:** portfolio repo scaffold (see `PORTFOLIO_PLAN.md` in the
`monishkamwal.github.io` repo) — the last open Phase 0 item.

**Then Phase 1 task 2 (training):** small CNN in PyTorch (~2 conv blocks + FC,
target ≥ 88% val accuracy); `train.py` logs to local MLflow (`sqlite:///mlflow.db`);
`evaluate.py` (confusion matrix, per-class metrics); `export_onnx.py` + ONNX-vs-PyTorch
parity unit test (PLAN.md Phase 1, task 2).

Watch items: the GitHub OIDC assume-role path is untested until the first workflow
uses it (Phase 2); EKS-on-free-plan question parked until Phase 3 Task 0; markdownlint
style nits in PLAN.md are known and not CI-checked.
