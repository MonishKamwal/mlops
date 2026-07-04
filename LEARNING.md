# LEARNING.md — things learned building this

Learning journal, newest first. Each entry: what happened, what was learned, why it
matters. This feeds the portfolio's Journey/devlog section (PLAN.md Phase 4). Claude:
add an entry whenever a task teaches a concept that wasn't obvious going in.

## 2026-07-04 — First training run: 0.915, and two artifact gotchas

- **`mlflow ui` doesn't find sqlite-backed runs on its own.** MLflow separates the
  *tracking store* (run metadata — here `mlflow.db`) from the *artifact store* (files —
  here `mlruns/`). A bare `mlflow ui` reads `./mlruns` as a file-based *tracking*
  store, finds no metadata there, and shows an empty UI — even though the run recorded
  perfectly. The UI is just another client and needs the same connection string the
  code used: `uv run mlflow ui --backend-store-uri sqlite:///mlflow.db`.
- **torch's ONNX exporter left a decoy.** The dynamo exporter wrote weights to a
  `model.onnx.data` sidecar; our metadata step (`onnx.load` → `onnx.save`) folded them
  back into a single self-contained `.onnx`, orphaning the sidecar — which, at the same
  size as the model, looked load-bearing. Verified `model.onnx` runs alone; export now
  deletes the stray, with a regression test pinning "serving ships exactly one file".
- **The 0.88 target fell in epoch 2; humans and CNNs agree on what's hard.** Final val
  accuracy 0.9151 with the curve still climbing at epoch 8 — on clean, plentiful data a
  ~420k-param CNN clears a portfolio-grade bar without tuning. The errors concentrate
  exactly where a person squinting at 28×28 doodles would struggle: dog, bird, and cat
  are the three worst classes (F1 0.77–0.84) while geometric shapes sail past 0.95 —
  there are many more ways to draw a dog than a star.

## 2026-07-04 — Building the training layer: the model is the easy part

- **PyTorch's default Linux wheel is a CUDA wheel.** A plain `torch` dependency drags
  ~2.5 GB of NVIDIA libraries into every CI run and lockfile — for a project that
  trains on a laptop and serves on Lambda, pure waste. The fix is a `[tool.uv.index]`
  entry for `download.pytorch.org/whl/cpu` plus a `[tool.uv.sources]` pin: torch (and
  only torch) resolves from the CPU index on every platform.
- **Dependency groups split train-time from serve-time.** torch/MLflow/onnx live in a
  `train` group, not in `[project.dependencies]` — the Phase 1 serving image should
  install onnxruntime, never torch. `[tool.uv] default-groups` keeps plain `uv sync`
  (and CI's `uv sync --locked`) installing everything, so the split costs nothing day
  to day.
- **The last epoch is not the best epoch.** Validation accuracy can peak before
  training ends, so `train.py` keeps the best-val-epoch weights in memory and writes
  those — checkpoint-the-best is early stopping's cheaper cousin: same artifact
  quality, no schedule tuning.
- **ONNX export is the second train/serve skew gap.** Task 1 closed preprocessing skew
  with one shared transform; the PyTorch→onnxruntime hop is another place outputs can
  silently diverge. So export ends with a parity check — the same batch through both
  runtimes, max |logit difference| under 1e-4. Bit-exact equality across different
  kernels is unattainable; tolerance, not `==`, and torch ≥ 2.9's dynamo-based
  exporter needs the separate `onnxscript` package (learned via test failure).
- **Model artifacts should be self-describing.** The class list is baked into both the
  checkpoint and the ONNX metadata: a model file that can't say what its output
  indices mean forces serving to trust an out-of-band params.yaml version — label
  mapping is part of the model, not the config.

## 2026-07-04 — Validation day: the lockfile is code, and blind thresholds meet reality

- **`uv sync --locked` turns dependency drift into a loud build failure.** Adding
  numpy/pillow/pyyaml to `pyproject.toml` on a machine without uv left `uv.lock`
  stale, so CI went red *by construction* — which is the design working, not a bug:
  the lockfile is part of the code, and the fix is one `uv sync` plus committing the
  regenerated lock, never fiddling with CI.
- **The blind-written layer passed first contact: 30/30 tests.** Everything authored
  without execution on the work laptop passed its first-ever run — including the
  *guessed* stroke-vs-PNG closeness thresholds (IoU > 0.4, mad < 0.15). The bet that
  paid off: deterministic functions, explicit contracts, and closeness bounds designed
  to be calibrated later rather than asserted exactly.
- **Raw QuickDraw is much bigger than what you train on.** Each class archive holds
  every drawing ever submitted — 90–230 MB per class, ~1.7 GB for 15 classes — and
  preprocessing samples 10k/class into one compact uint8 `.npz`. Fifteen large
  downloads is also where the atomic `.part`+rename / skip-existing logic earns its
  keep: an interrupted download can't leave a truncated archive that poisons later
  runs, and re-runs cost nothing.
- **A formatter-only diff is a good signal.** `ruff format` changed one file and zero
  logic — the entire executable delta from "written blind" to "validated" was
  whitespace.

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
