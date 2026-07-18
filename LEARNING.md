# LEARNING.md — things learned building this

Learning journal, newest first. Each entry: what happened, what was learned, why it
matters. This feeds the portfolio's Journey/devlog section (PLAN.md Phase 4). Claude:
add an entry whenever a task teaches a concept that wasn't obvious going in.

## 2026-07-18 — DVC day: make-for-data, and the cache is the point

- **DVC is `make` for data, and `dvc.lock` is the receipt.** Each stage declares
  cmd + deps (code files, params.yaml *sections*) + outs; repro re-runs only what
  changed. The lock file records every dep/out hash and lives in git — so a git
  commit now pins code, params, *and the exact bytes of every artifact*, while git
  itself stores only hashes. Param-scoped deps pay off immediately: download
  depends on `data.classes` alone, so tuning `samples_per_class` re-runs
  preprocess but not the 1.7 GB download.
- **DVC deletes a stage's outs before running it.** First repro re-downloaded all
  of `data/raw` even though the files sat right there — our script's skip-existing
  logic never got a chance. That's the contract, not a bug: a surviving output is
  unverifiable (is it produced, or stale?), so a stage must create its outs from
  nothing every time *it runs*. The toll is paid once; afterwards the stage
  doesn't run at all — outputs come back from cache.
- **The cache is content-addressed; the remote is just cache sync.**
  `.dvc/cache/files/md5/ab/cdef…` stores each unique blob once; workspace files
  are reflinks on APFS (no double disk). `dvc push` mirrors that layout into
  `s3://…/dvc/` — and per-out flags decide what belongs there: raw is
  `push: false` (Google's bucket is canonical; storing a second 1.7 GB copy buys
  nothing), `metrics.json` is `cache: false` (tiny, git-tracked, enables
  `dvc metrics diff` across commits). Not every artifact belongs in the remote —
  that's a per-out decision, not a global one.
- **Seeded training reproduced to four decimals, two weeks later.** Same params,
  re-downloaded data, fresh run: val 0.9151 / test 0.9157 / macro F1 0.9162 —
  identical to 2026-07-04, down to per-class F1s. Bit-identical weights are not
  promised across machines, but metric-identical on the same machine is what
  seeded determinism buys — and it turns "reproducible pipeline" from a claim
  into a diff.
- **Idempotency proof, again.** Same DoD instinct as Terraform: the second
  `dvc repro` printing five `didn't change, skipping` lines is the evidence the
  DAG describes a fixed point, not a script that happens to run.

## 2026-07-18 — Shipping day: mutable tags, pinned digests, safe intermediate states

- **Pushing `:latest` deploys nothing.** Lambda resolves the tag to an image *digest*
  at deploy time and pins it — ECR tags are mutable pointers; function config is
  immutable fact. A new image under the same tag is invisible until "Deploy new
  image" (or `update-function-code`) re-resolves the tag. This is also why
  Terraform's `image_uri` shows no drift: the function reports its pinned digest,
  not the moving tag. Phase 2's deploy workflow automates exactly this
  re-resolution step.
- **Order the rollout so every intermediate state is safe.** Apply → push → deploy
  meant: first an env var landed on a function whose code ignores it; then the new
  image sat in ECR serving nothing; only the final console deploy changed behavior —
  by which point its permission and config already existed. Every step individually
  reversible, API live throughout. The reverse order (code before config) would
  have been safe here too, but only because the logger fails open — ordering
  discipline is what makes rollouts safe *without* relying on such properties.
- **Fail-open logging inverts the verification.** An S3 failure costs a log line,
  never a prediction — so a working canvas proves nothing about logging, and the
  only trustworthy signal is objects appearing in the bucket. Hence the DoD was
  "draw, then watch `predictions/dt=…/` accumulate in the S3 Console" rather than
  any API response. It accumulated.

## 2026-07-06 — Prediction logging on Lambda: the freeze changes the design

- **Lambda freezes the container the instant the response returns.** Anything
  "fire-and-forget" — FastAPI BackgroundTasks, threads — doesn't fail loudly; it just
  stops mid-flight and resumes on the next thaw, or never if the environment is
  reaped. For data that must not be lost, the honest options are a synchronous write
  in the request path or real infrastructure (extension, queue). v0 takes the
  synchronous S3 PUT: ~tens of ms same-region, and every record provably lands
  before the response leaves.
- **Fail-open is a stance you write a test for.** Logging shares the request path,
  so its failure mode must be "one missing log line + a CloudWatch stack trace",
  never a failed prediction — enforced by a test that makes the fake S3 client throw
  and asserts `/predict` still returns 200. Same reasoning gives boto3 tight
  timeouts (connect 1 s, read 3 s, 2 attempts): a wedged S3 may cost moments, not a
  chunk of the 30 s invocation budget.
- **The plan said "middleware"; the record said otherwise.** ASGI middleware sees
  bytes, but the record's fields — digest of the canonical model input, ranked
  classes, source — exist only inside the handler. Log where the meaning lives; the
  digest covers the (1, 28, 28) float32 tensor, so identical drawings hash
  identically no matter how the JSON was formatted, and no raw user input is stored.
- **Config presence is the cleanest feature flag.** `PREDICTION_LOG_BUCKET` set →
  log; unset → no-op. The bucket name is knowledge only Terraform has, so infra
  injects it and the image stays generic — `docker run`, tests, and later EKS all
  get the safe default by doing nothing.
- **S3 key design is query design.** `predictions/dt=YYYY-MM-DD/…` is Hive-style
  partitioning: Athena and the Phase 4 drift jobs prune by prefix instead of
  scanning the bucket. One object per prediction, because a Lambda container serves
  one request at a time and any cross-request buffer dies in the freeze.
- **Append-only enforced by IAM, not by promise.** The exec role gets `s3:PutObject`
  on `predictions/*` and nothing else — the API cannot read, list, or delete even
  its own records. Least privilege doubling as an architecture statement: data flows
  one way, serving → logs → (Phase 4) drift reports.

## 2026-07-06 — Wiring a static site to a scale-to-zero API (Phase 1, task 5)

- **A warm-up ping doesn't have to be a no-op.** The plan said "GET /healthz on page
  load" — but *any* request boots the Lambda, so the frontend fires `GET /model-info`
  instead. One request does three jobs: warms the cold start while the visitor is
  still reading the hero text, feeds the "try: cat, bicycle…" prompt from the *live*
  class list, and gives the UI the model sha256 + val_accuracy to display. Nothing is
  hard-coded that the API can report about itself.
- **Function-URL-owned CORS answered the local-dev question for free.** Task 3
  deliberately kept CORS out of the FastAPI app (doubled headers break browsers) and
  parked "what about local frontend dev?" for this task. The answer is: nothing —
  `localhost:3000` is already on the Function URL allowlist, so `next dev` talks
  straight to the production Lambda. There is no local API tier at all. Verified with
  `curl -H "Origin: http://localhost:3000"`: the response echoes
  `Access-Control-Allow-Origin` and `Vary: Origin` shows per-origin matching.
- **Server-side bounding-box normalization keeps the client dumb.** Because
  `rasterize_strokes` normalizes every drawing to its own bounding box, the canvas
  sends raw coordinates in whatever space it draws in — no client-side scaling, no
  canvas size in the API contract. This is the no-train/serve-skew rule paying rent a
  second time: preprocessing that lives server-side can't drift when someone redesigns
  the frontend.
- **Make the cold start part of the story, not a spinner.** A warm Lambda answers in
  well under a second, so "in flight > 2 s" is a reliable cold-start detector. The UI
  uses it to switch from a generic "hmm…" to "the model is waking up — it scales to
  zero between visitors" — turning the platform's cheapest architectural decision into
  visible copy instead of apparent slowness.

## 2026-07-06 — Lambda Terraform: the parts the Console does for you silently

- **A "public" Function URL isn't public until you say so twice.** Setting
  `authorization_type = "NONE"` only disables IAM auth on the URL; invocation still
  requires a resource-based policy (`aws_lambda_permission` with
  `function_url_auth_type = "NONE"`, principal `*`). The Console attaches that policy
  behind the scenes when you click "create Function URL" — Terraform makes the hidden
  step visible, and forgetting it yields 403s on a URL that *looks* open.
- **Terraform owns the skeleton, CI owns the code — `ignore_changes` is the treaty.**
  From Phase 2 the deploy workflow updates the function image out-of-band
  (`lambda:UpdateFunctionCode` after the quality gate). Without
  `lifecycle { ignore_changes = [image_uri] }`, every later `terraform apply` would
  quietly roll the API back to the bootstrap image. This split — infra declarative,
  release imperative — is the standard pattern for "Terraform + CD both touch Lambda".
- **Pre-create the log group or pay forever.** If Lambda auto-creates
  `/aws/lambda/<name>`, retention is *never expire*. Declaring the
  `aws_cloudwatch_log_group` with `retention_in_days` before the function exists is
  the only clean way to cap it (plus it gets destroyed with the stack instead of
  lingering).
- **Memory is Lambda's only CPU dial.** vCPU scales linearly with memory (1 full vCPU
  at 1769 MB) — 1024 MB is bought not for RAM (the model is 1.6 MB) but so uvicorn
  boot + ONNX session init don't crawl through a cold start on ~0.29 vCPU.
- **Buildx's default attestations can make an image Lambda won't run.** Modern
  `docker build` attaches provenance attestations, turning the pushed artifact into an
  OCI image *index* (visible as "exporting attestation manifest" in the build log) —
  and Lambda only accepts single-platform image manifests. `--provenance=false` at
  build time keeps the artifact a plain manifest. Ordering also matters: the image
  must exist in ECR before the first apply, because Lambda validates `image_uri` at
  function creation.

## 2026-07-06 — Serving day: one HTTP image that Lambda can also run

- **Lambda Web Adapter is an extension, not a framework.** The whole "one image for
  Lambda and EKS" trick is a single `COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter`
  line dropping a binary into `/opt/extensions/`. On Lambda, that extension registers
  with the runtime API, polls `AWS_LWA_READINESS_CHECK_PATH` (our `/healthz`) until the
  web server is up, then forwards each invocation as a plain HTTP request to `PORT`.
  Anywhere else the extension simply never runs — `docker run` gives an ordinary
  uvicorn server. No Mangum, no handler shim, zero Lambda-specific code in the app.
- **The venv is the deployable unit in a uv multi-stage build.** Builder stage: copy
  `/uv` from Astral's image (pinned), `uv sync --locked --no-default-groups` — the
  task-2 dependency-group split pays off here: runtime deps only, no torch, no pytest.
  Runtime stage: copy `/app/.venv` and put it on `PATH`. Both stages share the same
  `python:3.12-slim-bookworm` base so the venv's interpreter symlink resolves. Deps
  install before source COPY, so code edits rebuild in seconds. Image: 441 MB vs
  multi-GB with torch inside.
- **The model file is the API's config.** The app reads the class list, val_accuracy,
  and even the graph's input/output names from `model.onnx` itself (metadata +
  `session.get_inputs()`), and `/model-info` serves the file's sha256. No params.yaml
  in the image, no constants imported from training code (that would drag torch in — a
  subprocess test pins `import quickdraw.serving.app` torch-free), and any deployed
  container can prove exactly which artifact it is running.
- **Load the model in the lifespan, map domain errors to 400s.** Loading in FastAPI's
  lifespan (not at import) means a broken model fails startup — before Lambda's
  readiness check ever passes — instead of surprising the first request. And the
  shared preprocess module's `ValueError`s ("empty drawing", bad strokes) are client
  errors: catch and return 400 with the message, or FastAPI 500s on them. One subtlety:
  `binascii.Error` (bad base64) already *is* a `ValueError`, and PIL raises `OSError`
  for undecodable images — the except clause needs both.
- **Ports below 1024 are privileged; 8080 is not.** The container binds 8080 — the Web
  Adapter's default — which needs no root and no capabilities, keeping a future
  non-root container user cheap. Nothing in the Lambda path cares about port 80: the
  Function URL fronts whatever port the adapter forwards to.

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
