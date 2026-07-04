"""The QuickDraw classifier: a deliberately small CNN.

The model is not the point of this project — the platform around it is (PLAN.md §0).
Two conv blocks and a small fully connected head are enough for the ≥ 88% val-accuracy
target on 15 visually distinct classes, train in minutes on a laptop CPU, and export to
an ONNX file small enough (~1.6 MB) that Lambda cold starts stay cheap.

Input contract: ``(N, 1, 28, 28)`` float32 in [0, 1] — exactly what
:func:`quickdraw.data.preprocess.bitmap_to_model_input` produces. Output: ``(N,
num_classes)`` raw logits; softmax is the consumer's job (loss functions and the
serving layer each apply their own).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import torch
from torch import nn


class QuickDrawCNN(nn.Module):
    """Two conv blocks (28→14→7 spatially) into a dropout-regularized FC head."""

    def __init__(self, num_classes: int, *, dropout: float = 0.3) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),  # 28 -> 14
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),  # 14 -> 7
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(dropout),
            nn.Linear(64 * 7 * 7, 128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.features(x))


def save_checkpoint(
    path: Path,
    state_dict: dict[str, torch.Tensor],
    *,
    classes: Sequence[str],
    dropout: float,
    val_accuracy: float,
    epoch: int,
) -> None:
    """Write a self-describing checkpoint: weights plus everything needed to rebuild.

    ``classes`` rides along because the label mapping is part of the model, not of the
    code — a checkpoint that can't say what its output indices mean is a skew bug
    waiting to happen. Only plain types and tensors go in, so loading never needs
    ``weights_only=False`` (i.e. never unpickles arbitrary objects).
    """
    torch.save(
        {
            "state_dict": state_dict,
            "classes": list(classes),
            "dropout": float(dropout),
            "val_accuracy": float(val_accuracy),
            "epoch": int(epoch),
        },
        path,
    )


def load_checkpoint(path: Path) -> tuple[QuickDrawCNN, dict[str, Any]]:
    """Rebuild the model from a checkpoint; returns it in eval mode with its metadata."""
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    model = QuickDrawCNN(len(checkpoint["classes"]), dropout=checkpoint["dropout"])
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model, checkpoint
