# Architecture

The full system, plane by plane. This is the **detailed** reference — every step that actually
runs, drawn from the workflows, the DVC DAG, and the Terraform roots. The portfolio site carries
a slimmed-down version of §1; the rest is here for depth and for the README.

Design rationale for each choice lives in [`PLAN.md` §2](PLAN.md); this document is *what happens*,
not *why that tool*.

---

## 1. System overview

Four planes: an always-on serving plane at ~$0, a CI/CD plane on GitHub Actions, an ephemeral
Kubernetes plane that exists ~30 minutes a month, and a monitoring plane that closes the loop
back into training.

```mermaid
flowchart TB
    subgraph Client["Browser — monishkamwal.github.io"]
        CANVAS["Canvas demo<br/>draw → strokes"]
        VERDICT["👍 / 👎 + class picker"]
        HUB_VIEW["Evidence + drift sections<br/>rendered from JSON contracts"]
    end

    subgraph Serve["Always-on serving plane — AWS Lambda, ~$0"]
        FURL["Lambda Function URL<br/>CORS allowlist"]
        APP["FastAPI + ONNX Runtime<br/>container image, arm64<br/>Lambda Web Adapter"]
        PRE["Shared preprocessing<br/>quickdraw.data.preprocess"]
        ONNX["model.onnx<br/>baked into the image"]
    end

    subgraph S3["S3 — persistent state"]
        LOGS[("logs bucket<br/>predictions/ feedback/ captures/")]
        DATA[("data bucket<br/>DVC remote · MLflow · monitoring/")]
        TFS[("tfstate bucket<br/>persistent/ + ephemeral/ keys")]
    end

    subgraph GHA["GitHub Actions — CI/CD plane"]
        CI["ci.yml<br/>lint · format · tests"]
        TD["train-deploy.yml<br/>pipeline → gate → build → deploy"]
        DR["drift-report.yml<br/>weekly monitoring"]
        EP["evidence-pages.yml<br/>render + publish hub"]
        EKS_W["eks-demo.yml + eks-failsafe.yml<br/>monthly cluster"]
    end

    subgraph Registry["MLflow — registry + tracking"]
        MLDB[("mlflow.db<br/>SQLite, synced to S3")]
        ALIAS["aliases: champion / challenger"]
    end

    subgraph Eph["Ephemeral plane — exists ~30 min/month"]
        EKSC["EKS cluster + VPC<br/>3× t4g.small Graviton"]
        HELM["Helm: quickdraw-api<br/>same image, by digest"]
        PROM["kube-prometheus-stack<br/>Prometheus + Grafana"]
        K6["k6 load test"]
    end

    ECR[("ECR<br/>serving image")]
    PAGES["GitHub Pages<br/>monishkamwal.github.io/mlops"]

    CANVAS -->|"POST /predict"| FURL
    VERDICT -->|"POST /feedback"| FURL
    FURL --> APP
    APP --> PRE --> ONNX
    APP -->|"one JSONL per request"| LOGS
    APP -->|"labeled drawing"| LOGS

    CI -.->|"gates the PR"| TD
    TD --> Registry
    TD -->|"push image"| ECR
    ECR -->|"update-function-code by digest"| APP
    TD --> DATA
    LOGS --> DR
    DATA --> DR
    DR -->|"drift.json · feedback.json"| DATA
    DR -->|"alert issue"| GHUB["GitHub issue<br/>label: drift-alert"]
    Registry --> EP
    DATA --> EP
    EP --> PAGES --> HUB_VIEW
    LOGS -->|"captures, opt-in retrain"| TD

    EKS_W --> EKSC --> HELM
    ECR --> HELM
    PROM --> K6
    EKS_W -->|"api-metrics.json + evidence"| PAGES
    TFS --- GHA
```

**The one-sentence version:** a visitor's drawing becomes a prediction, a log line, and — if they
grade it — labeled training data; every merge retrains, gates, and redeploys the model without a
human; every week the logs are scored for drift; every artifact behind those claims is published.

---

## 2. Training & deploy plane — `train-deploy.yml`

Fires on push to `main` touching model inputs — `config.py`, `data/**`, `training/**`,
`serving/**`, `dvc.yaml`, `dvc.lock`, `params.yaml`, `Dockerfile`, `uv.lock` — or on manual
dispatch. `concurrency: train-deploy`, no cancel-in-progress: a single writer for `mlflow.db`.

```mermaid
flowchart TB
    START["push to main touching model inputs<br/>OR workflow_dispatch"] --> OIDC

    OIDC["Assume AWS role via OIDC<br/>gha-app — no stored keys"] --> PULLM
    PULLM["mlflow_sync.sh pull<br/>s3://data/mlflow/mlflow.db"] --> PULLD
    PULLD["dvc pull — best-effort<br/>raw is push:false, so it reports missing"] --> BRANCH

    BRANCH{"include_captures?"}

    BRANCH -->|"no — the normal path"| REPRO
    REPRO["dvc repro<br/>the full DAG"] --> PUSHD["dvc push<br/>cache derived outs"] --> REF

    BRANCH -->|"yes — the retrain path"| RVAL
    RVAL["dvc repro validate<br/>data checks only"] --> SYNCC
    SYNCC["aws s3 sync captures/dt=… <br/>60-day window"] --> AUG
    AUG["training.augment<br/>select → rasterize → fold into TRAIN split only"] --> EXPL
    EXPL["explicit train → evaluate → export → reference<br/>skips dvc repro/push: live data isn't reproducible"] --> REF

    REF["Publish reference.csv<br/>→ s3://data/monitoring/reference.csv"] --> GATE

    GATE{"Quality gate<br/>training.gate"}
    GATE -->|"FAIL — below 0.85 floor<br/>or regression beyond ε=0.005"| STOP["Exit 1 + metric-diff summary<br/>deploy steps skipped<br/>live model untouched"]
    GATE -->|"PASS"| BUILD

    BUILD["Build arm64 image<br/>QEMU + buildx, --provenance=false"] --> PUSH
    PUSH["Push to ECR"] --> DEPLOY
    DEPLOY["lambda update-function-code BY DIGEST<br/>+ wait function-updated"] --> SMOKE
    SMOKE["Smoke test the live Function URL<br/>assert /model-info.model_sha256 == built onnx"] --> PUSHM
    PUSHM["mlflow_sync.sh push — if: always()<br/>so a blocked challenger's lineage still persists"] --> DONE["Evidence hub rebuild<br/>triggered by workflow_run"]

    STOP --> PUSHM
```

### The DVC DAG inside `dvc repro`

Each stage hashes its code deps + `params.yaml` sections; only what changed re-runs.

```mermaid
flowchart LR
    P["params.yaml<br/>classes · split · hyperparams · gate thresholds"]

    DL["download<br/>15 class archives from Google's public GCS"]
    PP["preprocess<br/>28×28 uint8 · deterministic stratified split"]
    VA["validate<br/>Pandera schema over per-split/class metadata"]
    TR["train<br/>PyTorch CNN · MLflow run · registers a version"]
    EV["evaluate<br/>confusion matrix · per-class metrics"]
    EX["export<br/>ONNX + parity check vs PyTorch"]
    RF["reference<br/>output distribution over the test split"]

    P -.-> DL & PP & VA & TR
    DL -->|"data/raw — cached, push:false"| PP
    PP -->|"quickdraw.npz"| VA
    VA -->|"reports/data_validation.json"| TR
    PP --> TR
    TR -->|"models/model.pt"| EV
    TR --> EX
    EX -->|"models/model.onnx"| IMG["baked into the serving image"]
    TR --> RF
    PP --> RF
    EV -->|"metrics.json — git-tracked, cache:false"| GITD["dvc metrics diff on PRs"]
    RF -->|"reference.csv"| MON["drift baseline"]
```

The `validate → train` dependency edge is what makes validation a **gate** rather than a sibling
stage: no report, no training.

### Gate semantics

```mermaid
flowchart LR
    CH["challenger<br/>this run's test_accuracy"] --> D1{"≥ 0.85 floor?"}
    D1 -->|no| BLOCK["BLOCK — exit 1"]
    D1 -->|yes| D2{"≥ champion − ε<br/>ε = 0.005?"}
    D2 -->|no| BLOCK
    D2 -->|yes| SHIP["SHIP the challenger"]
    SHIP --> D3{"strictly > champion?"}
    D3 -->|yes| CROWN["move the champion alias<br/>the bar rises"]
    D3 -->|no| HOLD["champion unchanged<br/>the bar never erodes"]
```

**Deploy and re-crown are decoupled.** Passing means "ship this"; the `champion` alias moves only
on a strict improvement, so champion is the best-ever quality bar rather than a record of what's
live. Nothing tracks "currently deployed" by alias — the built image digest is the source of truth.

---

## 3. Serving plane — the request path

One FastAPI + ONNX Runtime container image, arm64. It runs unmodified on Lambda (via the Lambda
Web Adapter), under `docker run` locally, and on EKS — no code fork between tiers.

```mermaid
flowchart TB
    subgraph Load["On page load"]
        WARM["GET /model-info<br/>warm-up ping"] --> META["live class list · model sha256 · val accuracy<br/>feeds the UI; 'waking up' state past 2 s"]
    end

    subgraph Predict["POST /predict"]
        IN["strokes — QuickDraw [[xs],[ys]] — and/or base64 PNG<br/>raw canvas coords; strokes win if both"]
        IN --> NORM["bbox-normalize → rasterize → 28×28 → model input<br/>THE SHARED MODULE — same code trained on"]
        NORM --> INF["ONNX Runtime session"]
        INF --> OUT["ranked classes + confidences → top-3 bars"]
        OUT --> PLOG["prediction_log.py<br/>one JSONL → predictions/dt=YYYY-MM-DD/"]
    end

    subgraph Feedback["POST /feedback → 204"]
        FIN["self-contained event:<br/>predicted_label · confidence · correct · source · model_sha256<br/>optional: strokes + true_label"]
        FIN --> FLOG["feedback_log.py<br/>→ feedback/dt=…/  — the lightweight verdict stream"]
        FIN --> LABEL{"strokes present<br/>AND labelable?"}
        LABEL -->|"👍 → label = the guess"| CLOG
        LABEL -->|"👎 + correction → label = true_label"| CLOG
        LABEL -->|"👎, no correction"| NOCAP["verdict logged, no capture<br/>an unlabeled error isn't trainable"]
        CLOG["capture_log.py<br/>→ captures/dt=…/  — the heavy labeled stream"]
    end

    HEALTH["GET /healthz"] -.-> RDY["Lambda Web Adapter readiness<br/>+ K8s startup/liveness/readiness probes"]
    METRICS["GET /metrics"] -.-> SCRAPE["Prometheus scrape — only on the EKS tier"]
```

Three properties worth stating out loud:

- **No train/serve skew by construction.** The browser sends the raw drawing; every transform down
  to the 28×28 tensor happens server-side in the same module the training pipeline imports. Parity
  tests enforce it.
- **All logging is fail-open.** An S3 outage costs a log line, never a prediction.
- **Writes are synchronous.** Lambda freezes after the response, so a background task would lose
  data.
- **Privacy-first by default.** The prediction log stores `input_sha256`, never pixels. Drawings
  are captured *only* on an explicit 👍/👎 with an upfront consent notice — no identity, no tracking.

---

## 4. Monitoring plane — `drift-report.yml`

Weekly, Mondays 07:00 UTC, plus dispatch. Drift is measured on the model's **output** distribution
— predicted-class mix, top-1 confidence, top1−top2 margin — not on input pixels, because the
prediction log deliberately doesn't retain them.

```mermaid
flowchart TB
    CRON["cron: Mon 07:00 UTC<br/>or dispatch with a min_confidence override"] --> OIDC2["OIDC → gha-app"]

    OIDC2 --> FETCH["Fetch reference.csv<br/>from the stable S3 key, not dvc pull"]
    OIDC2 --> SYNC1["Sync 30-day window<br/>predictions/dt=…"]
    OIDC2 --> SYNC2["Sync 30-day window<br/>feedback/dt=…"]
    OIDC2 --> HIST["Pull existing history — best-effort"]

    FETCH & SYNC1 --> DRIFT
    DRIFT["monitoring.drift<br/>NDJSON → frame → Pandera-validated<br/>→ Evidently, pinned Wasserstein / Jensen-Shannon @ 0.1"]
    DRIFT --> DJ["drift.json — the data contract<br/>per-column {method, score, threshold, drifted}<br/>+ self-computed distributions"]
    DRIFT --> DH["drift.html — functional artifact"]
    DRIFT --> DHIST["drift_history.json — trend, idempotent per day"]

    SYNC2 --> FB
    FB["monitoring.feedback<br/>proxy accuracy: overall · per-class · by-source<br/>empty window is normal → n=0, never raises"]
    FB --> FJ["feedback.json"]
    FB --> FHIST["feedback_history.json"]

    DJ & FJ --> ALERT{"monitoring.alert"}
    ALERT -->|"mean confidence < 0.55<br/>OR proxy accuracy < 0.5 with ≥10 verdicts"| ISSUE["Open or comment on the<br/>drift-alert GitHub issue — idempotent"]
    ALERT -->|"otherwise"| QUIET["no alert"]

    DJ & DH & DHIST & FJ & FHIST --> PUB["Publish to s3://data/monitoring/"]
    PUB --> EPTRIG["workflow_run → Evidence hub rebuild"]
```

**The key design call:** it does *not* alert on the ever-present OOD drift. Real doodles are
out-of-distribution against QuickDraw's bitmaps by definition — that fires every week and means
nothing. It alerts only when the model is doing *badly* on real drawings: a confidence collapse or
a proxy-accuracy floor breach. The OOD drift is still measured and published; it's just not
paged on.

---

## 5. The retrain flywheel

The loop that closes the system — a visitor's correction becomes training data.

```mermaid
flowchart LR
    DRAW["visitor draws"] --> PRED["prediction"] --> GRADE{"👍 / 👎"}
    GRADE -->|"👍"| CAP1["capture, label = the guess"]
    GRADE -->|"👎 + class picker"| CAP2["capture, label = the correction<br/>the model's actual errors — the valuable half"]
    CAP1 & CAP2 --> S3C[("captures/dt=…")]

    S3C -->|"opt-in: dispatch with include_captures=true"| SEL
    SEL["select_captures — quality bar<br/>👍 kept only if conf ≥ 0.7 · all 👎-with-label kept · per-class cap 500"]
    SEL --> RAST["rasterize_captures<br/>via the shared rasterize_strokes → no skew"]
    RAST --> FOLD["augment_npz<br/>extends the TRAIN split only"]
    FOLD --> HONEST["val/test stay pristine → honest eval"]
    HONEST --> RETRAIN["train → evaluate → export → reference"]
    RETRAIN --> GATE2["the same quality gate decides"]
    GATE2 --> DEPLOY2["ship or block"]
    DEPLOY2 --> DRAW
```

Demonstrated end-to-end on 2026-07-24 (run 30061303377): 4 captures folded → v14 registered at
test 0.9163 → gate PASS within ε → deployed. Four captures against 120k is negligible by design;
what the run proves is the *mechanism*, not a metric gain.

---

## 6. Ephemeral Kubernetes plane — `eks-demo.yml`

Monthly (1st, 06:00 UTC) or dispatch, behind an `eks-demo` environment approval gate. The cluster
exists for the length of the run and is destroyed unconditionally.

```mermaid
flowchart TB
    TRIG["monthly cron / dispatch<br/>environment approval · concurrency: eks-cluster"] --> APPLY
    APPLY["terraform apply — infra/ephemeral<br/>VPC 2 AZ single NAT + EKS 1.33<br/>addons: coredns, kube-proxy, vpc-cni before_compute<br/>node group: 3× t4g.small Graviton on-demand"] --> KUBE
    KUBE["Configure kubectl — if: always()<br/>so a failed apply still leaves a diagnosable run"] --> DIGEST
    DIGEST["Resolve the digest the Lambda tier serves<br/>lambda get-function"] --> PSTACK
    PSTACK["Install kube-prometheus-stack FIRST<br/>so the ServiceMonitor CRD exists<br/>+ provision the API dashboard via labeled ConfigMap"] --> HELMD
    HELMD["helm upgrade --install quickdraw-api<br/>BY DIGEST — a mutable tag can't sneak in<br/>ClusterIP, no public LB"] --> SMOKE2
    SMOKE2["Smoke tests via kubectl port-forward"] --> K6R
    K6R["k6 — 20 VU / 3 min → HTML report"] --> CAPM
    CAPM["Capture monitoring evidence — best-effort<br/>api-metrics.json via Prometheus query_range<br/>+ Grafana PNG"] --> CAPC
    CAPC["Capture cluster evidence<br/>get all -o wide · describe · top"] --> ART["Upload eks-demo-evidence artifact"]
    ART --> DESTROY["terraform destroy — if: always()<br/>runs on success, failure, or cancel"]

    FAIL["eks-failsafe.yml — 1st, 09:00 UTC, ~3h later<br/>also the break-glass dispatch"] --> FD["unconditional terraform destroy<br/>continue-on-error"]
    FD --> SWEEP["scripts/eks_sweep.py<br/>tier=ephemeral ONLY — pinned by test<br/>EKS → LBs → instances → NAT → EIPs → subnets → SGs → VPC"]
    SWEEP --> RED["exits nonzero on any leak → the run turns red"]
```

Three teardown layers, because a leaked cluster + NAT is ~$8–10/day: the `if: always()` destroy,
the scheduled failsafe sweep, and the same failsafe as a manual break-glass button. The two
Terraform roots are split precisely so this plane *physically cannot* touch state, data, or the
live API.

---

## 7. Evidence plane — `evidence-pages.yml`

Every claim in the portfolio resolves to a public artifact. The hub is rendered from the **MLflow
registry**, not from a checked-in metrics snapshot, so it can't drift from what actually shipped.

```mermaid
flowchart LR
    T1["workflow_run: Train & Deploy"] --> BUILD2
    T2["workflow_run: Drift report"] --> BUILD2
    T3["push touching evidence/ or reports/eval/"] --> BUILD2
    T4["dispatch"] --> BUILD2

    BUILD2["OIDC → pull mlflow.db<br/>+ confusion matrix — best-effort"] --> RENDER
    RENDER["quickdraw.evidence.export"] --> IDX["index.html"]
    RENDER --> EJ["evidence.json — the data contract"]
    RENDER --> CSS["style.css — deliberately throwaway"]
    RENDER --> CM["confusion_matrix.png"]
    RENDER --> MC["MODEL_CARD.md rendered as a section<br/>+ copied raw for the portfolio to reuse"]

    BUILD2 --> MPULL["Pull monitoring/*.json into _site/"]
    MPULL --> MJ["drift.json · drift_history.json<br/>feedback.json · feedback_history.json"]

    IDX & EJ & CSS & CM & MC & MJ --> DEPLOY3["upload-pages-artifact → deploy-pages"]
    DEPLOY3 --> LIVE["monishkamwal.github.io/mlops/"]
    LIVE --> PORT["portfolio site consumes the JSON<br/>and renders its own styled components"]
```

**The data-contract rule:** every public-facing output ships as JSON that the portfolio styles
itself — `evidence.json`, `drift.json`, `feedback.json`, `api-metrics.json`, and the two history
files. Generated HTML and Grafana PNGs are demoted to functional/dev artifacts. The site's look
belongs to the site.

---

## 8. Storage map

| Bucket | Prefix | Written by | Read by |
|---|---|---|---|
| `mlops-quickdraw-logs-ab1b` | `predictions/dt=…/` | serving, per request | drift report — 30-day window |
| | `feedback/dt=…/` | serving, on 👍/👎 | proxy accuracy — 30-day window |
| | `captures/dt=…/` | serving, on a labeled verdict | retrain path — 60-day window |
| `mlops-quickdraw-data-ab1b` | `dvc/` | `dvc push` in train-deploy | `dvc pull` |
| | `mlflow/mlflow.db` | `mlflow_sync.sh push` | gate, evidence hub |
| | `mlflow/artifacts/…` | MLflow natively — absolute `s3://` URIs, portable across machines | registry |
| | `monitoring/reference.csv` | train-deploy, stable key | drift report |
| | `monitoring/*.json` | drift report | evidence hub |
| `mlops-quickdraw-tfstate-k7f2` | `persistent/`, `ephemeral/` | Terraform, native S3 locking | Terraform |

The two Terraform roots use physically separate state keys, and the ephemeral role's IAM is scoped
so it can't reach the persistent tier's state.

---

## 9. Triggers

| Workflow | Trigger | Guardrails |
|---|---|---|
| `ci.yml` | every PR + push to `main` | lint · format · tests |
| `train-deploy.yml` | push to `main` touching model inputs; dispatch | `concurrency: train-deploy`, single mlflow writer; quality gate |
| `drift-report.yml` | Mon 07:00 UTC; dispatch | empty window is a valid result, never an error |
| `evidence-pages.yml` | after Train & Deploy or Drift report; evidence-source pushes; dispatch | `concurrency: evidence-pages`, no cancel |
| `eks-demo.yml` | 1st of month 06:00 UTC; dispatch | environment approval · `concurrency: eks-cluster` · `if: always()` destroy · per-job timeouts |
| `eks-failsafe.yml` | 1st of month 09:00 UTC; dispatch | no approval gate — it must run unattended; `tier=ephemeral` only |

---

## 10. Identity & permissions

No AWS keys exist anywhere. GitHub Actions mints an OIDC token and assumes one of two roles:

- **`gha-app`** — the application lifecycle: S3 on both buckets, ECR push, `lambda:UpdateFunctionCode`
  on `quickdraw-*`. Used by train-deploy, drift-report, evidence-pages.
- **`gha-eks`** — the ephemeral lifecycle only: region-scoped `ec2`/`eks`/`elb`/`autoscaling`/`logs`,
  IAM bounded to `role/quickdraw-ephemeral*` so "can create IAM roles" can't escalate to admin, plus
  the ephemeral state prefix.

The Lambda execution role can only `PutObject` to `predictions/*`, `feedback/*`, and `captures/*` —
append-only, no read, no delete.

---

## 11. The portfolio cut

This document is the deep version. The site carries a trimmed one; the trim, in priority order:

1. Keep **§1** as the hero diagram — collapse the four planes to four boxes and drop the S3 prefix
   detail from the node labels.
2. Keep the **flywheel (§5)** — it's the most distinctive part of the story and reads in one glance.
3. Keep the **gate (§2.3)** as a small inset; the ship-vs-re-crown split is the sharpest single idea
   in the project.
4. Cut §8–10 entirely — storage maps and IAM scoping are README material, not site material.
5. Cut the DVC DAG unless the site has a "reproducibility" section to hang it on.
6. §6 collapses well to three nodes: apply → exercise → destroy, with the failsafe as a footnote.
