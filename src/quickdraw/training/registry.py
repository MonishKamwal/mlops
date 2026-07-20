"""MLflow model registry semantics: every run is a challenger; the gate crowns champions.

The registry is the pipeline's memory of "the best model validated so far"
(``champion``) vs "what was just trained" (``challenger``). Training registers each
new checkpoint as a model version and points ``challenger`` at it; evaluate logs
test metrics to the challenger's run; the Phase 2 quality gate (task 4) compares
challenger against champion, ships on pass, and moves the ``champion`` alias only on
a strict improvement — so the alias is a non-eroding quality bar, not a record of
what happens to be deployed.

The very first version also becomes champion — there is nothing to compare
against, and a registry without a champion would deadlock the gate.

State lives in the SQLite tracking DB (``mlflow.db``). When ``MLFLOW_STATE_BUCKET``
is set, artifacts write to S3 natively and the DB is synced via
``scripts/mlflow_sync.sh`` — the DB is the only synced state, because MLflow
records artifact roots as absolute URIs: ``file:///Users/...`` paths inside a
shared DB would break on every other machine, while ``s3://`` URIs are portable
by construction.
"""

from __future__ import annotations

import contextlib
import os

import mlflow
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

MODEL_NAME = "quickdraw"
CHAMPION = "champion"
CHALLENGER = "challenger"
MLFLOW_EXPERIMENT = "quickdraw"
DEFAULT_TRACKING_URI = "sqlite:///mlflow.db"
STATE_BUCKET_ENV = "MLFLOW_STATE_BUCKET"  # project convention, not an MLflow variable


def artifact_location() -> str | None:
    """S3 artifact root when the state bucket is configured, else MLflow's default."""
    bucket = os.environ.get(STATE_BUCKET_ENV)
    return f"s3://{bucket}/mlflow/artifacts" if bucket else None


def ensure_experiment(tracking_uri: str) -> None:
    """Select the experiment, creating it with the right artifact root if needed.

    Fails loudly if the state bucket is configured but the experiment carries a
    non-S3 artifact root: new runs would inherit that root, and an absolute local
    path baked into a shared DB is exactly the trap this setup exists to prevent.
    """
    mlflow.set_tracking_uri(tracking_uri)
    experiment = mlflow.get_experiment_by_name(MLFLOW_EXPERIMENT)
    if experiment is None:
        mlflow.create_experiment(MLFLOW_EXPERIMENT, artifact_location=artifact_location())
    elif artifact_location() and not experiment.artifact_location.startswith("s3://"):
        raise RuntimeError(
            f"experiment '{MLFLOW_EXPERIMENT}' has a local artifact root "
            f"({experiment.artifact_location}) but {STATE_BUCKET_ENV} is set — this DB "
            "predates shared S3 state and must not be pushed. Archive it (e.g. "
            "mlflow.local.db) and start from a fresh or pulled DB."
        )
    mlflow.set_experiment(MLFLOW_EXPERIMENT)


def register_challenger(run_id: str, tracking_uri: str) -> str:
    """Register the run's checkpoint as a new version; alias it as the challenger.

    Returns the new version number as a string (MLflow's own type for it has
    flip-flopped between str and int across releases). The first version ever
    also becomes champion — the bootstrap case.
    """
    client = MlflowClient(tracking_uri=tracking_uri)
    with contextlib.suppress(MlflowException):  # already exists — the steady state
        client.create_registered_model(MODEL_NAME)
    source = f"{client.get_run(run_id).info.artifact_uri}/model.pt"
    version = client.create_model_version(MODEL_NAME, source=source, run_id=run_id)
    client.set_registered_model_alias(MODEL_NAME, CHALLENGER, version.version)
    try:
        client.get_model_version_by_alias(MODEL_NAME, CHAMPION)
    except MlflowException:
        client.set_registered_model_alias(MODEL_NAME, CHAMPION, version.version)
    return str(version.version)


def alias_test_accuracy(alias: str, tracking_uri: str) -> tuple[str, float]:
    """Return ``(version, test_accuracy)`` for the model version behind an alias.

    Raises if the aliased version's run never logged ``test_accuracy`` — evaluate
    must run before the gate, and a silent 0.0 would let a broken pipeline promote
    a model on garbage numbers. This is the gate's read side of the registry.
    """
    client = MlflowClient(tracking_uri=tracking_uri)
    version = client.get_model_version_by_alias(MODEL_NAME, alias)
    run = client.get_run(version.run_id)
    if "test_accuracy" not in run.data.metrics:
        raise RuntimeError(
            f"{alias} (version {version.version}, run {version.run_id}) has no "
            "test_accuracy metric — run evaluate before the gate."
        )
    return str(version.version), float(run.data.metrics["test_accuracy"])


def promote_to_champion(version: str, tracking_uri: str) -> None:
    """Point the ``champion`` alias at ``version``.

    The gate calls this only when a challenger strictly beats the champion, so the
    alias tracks the best model ever validated — the quality bar — rather than
    whatever last shipped. Champion means *best*, not *live*.
    """
    MlflowClient(tracking_uri=tracking_uri).set_registered_model_alias(
        MODEL_NAME, CHAMPION, version
    )


def log_test_metrics_to_challenger(metrics: dict, tracking_uri: str) -> str:
    """Attach evaluate's test metrics to the challenger's run; returns the run id.

    The gate compares *test* accuracy, and the champion's number must live on its
    run from back when it was the challenger — so every version's run gets these.
    Raises if there is no challenger: evaluate before train is a broken pipeline,
    and silence here would surface as a mystery at gate time.
    """
    client = MlflowClient(tracking_uri=tracking_uri)
    version = client.get_model_version_by_alias(MODEL_NAME, CHALLENGER)
    client.log_metric(version.run_id, "test_accuracy", metrics["test_accuracy"])
    client.log_metric(version.run_id, "macro_f1", metrics["macro_f1"])
    return version.run_id
