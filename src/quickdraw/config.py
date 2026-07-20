"""Typed access to ``params.yaml`` — the single source of truth for pipeline parameters."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class DataParams:
    """The ``data:`` section of params.yaml.

    Class order matters: a class's position in ``classes`` IS its label index, in
    training and serving alike. Reordering or inserting mid-list silently invalidates
    every trained model — append at the end and retrain instead.
    """

    classes: tuple[str, ...]
    samples_per_class: int
    seed: int
    train_fraction: float
    val_fraction: float
    test_fraction: float


@dataclass(frozen=True)
class TrainingParams:
    """The ``training:`` section of params.yaml."""

    epochs: int
    batch_size: int
    learning_rate: float
    weight_decay: float
    dropout: float
    seed: int


@dataclass(frozen=True)
class GateParams:
    """The ``gate:`` section of params.yaml — the deploy quality gate's thresholds.

    ``min_test_accuracy`` is an absolute floor; ``epsilon`` is how far below the
    champion's test accuracy a challenger may fall and still ship (seed-noise slack).
    """

    min_test_accuracy: float
    epsilon: float


def load_training_params(path: str | Path = "params.yaml") -> TrainingParams:
    """Load and validate the ``training:`` section of a params file."""
    raw = yaml.safe_load(Path(path).read_text())
    training = raw["training"]
    params = TrainingParams(
        epochs=int(training["epochs"]),
        batch_size=int(training["batch_size"]),
        learning_rate=float(training["learning_rate"]),
        weight_decay=float(training["weight_decay"]),
        dropout=float(training["dropout"]),
        seed=int(training["seed"]),
    )
    if params.epochs <= 0:
        raise ValueError("params.yaml: epochs must be positive")
    if params.batch_size <= 0:
        raise ValueError("params.yaml: batch_size must be positive")
    if params.learning_rate <= 0:
        raise ValueError("params.yaml: learning_rate must be positive")
    if params.weight_decay < 0:
        raise ValueError("params.yaml: weight_decay must be non-negative")
    if not 0 <= params.dropout < 1:
        raise ValueError("params.yaml: dropout must be in [0, 1)")
    return params


def load_gate_params(path: str | Path = "params.yaml") -> GateParams:
    """Load and validate the ``gate:`` section of a params file."""
    raw = yaml.safe_load(Path(path).read_text())
    gate = raw["gate"]
    params = GateParams(
        min_test_accuracy=float(gate["min_test_accuracy"]),
        epsilon=float(gate["epsilon"]),
    )
    if not 0 < params.min_test_accuracy <= 1:
        raise ValueError("params.yaml: gate.min_test_accuracy must be in (0, 1]")
    if params.epsilon < 0:
        raise ValueError("params.yaml: gate.epsilon must be non-negative")
    return params


def load_data_params(path: str | Path = "params.yaml") -> DataParams:
    """Load and validate the ``data:`` section of a params file."""
    raw = yaml.safe_load(Path(path).read_text())
    data = raw["data"]
    splits = data["splits"]
    params = DataParams(
        classes=tuple(data["classes"]),
        samples_per_class=int(data["samples_per_class"]),
        seed=int(data["seed"]),
        train_fraction=float(splits["train"]),
        val_fraction=float(splits["val"]),
        test_fraction=float(splits["test"]),
    )
    if not params.classes:
        raise ValueError("params.yaml: classes list is empty")
    if len(set(params.classes)) != len(params.classes):
        raise ValueError("params.yaml: duplicate class names")
    if params.samples_per_class <= 0:
        raise ValueError("params.yaml: samples_per_class must be positive")
    total = params.train_fraction + params.val_fraction + params.test_fraction
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        raise ValueError(f"params.yaml: split fractions sum to {total}, expected 1.0")
    return params
