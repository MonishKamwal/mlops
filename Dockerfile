# syntax=docker/dockerfile:1
# One serving image for every tier (PLAN.md §2). Locally and on EKS it is a plain
# uvicorn HTTP server; on Lambda the Web Adapter — an extension binary copied into
# /opt/extensions, no code changes — translates Function URL invocations into the
# same HTTP requests. No Lambda-specific code path exists in the app.

FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependency layer first: source-only changes rebuild from here, not from scratch.
# --no-default-groups drops the dev + train groups — runtime deps only, no torch.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-editable

FROM python:3.12-slim-bookworm
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY models/model.onnx ./models/model.onnx
ENV PATH="/app/.venv/bin:$PATH" \
    MODEL_PATH=/app/models/model.onnx \
    PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/healthz
EXPOSE 8080
CMD ["uvicorn", "quickdraw.serving.app:app", "--host", "0.0.0.0", "--port", "8080"]
