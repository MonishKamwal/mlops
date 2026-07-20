"""Registry semantics: challenger on every run, champion bootstrap, metric handoff."""

from pathlib import Path

import mlflow
import pytest
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from quickdraw.training import registry


@pytest.fixture()
def tracking_uri(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'mlflow.db'}"


def start_run(tracking_uri: str) -> str:
    registry.ensure_experiment(tracking_uri)
    with mlflow.start_run() as run:
        mlflow.log_metric("best_val_accuracy", 0.9)
    return run.info.run_id


def test_first_version_becomes_champion_and_challenger(tracking_uri):
    version = registry.register_challenger(start_run(tracking_uri), tracking_uri)
    assert version == "1"
    client = MlflowClient(tracking_uri=tracking_uri)
    champion = client.get_model_version_by_alias(registry.MODEL_NAME, registry.CHAMPION)
    challenger = client.get_model_version_by_alias(registry.MODEL_NAME, registry.CHALLENGER)
    assert str(champion.version) == str(challenger.version) == "1"
    assert champion.source.endswith("/model.pt")


def test_second_version_challenges_without_dethroning(tracking_uri):
    registry.register_challenger(start_run(tracking_uri), tracking_uri)
    second = registry.register_challenger(start_run(tracking_uri), tracking_uri)
    assert second == "2"
    client = MlflowClient(tracking_uri=tracking_uri)
    champion = client.get_model_version_by_alias(registry.MODEL_NAME, registry.CHAMPION)
    challenger = client.get_model_version_by_alias(registry.MODEL_NAME, registry.CHALLENGER)
    assert str(champion.version) == "1"
    assert str(challenger.version) == "2"


def test_test_metrics_land_on_the_challenger_run(tracking_uri):
    run_id = start_run(tracking_uri)
    registry.register_challenger(run_id, tracking_uri)
    logged_to = registry.log_test_metrics_to_challenger(
        {"test_accuracy": 0.91, "macro_f1": 0.9, "per_class": {}}, tracking_uri
    )
    assert logged_to == run_id
    run = MlflowClient(tracking_uri=tracking_uri).get_run(run_id)
    assert run.data.metrics["test_accuracy"] == pytest.approx(0.91)
    assert run.data.metrics["macro_f1"] == pytest.approx(0.9)


def test_metrics_without_challenger_fail_loudly(tracking_uri):
    with pytest.raises(MlflowException):
        registry.log_test_metrics_to_challenger(
            {"test_accuracy": 0.9, "macro_f1": 0.9}, tracking_uri
        )


def test_alias_test_accuracy_reads_the_metric(tracking_uri):
    run_id = start_run(tracking_uri)
    registry.register_challenger(run_id, tracking_uri)
    registry.log_test_metrics_to_challenger({"test_accuracy": 0.88, "macro_f1": 0.87}, tracking_uri)
    version, accuracy = registry.alias_test_accuracy(registry.CHALLENGER, tracking_uri)
    assert version == "1"
    assert accuracy == pytest.approx(0.88)


def test_alias_test_accuracy_without_metric_fails_loudly(tracking_uri):
    registry.register_challenger(start_run(tracking_uri), tracking_uri)  # no test metrics logged
    with pytest.raises(RuntimeError, match="test_accuracy"):
        registry.alias_test_accuracy(registry.CHALLENGER, tracking_uri)


def test_promote_to_champion_moves_the_alias(tracking_uri):
    registry.register_challenger(start_run(tracking_uri), tracking_uri)  # v1 = champion
    second = registry.register_challenger(start_run(tracking_uri), tracking_uri)  # v2 = challenger
    registry.promote_to_champion(second, tracking_uri)
    champion = MlflowClient(tracking_uri=tracking_uri).get_model_version_by_alias(
        registry.MODEL_NAME, registry.CHAMPION
    )
    assert str(champion.version) == "2"


def test_s3_artifact_root_when_bucket_configured(tracking_uri, monkeypatch):
    monkeypatch.setenv(registry.STATE_BUCKET_ENV, "some-bucket")
    registry.ensure_experiment(tracking_uri)
    experiment = mlflow.get_experiment_by_name(registry.MLFLOW_EXPERIMENT)
    assert experiment.artifact_location == "s3://some-bucket/mlflow/artifacts"


def test_local_rooted_experiment_refuses_shared_state(tracking_uri, monkeypatch):
    registry.ensure_experiment(tracking_uri)  # created with a local artifact root
    monkeypatch.setenv(registry.STATE_BUCKET_ENV, "some-bucket")
    with pytest.raises(RuntimeError, match="local artifact root"):
        registry.ensure_experiment(tracking_uri)
