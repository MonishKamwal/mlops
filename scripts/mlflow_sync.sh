#!/usr/bin/env bash
# Sync the MLflow tracking DB (runs + model registry) with S3.
#
# The DB is the ONLY synced state: artifacts write to S3 natively (train.py sets
# the experiment artifact root from MLFLOW_STATE_BUCKET), so their URIs are
# portable and this script stays one file long. Single-writer discipline is the
# CI workflow's concurrency group (Phase 2 task 5); locally, you are the writer.
set -euo pipefail

bucket="${MLFLOW_STATE_BUCKET:?MLFLOW_STATE_BUCKET must be set (e.g. mlops-quickdraw-data-ab1b)}"
remote="s3://${bucket}/mlflow/mlflow.db"

case "${1:-}" in
  pull)
    # Tolerate only "object does not exist yet" (the first-ever run); any other
    # failure (credentials, network) must stay loud.
    if aws s3api head-object --bucket "${bucket}" --key mlflow/mlflow.db >/dev/null 2>&1; then
      aws s3 cp "${remote}" mlflow.db
    else
      echo "no ${remote} yet — starting with a fresh local DB"
    fi
    ;;
  push)
    aws s3 cp mlflow.db "${remote}"
    ;;
  *)
    echo "usage: $(basename "$0") pull|push" >&2
    exit 2
    ;;
esac
