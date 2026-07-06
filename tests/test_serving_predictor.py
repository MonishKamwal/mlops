"""Predictor tests — the ONNX file is the single source of truth for the labels."""

import hashlib
import math
from pathlib import Path

import numpy as np
import pytest

from quickdraw.serving.predictor import Predictor, softmax


@pytest.fixture(scope="module")
def predictor(onnx_model_path: Path) -> Predictor:
    return Predictor(onnx_model_path)


def test_metadata_comes_from_the_model_file(
    predictor: Predictor, serving_classes: list[str], onnx_model_path: Path
) -> None:
    assert predictor.classes == serving_classes
    assert predictor.val_accuracy == 0.5
    assert predictor.model_sha256 == hashlib.sha256(onnx_model_path.read_bytes()).hexdigest()


def test_predict_returns_full_sorted_distribution(
    predictor: Predictor, serving_classes: list[str]
) -> None:
    rng = np.random.default_rng(0)
    model_input = rng.random((1, 28, 28), dtype=np.float32)
    ranked = predictor.predict(model_input)
    labels = [label for label, _ in ranked]
    probabilities = [p for _, p in ranked]
    assert sorted(labels) == sorted(serving_classes)  # every class, exactly once
    assert probabilities == sorted(probabilities, reverse=True)
    assert all(0.0 <= p <= 1.0 for p in probabilities)
    assert math.isclose(sum(probabilities), 1.0, rel_tol=1e-5)


def test_softmax_is_numerically_stable() -> None:
    # naive exp(1000) overflows to inf; the max-shifted version must not
    result = softmax(np.array([1000.0, 1000.0]))
    np.testing.assert_allclose(result, [0.5, 0.5])
    assert np.isfinite(softmax(np.array([-1000.0, 0.0, 1000.0]))).all()
