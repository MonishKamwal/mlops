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
- **Region: us-east-1 for the whole project** (state bucket + all resources) —
  defaulted in `infra/persistent/variables.tf` and `backend.tf`; if the hand-made
  state bucket ends up elsewhere, update both plus this line.
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

Phase 0: tasks 1–3 done (billing alarm deferred by design). Task 4: `infra/persistent/`
Terraform code written, **not yet validated or applied** — needs the hand-made state
bucket, then `init`/`apply` from the personal laptop. Task 5 (portfolio repo scaffold)
not started. Nothing committed yet (working tree: `.gitattributes` + `infra/` + doc
updates).

## Immediate next step (rolling — keep this precise)

**Finish Phase 0, task 4: bootstrap + apply the `infra/persistent` root** (code is
written; everything below except step 1 needs the personal laptop).

1. **Monish, S3 Console (one-time bootstrap):** create the TF state bucket — suggested
   name `mlops-quickdraw-tfstate-<4 random chars>`, region **us-east-1**, versioning
   **on**, encryption + block-all-public-access at their secure defaults. Then put the
   real name in `infra/persistent/backend.tf` (replace `REPLACE_ME`) — or tell Claude.
2. **Commit** (feature branch → PR → main): `.gitattributes`, `infra/persistent/`,
   this file, `LEARNING.md`.
3. **Monish, personal laptop terminal:** install terraform + AWS CLI if missing
   (`brew install awscli hashicorp/tap/terraform`), authenticate (IAM user or SSO —
   not root), then in `infra/persistent/`: `terraform init`, `terraform fmt -check`
   and `terraform validate` (the code has never been through either — no terraform
   binary on the work laptop), `terraform apply`, then `apply` again — the second run
   must be a no-op (Phase 0 DoD).
4. **Monish, GitHub UI:** repo → Settings → Secrets and variables → Actions → Variables:
   add `AWS_REGION` and `GHA_APP_ROLE_ARN` from the Terraform outputs.
5. Then Phase 0 task 5 (portfolio repo, see `PORTFOLIO_PLAN.md` there), then Phase 1
   task 1 (data download/preprocess).

Watch items: `infra/persistent/` has never seen `terraform fmt/validate/apply` — first
run happens on the personal laptop; EKS-on-free-plan question parked until Phase 3
Task 0; markdownlint style nits in PLAN.md are known and not CI-checked.
