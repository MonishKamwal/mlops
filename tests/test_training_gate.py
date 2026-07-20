"""Quality gate: floor + no-regression policy, promotion on pass, blocking on fail."""

from pathlib import Path

import mlflow
import pytest
from mlflow.tracking import MlflowClient

from quickdraw.config import GateParams
from quickdraw.training import gate, registry

PARAMS = GateParams(min_test_accuracy=0.85, epsilon=0.005)


@pytest.fixture()
def tracking_uri(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'mlflow.db'}"


def register_with_accuracy(tracking_uri: str, test_accuracy: float) -> str:
    """Register a fresh challenger version whose run carries a test_accuracy metric."""
    registry.ensure_experiment(tracking_uri)
    with mlflow.start_run() as run:
        mlflow.log_metric("best_val_accuracy", 0.9)
    version = registry.register_challenger(run.info.run_id, tracking_uri)
    registry.log_test_metrics_to_challenger(
        {"test_accuracy": test_accuracy, "macro_f1": 0.9}, tracking_uri
    )
    return version


@pytest.mark.parametrize(
    ("champion", "challenger", "passed"),
    [
        (0.90, 0.90, True),  # identical (bootstrap / byte-identical re-run)
        (0.90, 0.95, True),  # clearly better
        (0.90, 0.897, True),  # 0.3pp worse — within epsilon
        (0.90, 0.89, False),  # 1pp worse — beyond epsilon
        (0.90, 0.80, False),  # below floor (and beyond epsilon)
        (0.83, 0.84, False),  # beats a weak champion but below the 0.85 floor
    ],
)
def test_decide(champion: float, challenger: float, passed: bool) -> None:
    result, reasons = gate.decide(champion, challenger, PARAMS)
    assert result is passed
    assert bool(reasons) is not passed


def champion_version(tracking_uri: str) -> str:
    version = MlflowClient(tracking_uri=tracking_uri).get_model_version_by_alias(
        registry.MODEL_NAME, registry.CHAMPION
    )
    return str(version.version)


def test_bootstrap_run_ships_without_recrowning(tracking_uri: str) -> None:
    register_with_accuracy(tracking_uri, 0.90)  # v1 is both champion and challenger
    decision = gate.run_gate(tracking_uri, PARAMS)
    assert decision.passed
    assert not decision.promoted  # a tie with itself is not a strict improvement
    assert decision.champion_version == decision.challenger_version == "1"


def test_strict_improvement_ships_and_recrowns(tracking_uri: str) -> None:
    register_with_accuracy(tracking_uri, 0.90)  # v1 champion
    second = register_with_accuracy(tracking_uri, 0.93)  # v2 challenger, clearly better
    decision = gate.run_gate(tracking_uri, PARAMS)
    assert decision.passed and decision.promoted
    assert champion_version(tracking_uri) == second == "2"


def test_within_epsilon_ships_without_recrowning(tracking_uri: str) -> None:
    register_with_accuracy(tracking_uri, 0.90)  # v1 champion — the high-water mark
    register_with_accuracy(tracking_uri, 0.897)  # v2, 0.3pp worse: passes but no re-crown
    decision = gate.run_gate(tracking_uri, PARAMS)
    assert decision.passed  # cleared to deploy
    assert not decision.promoted  # champion is the best-ever, not the last shipped
    assert champion_version(tracking_uri) == "1"  # bar does not ratchet down


def test_fail_leaves_champion_untouched(tracking_uri: str) -> None:
    register_with_accuracy(tracking_uri, 0.90)  # v1 champion
    register_with_accuracy(tracking_uri, 0.80)  # v2 below floor + a regression
    decision = gate.run_gate(tracking_uri, PARAMS)
    assert not decision.passed
    assert not decision.promoted
    assert decision.reasons
    assert champion_version(tracking_uri) == "1"  # the bad model did not dethrone it


def test_main_fails_and_writes_step_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    register_with_accuracy(tracking_uri, 0.90)
    register_with_accuracy(tracking_uri, 0.80)  # challenger will be blocked
    params_path = tmp_path / "params.yaml"
    params_path.write_text("gate:\n  min_test_accuracy: 0.85\n  epsilon: 0.005\n")
    summary_path = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))

    code = gate.main(["--params", str(params_path), "--tracking-uri", tracking_uri])

    assert code == 1
    assert "FAIL" in capsys.readouterr().out
    assert "FAIL" in summary_path.read_text()  # metric-diff summary reached the job annotation


def test_main_passes_with_exit_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    register_with_accuracy(tracking_uri, 0.90)  # bootstrap champion passes trivially
    params_path = tmp_path / "params.yaml"
    params_path.write_text("gate:\n  min_test_accuracy: 0.85\n  epsilon: 0.005\n")

    code = gate.main(["--params", str(params_path), "--tracking-uri", tracking_uri])

    assert code == 0
    assert "PASS" in capsys.readouterr().out
