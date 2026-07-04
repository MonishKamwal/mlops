# LEARNING.md — things learned building this

Learning journal, newest first. Each entry: what happened, what was learned, why it
matters. This feeds the portfolio's Journey/devlog section (PLAN.md Phase 4). Claude:
add an entry whenever a task teaches a concept that wasn't obvious going in.

## 2026-07-04 — First real `terraform apply`: the region follows the bootstrap bucket

- **The project region was decided by a bucket.** The plan said us-east-1, but the
  hand-made state bucket landed in us-east-2. Since nothing in this project is
  region-bound (the one us-east-1-only piece — the CloudWatch billing alarm — is
  deferred anyway), the cheapest correct move was to follow the bucket: change two
  literals in code rather than recreate a resource. Region is configuration, not
  identity; MEMORY.md had even pre-authorized exactly this swap.
- **S3 bucket names are globally unique** — across *all* AWS accounts, not per
  account. That's why the buckets carry random suffixes (`-k7f2`, `-ab1b`): a plain
  descriptive name may already be taken by anyone in the world.
- **AWS region strings become endpoint DNS names verbatim.** A typo'd region in
  `aws configure` (`east-us-2`) failed with *"Could not connect to the endpoint URL:
  https://sts.east-us-2.amazonaws.com"* — a connection error, not an auth error,
  because the SDK mechanically builds `<service>.<region>.amazonaws.com`. Lesson:
  endpoint-URL errors mean "check the region string", not "check the credentials".
- **Idempotency is the proof that IaC is telling the truth.** The definition of done
  wasn't "apply succeeded" but "the *second* apply prints `No changes.`" — evidence
  the code describes a fixed point it converges to, rather than a script that happens
  to run. First apply: 12 resources; second: zero.
- **Terraform outputs never need saving.** They live in the state file and
  `terraform output` reprints them anytime. Nothing in them is secret here (bucket
  names, ECR URL, role ARN — fine for a public repo). Related UI nicety: GitHub
  Actions variables are literal strings — paste values *without* quotes, or the
  quotes become part of the value.
- **Code written blind survived contact with reality.** The whole `infra/persistent`
  root was authored on a laptop with no terraform binary; the first-ever
  `fmt`/`validate`/`apply` ran clean. Small, boring, well-documented resources are a
  repeatable recipe for that.

## 2026-07-03 — The data layer: one preprocessing path, or train/serve skew wins

- **Train/serve skew is an architecture problem, not a testing problem.** The classic
  failure: the browser (or serving layer) reimplements preprocessing "identically" in a
  second language, and the two paths drift. The fix is structural — the browser sends the
  *raw* drawing (strokes + PNG) and everything down to the 28×28 tensor happens in one
  Python module that training imports too. The parity test then just *proves* what the
  architecture already guarantees.
- **Parity comes in two strengths.** Strokes-vs-training is *exact* (bit-identical
  tensors — both are the same function calls). Strokes-vs-PNG can only be *close*
  (vector rasterization vs pixel re-cropping), so that test asserts ink-overlap IoU and
  mean-difference bounds instead of equality. Knowing which guarantee is achievable
  where — and testing each at its achievable strength — is the actual skill.
- **Store uint8, normalize at the model boundary.** The processed artifact keeps raw
  28×28 uint8 bitmaps (4× smaller than float32); scaling to [0,1] happens in
  `bitmap_to_model_input`, the one function both training and serving call. Parity by
  construction — nobody can normalize differently because there is only one place
  normalization exists.
- **QuickDraw's formats:** Google ships per-class `.npy` archives of pre-rendered
  28×28 bitmaps (white ink on black) *and* raw/simplified stroke JSON. We train on
  their bitmaps but serve from strokes — so the stroke rasterizer only *approximates*
  their renderer (fit to a 256px box with margin, 16px lines, anti-aliased downsample).
  The residual gap is un-testable locally and is exactly what Evidently should see as
  drift in Phase 4: a known risk, monitored rather than wished away.
- **numpy seeding niceties:** `default_rng([seed, class_index])` derives an independent,
  reproducible stream per class (no hand-rolled `seed + i` collisions);
  `np.load(..., mmap_mode="r")` + sorted fancy indexing samples 10k rows from a ~100 MB
  archive without ever loading it.
- **Written blind:** this entire layer was authored on the locked-down work laptop with
  zero execution — no pytest, no ruff, not even an import check. Interfaces and
  invariants were designed to be verifiable later (deterministic functions, explicit
  contracts, loose-then-calibrate thresholds); the personal laptop gets to find out how
  it went.

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
