"""Tests for the CNN architecture and checkpoint round-tripping."""

from pathlib import Path

import numpy as np
import torch

from quickdraw.data.preprocess import bitmap_to_model_input
from quickdraw.training.model import QuickDrawCNN, load_checkpoint, save_checkpoint


def test_forward_shape() -> None:
    model = QuickDrawCNN(num_classes=15)
    logits = model(torch.zeros(5, 1, 28, 28))
    assert logits.shape == (5, 15)


def test_eval_mode_is_deterministic() -> None:
    model = QuickDrawCNN(num_classes=4, dropout=0.5)
    model.eval()
    x = torch.rand(3, 1, 28, 28)
    with torch.no_grad():
        first, second = model(x), model(x)
    assert torch.equal(first, second)


def test_accepts_shared_preprocess_output() -> None:
    """The model must consume bitmap_to_model_input's output as-is — that function is
    the train/serve contract, so a dtype or shape mismatch here is a skew bug."""
    bitmaps = np.random.default_rng(0).integers(0, 256, size=(4, 28, 28), dtype=np.uint8)
    batch = torch.from_numpy(bitmap_to_model_input(bitmaps))
    model = QuickDrawCNN(num_classes=15)
    model.eval()
    with torch.no_grad():
        assert model(batch).shape == (4, 15)


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    classes = ["cat", "dog", "fish"]
    model = QuickDrawCNN(num_classes=3, dropout=0.2)
    model.eval()
    path = tmp_path / "model.pt"
    save_checkpoint(
        path, model.state_dict(), classes=classes, dropout=0.2, val_accuracy=0.91, epoch=4
    )
    loaded, checkpoint = load_checkpoint(path)
    assert checkpoint["classes"] == classes
    assert checkpoint["val_accuracy"] == 0.91
    assert checkpoint["epoch"] == 4
    x = torch.rand(2, 1, 28, 28)
    with torch.no_grad():
        assert torch.equal(model(x), loaded(x))
