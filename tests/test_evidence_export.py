"""Evidence hub: registry-as-source-of-truth gather + self-contained static render."""

import json
from pathlib import Path

import mlflow
import pytest
from mlflow.tracking import MlflowClient

from quickdraw.config import GateParams
from quickdraw.evidence import export
from quickdraw.training import registry

GATE = GateParams(min_test_accuracy=0.85, epsilon=0.005)


@pytest.fixture()
def tracking_uri(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'mlflow.db'}"


def _train_run(tracking_uri: str, *, epochs: int, lr: float, val: float, test: float) -> str:
    """Mimic train + evaluate: a run with params/metrics, registered + given test metrics."""
    registry.ensure_experiment(tracking_uri)
    with mlflow.start_run() as run:
        mlflow.log_params({"epochs": epochs, "learning_rate": lr})
        mlflow.log_metric("best_val_accuracy", val)
    version = registry.register_challenger(run.info.run_id, tracking_uri)
    registry.log_test_metrics_to_challenger(
        {"test_accuracy": test, "macro_f1": test - 0.01}, tracking_uri
    )
    return version


def test_gather_runs_joins_registry_and_orders_newest_first(tracking_uri: str) -> None:
    _train_run(tracking_uri, epochs=8, lr=0.001, val=0.90, test=0.90)  # v1: bootstrap
    v2 = _train_run(tracking_uri, epochs=8, lr=0.001, val=0.92, test=0.93)  # v2: challenger
    registry.promote_to_champion(v2, tracking_uri)  # champion now points at v2

    rows = export.gather_runs(MlflowClient(tracking_uri=tracking_uri))

    assert [r.version for r in rows] == ["2", "1"]  # newest first
    champ = rows[0]
    assert champ.alias is not None and "champion" in champ.alias
    assert champ.test_accuracy == pytest.approx(0.93)
    assert champ.epochs == 8
    assert champ.learning_rate == pytest.approx(0.001)
    assert rows[1].alias is None  # v1 lost both aliases


def test_bare_run_without_registration_has_no_version(tracking_uri: str) -> None:
    registry.ensure_experiment(tracking_uri)
    with mlflow.start_run():
        mlflow.log_metric("best_val_accuracy", 0.5)

    (row,) = export.gather_runs(MlflowClient(tracking_uri=tracking_uri))

    assert row.version is None
    assert row.alias is None
    assert row.test_accuracy is None  # evaluate never logged one


def test_build_context_selects_champion_and_flattens_per_class() -> None:
    runs = [
        export.RunRow("abc123ef", "2026-07-20 10:00", 8, 0.001, 0.92, 0.93, 0.92, "2", "champion"),
        export.RunRow("def456ab", "2026-07-18 09:00", 8, 0.001, 0.90, 0.90, 0.90, "1", None),
    ]
    eval_metrics = {
        "per_class": {"cat": {"precision": 0.8, "recall": 0.8, "f1": 0.8, "support": 1000}}
    }

    ctx = export.build_context(runs, eval_metrics, GATE)

    assert ctx["champion"]["version"] == "2"
    assert ctx["per_class"] == [
        {"cls": "cat", "precision": 0.8, "recall": 0.8, "f1": 0.8, "support": 1000}
    ]
    assert ctx["gate"] == {"min_test_accuracy": 0.85, "epsilon": 0.005}
    assert ctx["f1_chart"] and ctx["accuracy_chart"]  # charts present when data exists


def test_build_context_without_eval_metrics_is_graceful() -> None:
    runs = [
        export.RunRow("abc123ef", "2026-07-20 10:00", 8, 0.001, 0.92, 0.93, 0.92, "2", "champion")
    ]

    ctx = export.build_context(runs, None, GATE)

    assert ctx["per_class"] == []
    assert ctx["f1_chart"] is None  # no per-class data → no chart, no crash
    assert ctx["champion"]["version"] == "2"


def test_build_data_is_json_serializable_and_presentation_free() -> None:
    runs = [
        export.RunRow("abc123ef", "2026-07-20 10:00", 8, 0.001, 0.92, 0.93, 0.92, "2", "champion"),
        export.RunRow("def456ab", "2026-07-18 09:00", 8, 0.001, 0.90, 0.90, 0.90, "1", None),
    ]

    data = export.build_data(runs, None, GATE)
    round_tripped = json.loads(json.dumps(data))  # the contract must survive JSON

    assert round_tripped["champion"]["version"] == "2"
    assert [r["version"] for r in round_tripped["runs"]] == ["2", "1"]
    assert not export._PRESENTATION_KEYS & data.keys()  # no HTML/chart markup leaks in


def test_render_writes_self_contained_site(
    tracking_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metrics = tmp_path / "metrics.json"
    metrics.write_text(
        json.dumps(
            {"per_class": {"cat": {"precision": 0.8, "recall": 0.8, "f1": 0.8, "support": 1000}}}
        )
    )
    cm = tmp_path / "cm.png"
    cm.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(export, "EVAL_METRICS_PATH", metrics)
    monkeypatch.setattr(export, "CONFUSION_MATRIX_PATH", cm)
    monkeypatch.setattr(export, "MODEL_CARD_PATH", tmp_path / "absent.md")  # card-free path

    _train_run(tracking_uri, epochs=8, lr=0.001, val=0.92, test=0.93)

    out = tmp_path / "site"
    index = export.render(out, tracking_uri=tracking_uri, gate_params=GATE)

    assert index == out / "index.html"
    html = index.read_text()
    assert "QuickDraw" in html
    assert "champion" in html
    assert "confusion_matrix.png" in html  # image referenced
    assert export.BLOCKED_GATE_RUN_URL in html
    assert (out / "style.css").exists()
    assert (out / "confusion_matrix.png").exists()

    # The portfolio site consumes this, not the HTML — so it must be present and sound.
    data = json.loads((out / "evidence.json").read_text())
    assert data["champion"]["version"] == "1"
    assert data["gate"]["min_test_accuracy"] == 0.85


def test_load_model_card_html_renders_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = tmp_path / "MODEL_CARD.md"
    card.write_text("# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    monkeypatch.setattr(export, "MODEL_CARD_PATH", card)

    html = export.load_model_card_html()

    assert "<h1>Title</h1>" in html
    assert "<table>" in html  # the `tables` extension is active


def test_load_model_card_html_absent_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "MODEL_CARD_PATH", tmp_path / "nope.md")
    assert export.load_model_card_html() is None


def test_render_includes_model_card_and_copies_source(
    tracking_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = tmp_path / "MODEL_CARD.md"
    card.write_text("# Model card — QuickDraw\n\nSome **prose**.\n")
    monkeypatch.setattr(export, "MODEL_CARD_PATH", card)
    monkeypatch.setattr(export, "EVAL_METRICS_PATH", tmp_path / "no-metrics.json")
    monkeypatch.setattr(export, "CONFUSION_MATRIX_PATH", tmp_path / "no-cm.png")

    _train_run(tracking_uri, epochs=8, lr=0.001, val=0.92, test=0.93)

    out = tmp_path / "site"
    html = export.render(out, tracking_uri=tracking_uri, gate_params=GATE).read_text()

    assert 'class="model-card"' in html
    assert "<strong>prose</strong>" in html  # markdown rendered into the page
    assert (out / "MODEL_CARD.md").exists()  # raw source shipped for the portfolio
    # the rendered card is presentation, never part of the JSON data contract
    assert "model_card_html" not in json.loads((out / "evidence.json").read_text())
