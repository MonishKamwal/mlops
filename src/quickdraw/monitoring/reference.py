"""Build the Evidently drift reference (PLAN.md Phase 4, task 1).

The prediction logs are privacy-first: they store a hash of the model input, never the pixels
or strokes (``serving/prediction_log.py``). So drift is measured on the model's *output*
distribution, not its input. The reference is the deployed model's behaviour on the held-out
test split — per test example: the predicted class, the top-1 confidence, and the top1-top2
margin. The weekly drift report (task 2) compares live prediction logs against this baseline;
because real visitor doodles are out-of-distribution vs QuickDraw, the model should be
measurably *less* confident on them, and that gap is the drift we expect to surface.

Stored as CSV, not parquet: the DVC out stays deterministic and human-inspectable, and
Evidently reads it straight into a DataFrame. Regenerated as a DVC stage whenever the model
changes, so the reference always reflects the currently-deployed model.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from quickdraw.data.preprocess import bitmap_to_model_input
from quickdraw.training.model import load_checkpoint

BATCH = 512


def summarize_predictions(probs: np.ndarray, classes: Sequence[str]) -> pd.DataFrame:
    """Reduce per-example class probabilities to the drift features.

    Pure and model-free so it can be unit-tested directly: for each row, the predicted label
    (argmax), the confidence (max probability), and the margin (top1 - top2).
    """
    top1 = probs.argmax(axis=1)
    confidence = probs.max(axis=1)
    # Second-highest probability per row; the (top1 - top2) margin shrinks first when the
    # model gets uncertain, so it's an earlier drift signal than confidence alone.
    top2 = np.partition(probs, -2, axis=1)[:, -2]
    return pd.DataFrame(
        {
            "predicted_label": [classes[i] for i in top1],
            "confidence": np.round(confidence, 6),
            "margin": np.round(confidence - top2, 6),
        }
    )


def _softmax_probabilities(model: torch.nn.Module, images: np.ndarray) -> np.ndarray:
    """Batched per-example softmax probabilities for ``(N, 1, 28, 28)`` inputs, on CPU."""
    model.eval()
    out: list[np.ndarray] = []
    with torch.no_grad():
        for start in range(0, len(images), BATCH):
            batch = torch.from_numpy(images[start : start + BATCH])
            out.append(torch.softmax(model(batch), dim=1).numpy())
    return np.concatenate(out)


def build_reference(model_path: Path, data_path: Path) -> pd.DataFrame:
    """The deployed model's prediction distribution over the test split."""
    model, checkpoint = load_checkpoint(model_path)
    classes = list(checkpoint["classes"])
    with np.load(data_path) as data:
        images = bitmap_to_model_input(data["x_test"])
    return summarize_predictions(_softmax_probabilities(model, images), classes)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the drift reference dataset.")
    parser.add_argument("--model", type=Path, default=Path("models/model.pt"))
    parser.add_argument("--data", type=Path, default=Path("data/processed/quickdraw.npz"))
    parser.add_argument("--out", type=Path, default=Path("reports/monitoring/reference.csv"))
    args = parser.parse_args(argv)

    df = build_reference(args.model, args.data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(
        f"wrote {len(df)} reference rows to {args.out} "
        f"(mean confidence {df['confidence'].mean():.4f}, mean margin {df['margin'].mean():.4f})"
    )


if __name__ == "__main__":
    main()
