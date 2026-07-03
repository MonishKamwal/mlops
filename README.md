# QuickDraw MLOps Platform

[![CI](https://github.com/MonishKamwal/mlops/actions/workflows/ci.yml/badge.svg)](https://github.com/MonishKamwal/mlops/actions/workflows/ci.yml)

End-to-end MLOps platform for **live sketch recognition** on the
[Google QuickDraw](https://quickdraw.withgoogle.com/data) dataset. Visitors draw on a canvas at
[monishkamwal.github.io](https://monishkamwal.github.io) and a deployed model classifies the
doodle in real time.

The model is deliberately small (a CNN over 28×28 bitmaps, ~15 classes); the point is the
**platform around it** — reproducible pipelines, experiment tracking, automated quality gates,
infrastructure as code, and monitoring against real visitor traffic:

- **Pipeline & data:** DVC (S3 remote) runs `download → validate → preprocess → train →
  evaluate → export`, with Pandera schema validation and PyTorch training exported to ONNX.
- **Tracking & registry:** MLflow (SQLite state synced to S3) with champion/challenger aliases
  and a metric-regression gate that can block a deploy.
- **Serving:** one FastAPI + onnxruntime container image serves both tiers — AWS Lambda
  (Function URL, always-on, ~$0) and an **ephemeral EKS cluster** provisioned by Terraform and
  Helm weekly, load-tested with k6, then destroyed.
- **CI/CD:** GitHub Actions with OIDC to AWS (no stored keys); merge to `main` → train → gate →
  build → deploy, no manual steps.
- **Monitoring:** prediction logs (JSONL to S3) feed weekly Evidently drift reports comparing
  real visitor drawings against the training distribution.
- **Evidence hub:** every claim above is backed by a public artifact (MLflow export, eval
  reports, k6 results, drift dashboards) published via GitHub Pages.

The full architecture, decision log, and phased roadmap live in [PLAN.md](PLAN.md).

## Status

**Phase 0 — foundations.** Tooling, CI, and AWS cost guardrails are being put in place before
any platform code. See PLAN.md §5 for the phase breakdown.

## Development

Requires [uv](https://docs.astral.sh/uv/) (manages the Python 3.12 toolchain and venv).

```sh
uv sync              # create venv + install dev dependencies
uv run pytest        # run tests
uv run ruff check .  # lint
uv run pre-commit install  # enable git hooks (once)
```

## License

[MIT](LICENSE)
