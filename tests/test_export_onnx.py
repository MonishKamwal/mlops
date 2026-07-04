"""ONNX export tests — including the PyTorch-vs-ONNX parity check (Phase 1 DoD)."""

import json
from pathlib import Path

import onnxruntime
import pytest
import torch

from quickdraw.training.export_onnx import PARITY_TOLERANCE, export_model, max_abs_difference
from quickdraw.training.model import QuickDrawCNN, save_checkpoint

CLASSES = ["airplane", "apple", "banana", "bicycle", "bird", "car", "cat"]


@pytest.fixture()
def checkpoint_path(tmp_path: Path) -> Path:
    """A checkpoint with random (untrained) weights — parity is about the export,
    not the training, so any weights will do."""
    torch.manual_seed(0)
    model = QuickDrawCNN(num_classes=len(CLASSES), dropout=0.3)
    path = tmp_path / "model.pt"
    save_checkpoint(
        path, model.state_dict(), classes=CLASSES, dropout=0.3, val_accuracy=0.5, epoch=1
    )
    return path


def test_onnx_output_matches_pytorch(checkpoint_path: Path, tmp_path: Path) -> None:
    onnx_path = export_model(checkpoint_path, tmp_path / "model.onnx")
    # batch of 64 also exercises the dynamic batch axis (export traced batch size 1)
    difference = max_abs_difference(checkpoint_path, onnx_path, batch_size=64)
    assert difference <= PARITY_TOLERANCE


def test_classes_embedded_in_onnx_metadata(checkpoint_path: Path, tmp_path: Path) -> None:
    onnx_path = export_model(checkpoint_path, tmp_path / "model.onnx")
    session = onnxruntime.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    metadata = session.get_modelmeta().custom_metadata_map
    assert json.loads(metadata["classes"]) == CLASSES
    assert float(metadata["val_accuracy"]) == 0.5
