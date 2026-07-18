"""Evaluate the trained checkpoint on the held-out test split.

Writes ``reports/eval/metrics.json`` (overall accuracy, macro F1, per-class
precision/recall/F1/support) and ``reports/eval/confusion_matrix.png``. These files are
the model's report card: DVC picks them up as stage outputs in Phase 2, and the Phase 2
evidence hub publishes them.

The metrics are ~20 lines of numpy on the confusion matrix — not worth a scikit-learn
dependency in the training image.

Usage: ``uv run python -m quickdraw.training.evaluate``
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import matplotlib
import numpy as np
import torch

from quickdraw.data.preprocess import bitmap_to_model_input
from quickdraw.training.model import QuickDrawCNN, load_checkpoint
from quickdraw.training.registry import DEFAULT_TRACKING_URI, log_test_metrics_to_challenger

matplotlib.use("Agg")  # render to files; no display needed (or available in CI)
import matplotlib.pyplot as plt  # noqa: E402

EVAL_BATCH_SIZE = 1024


def predict(model: QuickDrawCNN, images: np.ndarray) -> np.ndarray:
    """Batched argmax predictions for ``(N, 1, 28, 28)`` float32 inputs, on CPU."""
    model.eval()
    predictions = []
    with torch.no_grad():
        for start in range(0, len(images), EVAL_BATCH_SIZE):
            batch = torch.from_numpy(images[start : start + EVAL_BATCH_SIZE])
            predictions.append(model(batch).argmax(dim=1).numpy())
    return np.concatenate(predictions)


def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, num_classes: int) -> np.ndarray:
    """``cm[i, j]`` = count of samples with true class i predicted as class j."""
    cm = np.zeros((num_classes, num_classes), dtype=np.int64)
    np.add.at(cm, (y_true, y_pred), 1)
    return cm


def classification_metrics(cm: np.ndarray, classes: Sequence[str]) -> dict:
    """Overall + per-class metrics from a confusion matrix.

    Zero-support or never-predicted classes get 0.0 rather than NaN, so the report
    stays valid JSON and a dead class is loudly visible instead of silently dropped.
    """
    true_positives = np.diag(cm).astype(np.float64)
    support = cm.sum(axis=1).astype(np.float64)
    predicted = cm.sum(axis=0).astype(np.float64)
    precision = np.divide(
        true_positives, predicted, out=np.zeros_like(true_positives), where=predicted > 0
    )
    recall = np.divide(
        true_positives, support, out=np.zeros_like(true_positives), where=support > 0
    )
    denominator = precision + recall
    f1 = np.divide(
        2 * precision * recall, denominator, out=np.zeros_like(denominator), where=denominator > 0
    )
    return {
        "test_accuracy": float(true_positives.sum() / cm.sum()),
        "macro_f1": float(f1.mean()),
        "per_class": {
            name: {
                "precision": round(float(precision[i]), 4),
                "recall": round(float(recall[i]), 4),
                "f1": round(float(f1[i]), 4),
                "support": int(support[i]),
            }
            for i, name in enumerate(classes)
        },
    }


def plot_confusion_matrix(cm: np.ndarray, classes: Sequence[str], path: Path) -> None:
    """Row-normalized heatmap with counts annotated — readable at 15x15."""
    normalized = cm / np.maximum(cm.sum(axis=1, keepdims=True), 1)
    fig, ax = plt.subplots(figsize=(10, 9))
    image = ax.imshow(normalized, cmap="Blues", vmin=0, vmax=1)
    fig.colorbar(image, ax=ax, label="fraction of true class")
    ax.set_xticks(range(len(classes)), classes, rotation=45, ha="right")
    ax.set_yticks(range(len(classes)), classes)
    ax.set_xlabel("predicted")
    ax.set_ylabel("true")
    for i in range(len(classes)):
        for j in range(len(classes)):
            if cm[i, j]:
                color = "white" if normalized[i, j] > 0.5 else "black"
                ax.text(j, i, str(cm[i, j]), ha="center", va="center", fontsize=6, color=color)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def evaluate(model_path: Path, data_path: Path, out_dir: Path) -> dict:
    """Score the checkpoint on the test split; write metrics.json + confusion matrix."""
    model, checkpoint = load_checkpoint(model_path)
    classes = checkpoint["classes"]
    with np.load(data_path) as data:
        images = bitmap_to_model_input(data["x_test"])
        y_true = data["y_test"]
    y_pred = predict(model, images)
    cm = confusion_matrix(y_true, y_pred, len(classes))
    metrics = classification_metrics(cm, classes)
    metrics["val_accuracy"] = round(float(checkpoint["val_accuracy"]), 4)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    plot_confusion_matrix(cm, classes, out_dir / "confusion_matrix.png")
    return metrics


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate the trained QuickDraw model.")
    parser.add_argument("--model", type=Path, default=Path("models/model.pt"))
    parser.add_argument("--data", type=Path, default=Path("data/processed/quickdraw.npz"))
    parser.add_argument("--out-dir", type=Path, default=Path("reports/eval"))
    parser.add_argument("--tracking-uri", default=DEFAULT_TRACKING_URI)
    args = parser.parse_args(argv)
    metrics = evaluate(args.model, args.data, args.out_dir)
    worst = sorted(metrics["per_class"].items(), key=lambda item: item[1]["f1"])[:3]
    print(f"test_accuracy={metrics['test_accuracy']:.4f} macro_f1={metrics['macro_f1']:.4f}")
    print("hardest classes: " + ", ".join(f"{name} (f1={m['f1']})" for name, m in worst))
    print(f"wrote {args.out_dir / 'metrics.json'} and {args.out_dir / 'confusion_matrix.png'}")
    run_id = log_test_metrics_to_challenger(metrics, args.tracking_uri)
    print(f"logged test metrics to challenger run {run_id}")


if __name__ == "__main__":
    main()
