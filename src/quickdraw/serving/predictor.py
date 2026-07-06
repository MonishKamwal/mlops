"""ONNX inference for the serving API.

The exported model file is self-describing: :mod:`quickdraw.training.export_onnx`
embeds the class list and training val_accuracy in its metadata. The predictor reads
the label mapping from the model itself — never from params.yaml — so the serving
image cannot pair weights with a label order from a different code version. For the
same reason nothing here imports the training stack: torch stays out of the image
(PLAN.md §2; a test enforces it).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import onnxruntime


def softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax over the last axis (the model outputs raw logits)."""
    shifted = logits - logits.max(axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=-1, keepdims=True)


class Predictor:
    """One loaded ONNX session; created once at app startup, shared by all requests."""

    def __init__(self, model_path: Path) -> None:
        self.model_path = Path(model_path)
        # identifies exactly which model artifact is deployed (surfaced by /model-info,
        # logged with every prediction from task 6 on)
        self.model_sha256 = hashlib.sha256(self.model_path.read_bytes()).hexdigest()
        self.session = onnxruntime.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )
        metadata = self.session.get_modelmeta().custom_metadata_map
        self.classes: list[str] = json.loads(metadata["classes"])
        self.val_accuracy = float(metadata["val_accuracy"])
        # input/output names come from the graph itself — no constants shared with
        # the exporter, so serving never needs to import training code
        self._input_name = self.session.get_inputs()[0].name
        self._output_name = self.session.get_outputs()[0].name

    def predict(self, model_input: np.ndarray) -> list[tuple[str, float]]:
        """``(1, 28, 28)`` float32 model input -> ``[(class, probability), ...]``,
        every class, sorted by probability descending."""
        batch = np.expand_dims(np.asarray(model_input, dtype=np.float32), axis=0)
        (logits,) = self.session.run([self._output_name], {self._input_name: batch})
        probabilities = softmax(logits[0])
        order = np.argsort(probabilities)[::-1]
        return [(self.classes[i], float(probabilities[i])) for i in order]
