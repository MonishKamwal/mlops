"""Tests for the evaluation metrics (pure functions) and report generation."""

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from quickdraw.training.evaluate import classification_metrics, confusion_matrix, evaluate
from quickdraw.training.model import QuickDrawCNN, save_checkpoint

CLASSES = ["cat", "dog"]


def test_confusion_matrix_counts() -> None:
    y_true = np.array([0, 0, 0, 1, 1, 1])
    y_pred = np.array([0, 0, 1, 1, 1, 1])
    cm = confusion_matrix(y_true, y_pred, num_classes=2)
    assert cm.tolist() == [[2, 1], [0, 3]]


def test_classification_metrics_by_hand() -> None:
    cm = np.array([[2, 1], [0, 3]])
    metrics = classification_metrics(cm, CLASSES)
    assert metrics["test_accuracy"] == pytest.approx(5 / 6)
    cat, dog = metrics["per_class"]["cat"], metrics["per_class"]["dog"]
    assert cat["precision"] == pytest.approx(1.0)  # 2 of 2 cat predictions correct
    assert cat["recall"] == pytest.approx(2 / 3, abs=1e-4)  # 2 of 3 true cats found
    assert dog["precision"] == pytest.approx(3 / 4)
    assert dog["recall"] == pytest.approx(1.0)
    assert cat["support"] == 3 and dog["support"] == 3


def test_zero_division_yields_zero_not_nan() -> None:
    # class 1 never predicted, class 2 has no true samples
    cm = np.array([[3, 0, 1], [2, 0, 0], [0, 0, 0]])
    metrics = classification_metrics(cm, ["a", "b", "c"])
    assert metrics["per_class"]["b"]["precision"] == 0.0
    assert metrics["per_class"]["c"]["recall"] == 0.0
    assert not any(np.isnan(v["f1"]) for v in metrics["per_class"].values())


def test_evaluate_writes_report(tmp_path: Path) -> None:
    torch.manual_seed(0)
    model = QuickDrawCNN(num_classes=2, dropout=0.0)
    model_path = tmp_path / "model.pt"
    save_checkpoint(
        model_path, model.state_dict(), classes=CLASSES, dropout=0.0, val_accuracy=0.9, epoch=2
    )
    rng = np.random.default_rng(0)
    data_path = tmp_path / "quickdraw.npz"
    np.savez(
        data_path,
        classes=np.array(CLASSES),
        x_test=rng.integers(0, 256, size=(20, 28, 28)).astype(np.uint8),
        y_test=rng.integers(0, 2, size=20).astype(np.int64),
    )

    out_dir = tmp_path / "eval"
    metrics = evaluate(model_path, data_path, out_dir)

    written = json.loads((out_dir / "metrics.json").read_text())
    assert written == metrics
    assert set(written["per_class"]) == set(CLASSES)
    assert 0.0 <= written["test_accuracy"] <= 1.0
    assert written["val_accuracy"] == 0.9
    assert (out_dir / "confusion_matrix.png").stat().st_size > 0
