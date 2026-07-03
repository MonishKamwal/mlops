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
