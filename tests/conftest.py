"""Shared fixtures: one real exported ONNX model for the serving tests.

The fixture goes through the actual checkpoint-save -> export_model path (untrained
weights — serving behavior, not accuracy, is under test), so the serving tests
exercise the same metadata contract the real artifact carries. Session-scoped: the
dynamo exporter takes a few seconds and every serving test can share one file.
"""

from pathlib import Path
from typing import Any

import pytest

SERVING_TEST_CLASSES = ["airplane", "apple", "banana", "bicycle", "bird", "car", "cat"]


class FakeS3Client:
    """Stands in for boto3's S3 client in prediction-log tests (PutObjectClient shape)."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> Any:
        if self.fail:
            raise RuntimeError("s3 is down")
        self.puts.append(kwargs)


@pytest.fixture(scope="session")
def serving_classes() -> list[str]:
    return SERVING_TEST_CLASSES


@pytest.fixture(scope="session")
def onnx_model_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    import torch

    from quickdraw.training.export_onnx import export_model
    from quickdraw.training.model import QuickDrawCNN, save_checkpoint

    torch.manual_seed(0)
    model = QuickDrawCNN(num_classes=len(SERVING_TEST_CLASSES), dropout=0.3)
    directory = tmp_path_factory.mktemp("serving-model")
    checkpoint_path = directory / "model.pt"
    save_checkpoint(
        checkpoint_path,
        model.state_dict(),
        classes=SERVING_TEST_CLASSES,
        dropout=0.3,
        val_accuracy=0.5,
        epoch=1,
    )
    return export_model(checkpoint_path, directory / "model.onnx")
