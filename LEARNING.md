# LEARNING.md — things learned building this

Learning journal, newest first. Each entry: what happened, what was learned, why it
matters. This feeds the portfolio's Journey/devlog section (PLAN.md Phase 4). Claude:
add an entry whenever a task teaches a concept that wasn't obvious going in.

## 2026-07-03 — Terraform bootstrap, S3-native locking, OIDC (and a line-endings ambush)

- **The state bucket is a chicken-and-egg.** Terraform can't create the bucket its own
  state lives in, so exactly one resource — a versioned S3 bucket — is made by hand,
  and everything else is code. Accepting one manual bootstrap resource is the standard
  answer, not a compromise.
- **State locking no longer needs DynamoDB.** Terraform ≥ 1.11 locks natively via a
  lock object in the state bucket itself (`use_lockfile = true`). Most tutorials still
  teach the DynamoDB lock-table pattern — that's legacy now.
- **Backend blocks can't use variables.** Backend config is resolved before variable
  evaluation, so the bucket/region in `backend.tf` are literals even though the same
  region is a variable everywhere else.
- **GitHub OIDC → AWS means CI holds zero long-lived secrets.** Each workflow run gets
  a short-lived token signed by GitHub; AWS trusts the issuer once (the OIDC provider
  resource), and the role's trust policy pins the *audience* (`sts.amazonaws.com`) and
  *subject* (`repo:MonishKamwal/mlops:*`). There is no key to leak, and the role's
  permission policy defines the entire blast radius.
- **`default_tags` on the AWS provider** tags every resource the provider creates —
  cost attribution that can't be forgotten one resource at a time.
- **Line-endings ambush:** the repo stores LF, but Windows-side tooling checked files
  out as CRLF, so git-in-WSL reported *every* tracked file modified with byte-identical
  content. Fix: `.gitattributes` with `* text=auto`, which makes git compare normalized
  content — and being committed, it fixes every clone on every machine, unlike the
  per-machine `core.autocrlf` setting.

## 2026-07-03 — AWS account plans, billing guardrails, and regions

- **AWS revamped its free tier in July 2025.** New accounts choose a *free plan* or
  *paid plan*. Free plan: $100 credits (+ up to $100 earnable), 6-month window, account
  **cannot incur charges**, and credit-hungry services are blocked outright. Upgrading
  directly to paid keeps remaining credits; upgrading via Organizations forfeits them.
  Discovered mid-setup — this project's account is on the free plan.
- **CloudWatch billing alarms have two hidden prerequisites:** they only exist in
  **us-east-1** (billing metrics are published nowhere else), and the metric doesn't
  exist at all until "Receive CloudWatch billing alerts" is enabled under *Billing
  preferences → Alert preferences* (then takes minutes–hours to appear). On a free-plan
  account the metric is moot anyway — it reads $0 by construction — so the alarm is
  deferred until the paid-plan upgrade. AWS Budgets track credit burn instead.
- **"Regions aren't enabled for this account" is (usually) not an error.** Regions
  launched after March 2019 are *opt-in* and show as disabled for every account;
  default regions like us-east-1 are always enabled and can't be turned off.
- **Guardrails before resources, in practice:** budgets and alerts were configured
  before a single piece of infrastructure existed. Being responsible for things outside
  code — the ops half of MLOps — starts on day one, not after the first bill.
