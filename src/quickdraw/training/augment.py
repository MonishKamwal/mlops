"""Fold captured real drawings into the training set (PLAN.md Phase 4, task 5).

Closes the flywheel: labeled captures from the canvas (``captures/dt=…/`` — see
:mod:`quickdraw.serving.capture_log`) become extra training examples. Each capture's strokes are
rasterized through the **same** ``rasterize_strokes`` the QuickDraw bitmaps and the serving path
use, so a real drawing enters training in the identical ``(28, 28)`` uint8 form — no skew.

Only the **train** split is extended; val/test stay pristine so evaluation stays honest. Quality
bar: a 👍 (``correct``) is kept only when the model was confident (it's a *reinforcing* example,
so a shaky one adds little); a 👎 correction is always kept (a labeled *error* is the valuable,
scarce signal). A per-class cap stops one popular class from swamping the mix.

This is an opt-in retrain path (a ``workflow_dispatch`` toggle), not the reproducible pipeline —
captures are live data, so a run isn't byte-reproducible by design.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from quickdraw.data.preprocess import rasterize_strokes
from quickdraw.monitoring.drift import read_ndjson_dir


def select_captures(
    records: Iterable[dict[str, Any]],
    classes: Sequence[str],
    *,
    min_confidence: float,
    per_class_cap: int,
) -> list[dict[str, Any]]:
    """Apply the quality bar + per-class cap. Pure — no rasterization, no IO."""
    kept: list[dict[str, Any]] = []
    per_class: dict[str, int] = dict.fromkeys(classes, 0)
    for rec in records:
        label = rec.get("label")
        if label not in per_class:  # unknown/missing label — skip defensively
            continue
        correct = bool(rec.get("correct"))
        # 👍 kept only if confident (reinforcing); 👎 always kept (a labeled error).
        if correct and float(rec.get("confidence", 0.0)) < min_confidence:
            continue
        if per_class[label] >= per_class_cap:
            continue
        if not rec.get("strokes"):
            continue
        per_class[label] += 1
        kept.append(rec)
    return kept


def rasterize_captures(
    records: Sequence[dict[str, Any]], classes: Sequence[str]
) -> tuple[np.ndarray, np.ndarray]:
    """Selected captures -> ``(bitmaps (N,28,28) uint8, labels (N,) int64)``."""
    index = {c: i for i, c in enumerate(classes)}
    bitmaps = [rasterize_strokes(rec["strokes"]) for rec in records]
    labels = [index[rec["label"]] for rec in records]
    if not bitmaps:
        return np.empty((0, 28, 28), dtype=np.uint8), np.empty((0,), dtype=np.int64)
    return np.stack(bitmaps).astype(np.uint8), np.asarray(labels, dtype=np.int64)


def augment_npz(
    base_path: Path,
    captures_dir: Path,
    out_path: Path,
    *,
    min_confidence: float,
    per_class_cap: int,
) -> int:
    """Extend the train split of ``base_path`` with captures; write ``out_path``. Returns #added."""
    with np.load(base_path) as data:
        arrays = {k: data[k] for k in data.files}
    classes = [str(c) for c in arrays["classes"]]

    selected = select_captures(
        read_ndjson_dir(captures_dir),
        classes,
        min_confidence=min_confidence,
        per_class_cap=per_class_cap,
    )
    bitmaps, labels = rasterize_captures(selected, classes)

    if len(bitmaps):
        arrays["x_train"] = np.concatenate([arrays["x_train"], bitmaps])
        arrays["y_train"] = np.concatenate([arrays["y_train"], labels])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, **arrays)
    return int(len(bitmaps))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fold captured drawings into the training set.")
    parser.add_argument(
        "--in", dest="base", type=Path, default=Path("data/processed/quickdraw.npz")
    )
    parser.add_argument(
        "--captures", type=Path, required=True, help="dir of synced capture *.jsonl"
    )
    parser.add_argument("--out", type=Path, default=Path("data/processed/quickdraw_augmented.npz"))
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument("--per-class-cap", type=int, default=500)
    args = parser.parse_args(argv)

    added = augment_npz(
        args.base,
        args.captures,
        args.out,
        min_confidence=args.min_confidence,
        per_class_cap=args.per_class_cap,
    )
    print(f"augmented training set with {added} captured drawings -> {args.out}")


if __name__ == "__main__":
    main()
