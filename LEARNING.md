# LEARNING.md — things learned building this

Learning journal, newest first. Each entry: what happened, what was learned, why it
matters. This feeds the portfolio's Journey/devlog section (PLAN.md Phase 4). Claude:
add an entry whenever a task teaches a concept that wasn't obvious going in.

## 2026-07-23 — Evidently drift: the method silently flips direction on you (and p-values lie at scale)

- Evidently's `DataDriftPreset` **auto-selects the drift method by sample size**: small samples
  get statistical tests (K-S / chi-square, value = **p-value**, drifted when value **<** 0.05);
  large ones (our reference is 15k rows) get **distance** metrics (Wasserstein / Jensen-Shannon,
  value = a **distance**, drifted when value **>** threshold). Same field name, opposite
  direction. My first cut hard-coded `drifted = value < threshold`, so on the real 15k-vs-300
  comparison it reported `confidence` as *not* drifted even though its mean had fallen 0.907 →
  0.482 — a backwards drift report, worse than none. The tell: Evidently's own
  `DriftedColumnsCount` said 2 columns drifted while my per-column booleans said the other 2.
- Two lessons. (1) **Don't recompute a library's verdict from its raw score unless you know the
  method** — either read the library's own drifted flag, or *pin* the method so the direction is
  fixed. I pinned `num_method="wasserstein"`, `cat_method="jensenshannon"`, threshold 0.1, so
  every column uses a distance and `drifted = score > threshold` is uniformly correct. (2)
  **Distances beat p-values for drift at these sizes anyway**: with 15k reference rows, K-S
  p-values are hypersensitive — any trivial difference gives p≈0, so *everything* "drifts". A
  distance measures *magnitude* independent of n, which is what you actually want to threshold on.
- Verified end-to-end: synthetic low-confidence "live" logs vs the real reference → confidence
  Wasserstein 2.34 (drifted), margin 1.91 (drifted), predicted_label JS 0.06 (not drifted) —
  matching Evidently's count of 2. The `drift.json` contract carries `{method, score, threshold,
  drifted}` per column so it's self-describing, plus self-computed histograms for the site.

## 2026-07-22 — Wiring the monitoring stack: two silent gotchas (scrape selection + a library registry bug)

- **kube-prometheus-stack ignores "foreign" ServiceMonitors by default.** The Prometheus
  operator only selects ServiceMonitors that carry the stack's own `release` label — so our
  `quickdraw-api` chart's ServiceMonitor would be silently *not scraped*, no error, just a
  missing target. Two fixes: add the `release: <stack>` label to our monitor (couples our
  chart to the stack's release name), or set `serviceMonitorSelectorNilUsesHelmValues: false`
  in the stack's values, which renders `serviceMonitorSelector: {}` on the Prometheus CR =
  "select all". Took the latter to keep the API chart decoupled. Verified locally with
  `helm template ... | grep serviceMonitorSelector` before ever touching a cluster — value
  toggles like this don't show up literally, so check the *rendered CR field*, not the flag.
- **`prometheus-fastapi-instrumentator`'s in-progress gauge ignores the custom registry.**
  Wanted the plan's in-flight-requests panel, which needs `should_instrument_requests_inprogress
  =True`. But that gauge collides ("Duplicated timeseries in CollectorRegistry") the moment a
  second app is built in-process — which the test suite does constantly. `Instrumentator` takes
  a `registry=` arg that should isolate it, but reading the lib source (middleware.py) the
  inprogress `Gauge(...)` is created with **no `registry=`**, so it always lands on the global
  default `REGISTRY` regardless. So the arg can't fix it; you'd need a custom gauge or a
  test-registry reset. Not worth it for one panel that would also have coupled Task 5 to a
  serving-image rebuild — dropped it for a "requests by status" panel off the metrics the app
  already emits. Lesson: when a library arg "should" fix something and doesn't, read the source
  before building scaffolding around it — the fix may be one it structurally can't apply.

## 2026-07-22 — "Unhealthy nodes": nodes join but stay NotReady when the CNI isn't managed

- With the instance-type wall cleared (`t4g.small` Graviton), the first apply got *further* and
  died differently: `NodeCreationFailure: Unhealthy nodes in the kubernetes cluster`. The word
  matters — **"unhealthy" ≠ "failed to join."** The two `t4g.small` instances launched (visible
  `Running` in EC2), registered with the control plane (so node→API networking is fine), and
  then sat **NotReady** until the managed node group timed out and rolled back. A registered EKS
  node is held `NotReady` until its **network plugin (VPC CNI / `aws-node`) reports ready** —
  so "unhealthy nodes" at create time is almost always a CNI problem, not a networking or IAM one.
- Root cause: we declared *no* addons, leaning on the cluster's self-bootstrapped default CNI.
  On a fresh cluster the node group can come up before that CNI is configured, so nodes register
  into a cluster with no working network plugin. Fix: manage the addon and **order it before the
  nodes** — `addons = { vpc-cni = { before_compute = true } }`. `before_compute` installs/configures
  the CNI *before* the node group is created, so nodes find a ready network plugin the moment they
  join. (coredns/kube-proxy don't need it — coredns is a Deployment that schedules once nodes exist.)
- Two gotchas found in passing: (1) the EKS module **v21 renamed `cluster_addons` → `addons`**
  (the v20 name errors as "argument not expected"); (2) we'd been **flying blind on the failure**
  because the `Configure kubectl` step had no `if: always()`, so on an apply failure it was skipped
  and the `if: always()` evidence step's `kubectl` calls hit no kubeconfig (swallowed by `|| true`).
  The control plane *does* exist between an apply failure and the `if: always()` destroy, so making
  kubectl config + node diagnostics (`describe nodes`, `aws-node` logs) run `if: always()` turns a
  blind 40-minute cycle into a self-explaining one. Lesson: a failure-path diagnostic is only useful
  if it actually runs on the failure path — check the `if:` conditions, not just that the step exists.

## 2026-07-22 — The free plan blocks worker nodes three ways; "control plane works" ≠ "EKS works"

- Task 0 checked "is EKS blocked?" by creating a **control-plane-only** cluster — it worked,
  so we recorded "EKS is NOT blocked on the free plan." That was a false all-clear: the
  control plane is a managed AWS service, but **worker nodes are EC2**, and EC2 is where the
  post-July-2025 free plan bites. The first real `eks-demo` apply got all the way through the
  control plane and died at the node group: `AsgInstanceLaunchFailures ... t3.medium is not
  eligible for Free Tier`. Lesson: verify the part that's actually constrained, end to end —
  a smoke test that stops short of the constraint proves nothing about it.
- The new free plan (accounts created on/after 2025-07-15) constrains nodes **three** ways,
  and you hit them in sequence:
  1. **Instance eligibility.** Only a fixed list is free-tier eligible: `t3.micro`,
     `t3.small`, `t4g.micro`, `t4g.small`, `c7i-flex.large`, `m7i-flex.large` (6 months).
     `t3.medium` isn't on it → launch refused. (This differs from the *legacy* free tier,
     whose rule was "t2.micro, or t3.micro only where t2 is unavailable.")
  2. **vCPU service quota.** New accounts *can* default to a **1-vCPU** quota on "Running
     On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances." *Every* eligible type is ≥2
     vCPU, so if you have that default, nothing launches until you request an increase (Service
     Quotas console) — a separate failure that only surfaces *after* you fix eligibility. Check
     it first: on this account it was already **16**, so it never actually bit us — but it's the
     classic second wall, worth ruling out before a 45-minute apply discovers it for you.
  3. **Architecture.** Our serving image is single-platform `linux/arm64` (to match the
     Lambda tier), so nodes must be arm64 too. `t3.*` is x86_64 — it would boot and then fail
     to run the pod (`exec format error`). Only `t4g.*` (Graviton) is both eligible *and*
     arm64. So the arm64 constraint that felt like a burden actually *narrows the choice to
     the right answer* and reinforces the "one artifact, both tiers" story: local Apple
     Silicon → Lambda → Graviton nodes are all arm64, one image the whole way.
- Resolution (staying on the free plan, no paid upgrade): node group → `t4g.small` Graviton,
  `ami_type = AL2023_ARM_64_STANDARD` (the module defaults to x86_64 and won't infer arch
  from `instance_types`), `capacity_type = ON_DEMAND` (these types are free *on-demand*;
  spot would bill spot price against credits for nothing), plus a one-time vCPU quota bump.

## 2026-07-21 — Module constants as default args aren't monkeypatchable

- `def load_x(path: Path = MODULE_CONST)` captures `MODULE_CONST`'s *value* at function
  definition (import) time, not per call. So a test that does
  `monkeypatch.setattr(module, "MODULE_CONST", tmp)` and then calls `load_x()` still reads
  the original path — the default was frozen at import. It bit the model-card tests (they
  kept reading the real `MODEL_CARD.md` instead of a temp one) and had been quietly masking
  a stale read in an eval-metrics test that simply never asserted on the patched content.
- Fix: default the parameter to `None` and resolve the constant inside the body
  (`path = path or MODULE_CONST`), so the lookup happens at call time and the constant stays
  patchable. Rule of thumb: if a module constant is meant to be overridable — by tests or
  callers — read it in the body, not in the signature.

## 2026-07-21 — Path filters are file-level: broad globs couple unrelated work to expensive jobs

- A `paths: [src/quickdraw/**]` trigger on the train→deploy workflow meant that adding an
  *evidence-hub* module under `src/quickdraw/evidence/` retrained the model and redeployed
  the Lambda — an ~8-min build for a change that can't touch the model. Path filters match
  file locations, not intent; a glob that's convenient today silently widens every time the
  package grows a subpackage with a different purpose.
- The gate contained the blast radius — each unintended challenger shipped within-ε and never
  re-crowned, so champion held — but "nothing broke" isn't "nothing happened": the registry
  collected throwaway versions (v4, v5) and the Lambda was redeployed for free. The fix is to
  enumerate the job's *real* inputs (`config.py`, `data/**`, `training/**`, `serving/**`,
  pipeline, params, deps) instead of globbing a whole package — which also makes reality match
  the trigger's own comment, "only model-affecting changes redeploy."

## 2026-07-21 — The evidence hub: publish from the registry, split data from styling

- **Publish from the source of truth, not a checked-in snapshot.** The git-tracked
  `reports/eval/metrics.json` is one machine's artifact — it still reads 0.9157 while the
  registry's champion is v2 @ 0.9170 (a CI retrain that re-crowned). A dashboard that
  reads the **MLflow registry** for "what's champion and its number" cannot drift out of
  sync with reality; one that reads the committed file can. So `export.py` gathers runs +
  aliases from the registry and uses the eval JSON only for supplementary per-class detail.
- **Separate data from presentation with a JSON contract.** The hub writes `evidence.json`
  (pure data) next to `index.html` (one default rendering). Anything that wants to
  restyle — here, the portfolio site later — consumes the JSON and builds its own
  components; the HTML/CSS in this repo are deliberately throwaway. The mechanism:
  `build_data()` returns only JSON-serializable content; `build_context()` is that plus
  presentation-only keys (chart markup, the plotly CDN); `render()` strips those keys back
  out to produce the JSON — so both artifacts are provably rendered from the same data and
  can't disagree.
- **One OIDC permission, two federations.** `id-token: write` is what lets
  `configure-aws-credentials` mint a token AWS trades for role credentials — *and* what
  `actions/deploy-pages` uses to authenticate the Pages deploy. The same line does double
  duty; the Pages half additionally needs `pages: write`. Worth knowing before reaching for
  a second auth mechanism you don't need.
- **Read the DB, not the artifacts.** Everything the hub needs — run metrics, versions,
  aliases — lives in `mlflow.db`, so `mlflow_sync.sh pull` (one file) is the entire data
  fetch; no S3 artifact download. The confusion-matrix PNG (a DVC out) is the lone extra,
  pulled best-effort, and the page degrades gracefully when it's absent.
- **A dep group can be import-visible to tests yet absent from the image.** `evidence`
  (jinja2, plotly) sits in `default-groups` so `ci.yml`'s pytest can import the module,
  while the Dockerfile's `--no-default-groups` keeps it out of the serving image — the same
  lever the `train` group uses: default-for-dev, excluded-for-prod.

## 2026-07-20 — First OIDC deploy: the merge→live path, proven

- **OIDC is keyless deploy — the credential is minted per run and expires.**
  `id-token: write` lets GitHub mint a short-lived OIDC JWT; `configure-aws-credentials`
  trades it at AWS STS via web-identity federation for ~1h temporary credentials. No
  AWS access key is ever stored in the repo — nothing to rotate, nothing to leak. The
  proof it worked is one log line: `Authenticated as … :GitHubActions`. The security
  boundary isn't a secret, it's the trust policy (`infra/persistent/iam.tf`), which
  pins the allowed subject to `repo:MonishKamwal/mlops:*` — only workflows in this repo
  can assume `gha-app`.
- **Prove risky new plumbing on your terms.** The workflow's `push` trigger ignores
  changes to itself, so merging it didn't fire it — the first exercise of the untested
  OIDC + arm64 paths was a deliberate `workflow_dispatch` with a human watching, not a
  surprise on some future model merge. New infra plumbing should get its first run where
  you can see it fail.
- **arm64-via-QEMU is only cheap because the serving image has no torch.** Lambda runs
  on Graviton (arm64); the amd64 runner cross-builds under QEMU emulation, and emulated
  CPU work is slow. But the serving image installs onnxruntime only — torch lives in the
  `train` group, excluded from the image — so the emulated `uv sync` stays light and the
  whole build ran ~7.5 min. This is the earlier "one image, serving deps only" decision
  paying off: with torch in the image, the emulated compile would have been brutal.
- **`--provenance=false` + deploy by digest.** buildx defaults to pushing a multi-platform
  OCI index (with a provenance attestation); Lambda's image loader rejects that and wants
  a single-platform manifest — hence `--provenance=false` (a Phase 1 shipping-day lesson,
  now automated). And the Lambda is pointed at `repo@sha256:…`, not a mutable `:latest`
  tag: the digest is the immutable fact of exactly which bytes are live.
- **The smoke test is what makes "deployed" mean "serving the new model".**
  `update-function-code` succeeding only proves the API call worked. The real assertion
  is `sha256(the onnx we just built)` == the live `/model-info.model_sha256`, retried
  through the post-update cold start. Without that equality check a silently-stale image
  would pass unnoticed — deploy verification should assert artifact identity, not just a 200.
- **`dvc pull` reporting missing raw is success, not failure.** The 1.7 GB raw archives
  are `push:false` (Google's GCS is canonical), so pull can never fetch them; the step is
  best-effort (`|| echo …`) and `dvc repro` re-downloads raw from GCS. A cross-run
  `actions/cache` on `data/raw` (keyed on `params.yaml`) keeps that download from
  happening every deploy — a class-list change busts the key and re-fetches.

## 2026-07-20 — The quality gate: decouple "ship it" from "it's the new best"

- **The gate is the part of the system allowed to say "no".** Everything upstream
  produces a model; the gate is the one component whose job is to *refuse* to ship
  one. Two rules from `params.yaml`: an absolute floor (`min_test_accuracy`, so
  "better than a bad champion" isn't enough) and a no-regression band
  (`challenger ≥ champion − epsilon`). Pass → exit 0 → deploy proceeds; fail →
  metric-diff summary + exit 1 → the workflow dies before anything ships. A nonzero
  exit code *is* the enforcement — a Python `sys.exit(main())` returning 1 is what
  stops the next CI step.
- **ε exists because training isn't bit-exact everywhere.** Seed-identical runs are
  byte-identical on CPU but not on GPU backends; without a tolerance band, a 0.001
  wobble would block every deploy. ε=0.5pp treats sub-noise dips as noise.
- **The subtle bug ε introduces, and the fix: separate "deploy" from "re-crown".**
  The naive design is *pass ⟹ promote champion*. But then a string of within-ε
  challengers each becomes champion, and because the gate compares against champion,
  its own baseline *ratchets* down by up to ε every run — the bar you're enforcing
  quietly erodes. The fix is to decouple the two decisions: **passing means "ship
  this challenger"; the champion alias moves only on a *strict* improvement**
  (`challenger > champion`). So `champion` is the best model ever validated — a fixed
  high-water mark — and the gate's baseline can't drift. This is why `run_gate`
  computes `promoted = passed and challenger > champion` separately from `passed`.
- **Every invariant you gain costs one you give up — name it.** With this split,
  `champion` no longer means "what's deployed"; the live model may sit up to ε below
  it, and *no* alias tracks the deployed version (the deploy workflow's built image
  is the source of truth for what's live). That's an acceptable trade for a
  non-eroding quality bar, but it's a real change in what the alias *means* — worth
  saying out loud rather than discovering later when something reads `champion`
  expecting "production".
- **Read before you write.** `run_gate` reads the champion's accuracy *before* any
  promotion — otherwise, after promoting, it would be comparing a model against
  itself. Order matters when the thing you compare against is the thing you mutate.
- **Not every step belongs in the DVC DAG.** The gate mutates registry state (moves
  an alias) and reads it from S3-synced MLflow — neither pure nor file-hashable, so
  it can't be a `dvc repro` stage. It's a CLI run *after* repro, wired as
  `dvc repro → gate → build/push` in the deploy workflow (task 5). Rule of thumb:
  DVC stages are reproducible functions of files; anything with external side
  effects or registry reads is orchestration, not pipeline.
- **Keep policy pure, keep I/O thin.** `decide()` is a side-effect-free function of
  three floats returning `(passed, reasons)`, so the entire pass/fail matrix is a
  six-line parametrized unit test with no MLflow at all; `run_gate()` does the alias
  reads and the one conditional promotion. Testing the policy shouldn't need a database.

## 2026-07-18 — MLflow state goes to S3: sync the DB, never the artifacts

- **MLflow bakes absolute paths into the tracking DB.** Each experiment records
  its artifact root as an absolute URI — locally that's `file:///Users/...`. Sync
  that DB to CI and every new run tries to write artifacts into a path that only
  exists on the laptop. The structural fix: artifacts go to S3 *natively* (the
  experiment is created with an `s3://` root), because S3 URIs mean the same
  thing on every machine. The DB becomes the only synced state — one
  `aws s3 cp` each way — and `mlruns/` syncing dies before it was born.
- **The env-presence switch earns its third use.** `MLFLOW_STATE_BUCKET` set →
  S3 artifact root + sync; unset → fully local and AWS-free. Same pattern as
  `PREDICTION_LOG_BUCKET`: infra knowledge is injected, never baked into code,
  and tests force the unset state via an autouse fixture so a developer's shell
  exports can't make the suite touch AWS.
- **Guard the trap, don't document it.** If the bucket is set but the experiment
  carries a local artifact root (a pre-shared-state DB), training refuses to run
  with an explanation — the failure mode is eliminated by a check, not a README
  warning. Corollary: the laptop-era mlflow.db was archived, not uploaded;
  shared history starts the day tracking became shared infrastructure.
- **Registry aliases are deployment metadata, not history.** `challenger` always
  points at the latest trained version (set by train), `champion` at what
  deploys (moved only by the gate — task 4). Evaluate logs test metrics onto
  the challenger's *run*, so every version permanently carries the numbers it
  will one day defend as champion. The first version bootstraps as both — a
  registry without a champion would deadlock the gate.

## 2026-07-18 — Data validation: a schema is a contract, a dep edge is a gate

- **Pandera validates dataframes, so give it a dataframe worth validating.** The
  npz is tensors, not tables — the stage reduces it to one metadata row per
  (split, class): count, pixel min/max, mean ink fraction. The schema then *is*
  the documentation of what healthy data looks like: exact split sizes, every
  class in every split, label→class order matching params.yaml, true-black
  background, near-white ink present, ink fraction away from blank and solid.
  Tensor-level facts a dataframe can't carry (dtypes, shapes, the artifact's
  embedded class list) are plain ValueErrors checked first.
- **A validation stage only gates what depends on it.** DVC has no "run this
  before that" ordering — only data edges. Making train depend on the
  validation *report file* is what turns validation from a sibling stage into a
  gate: the stage fails → no report → train cannot run. Structure, not
  discipline.
- **DVC outs must be deterministic — no timestamps in reports.** The report is
  hashed like any artifact; a timestamp would make every run look changed and
  poison the cache. (A test asserts byte-identical output across runs.)
- **The plan moved, and that's the plan working.** PLAN.md sketched validate
  before preprocess, but every prescribed check describes the *processed*
  artifact, and malformed raw data already crashes preprocess loudly. The gate
  belongs at the door of the expensive silent consumer: training. PLAN.md
  amended, reasoning recorded.
- **Same-machine seeded training is byte-deterministic — and DVC noticed.**
  Rewiring train's deps forced it to re-run; it produced a bit-identical
  model.pt, so DVC skipped evaluate and export outright: the cascade stops at
  the first unchanged artifact, like ccache for ML. (Identical loss curves to
  four decimals were the tell; the skipped stages were the proof.)

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
