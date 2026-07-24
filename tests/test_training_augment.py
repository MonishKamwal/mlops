import json

import numpy as np

from quickdraw.training.augment import (
    augment_npz,
    rasterize_captures,
    select_captures,
)

CLASSES = ["cat", "dog", "fish"]
STROKES = [[[10.0, 20.0, 30.0], [10.0, 15.0, 20.0]]]


def _cap(label: str, correct: bool, conf: float = 0.9) -> dict:
    return {"label": label, "correct": correct, "confidence": conf, "strokes": STROKES}


def test_thumbs_up_gated_by_confidence() -> None:
    recs = [_cap("cat", True, 0.9), _cap("dog", True, 0.5)]
    kept = select_captures(recs, CLASSES, min_confidence=0.7, per_class_cap=100)
    assert [r["label"] for r in kept] == ["cat"]  # low-confidence 👍 dropped


def test_thumbs_down_always_kept() -> None:
    # A 👎 correction is a labeled error — kept regardless of confidence.
    kept = select_captures(
        [_cap("fish", False, 0.1)], CLASSES, min_confidence=0.7, per_class_cap=100
    )
    assert len(kept) == 1


def test_per_class_cap() -> None:
    recs = [_cap("cat", True) for _ in range(5)]
    kept = select_captures(recs, CLASSES, min_confidence=0.7, per_class_cap=2)
    assert len(kept) == 2


def test_unknown_label_and_missing_strokes_skipped() -> None:
    recs = [
        _cap("not-a-class", False),
        {"label": "cat", "correct": True, "confidence": 0.9},  # no strokes
        _cap("cat", True),
    ]
    kept = select_captures(recs, CLASSES, min_confidence=0.7, per_class_cap=100)
    assert len(kept) == 1


def test_rasterize_captures_shapes_and_labels() -> None:
    recs = [_cap("cat", True), _cap("fish", False)]
    bitmaps, labels = rasterize_captures(recs, CLASSES)
    assert bitmaps.shape == (2, 28, 28)
    assert bitmaps.dtype == np.uint8
    assert labels.tolist() == [0, 2]  # cat=0, fish=2


def test_rasterize_empty() -> None:
    bitmaps, labels = rasterize_captures([], CLASSES)
    assert bitmaps.shape == (0, 28, 28)
    assert labels.shape == (0,)


def _write_base_npz(path, n_train=6) -> None:
    rng = np.random.default_rng(0)
    np.savez_compressed(
        path,
        classes=np.array(CLASSES),
        x_train=rng.integers(0, 255, (n_train, 28, 28), dtype=np.uint8),
        y_train=rng.integers(0, len(CLASSES), n_train, dtype=np.int64),
        x_val=rng.integers(0, 255, (2, 28, 28), dtype=np.uint8),
        y_val=rng.integers(0, len(CLASSES), 2, dtype=np.int64),
        x_test=rng.integers(0, 255, (2, 28, 28), dtype=np.uint8),
        y_test=rng.integers(0, len(CLASSES), 2, dtype=np.int64),
    )


def test_augment_npz_extends_only_train(tmp_path) -> None:
    base = tmp_path / "base.npz"
    _write_base_npz(base, n_train=6)
    caps = tmp_path / "captures" / "dt=2026-07-24"
    caps.mkdir(parents=True)
    caps.joinpath("a.jsonl").write_text(
        "\n".join(json.dumps(_cap(c, True)) for c in ["cat", "dog", "fish"]) + "\n"
    )
    out = tmp_path / "aug.npz"

    added = augment_npz(base, tmp_path / "captures", out, min_confidence=0.7, per_class_cap=100)

    assert added == 3
    with np.load(out) as d:
        assert d["x_train"].shape == (9, 28, 28)  # 6 + 3
        assert d["y_train"].shape == (9,)
        assert d["x_val"].shape == (2, 28, 28)  # untouched
        assert d["x_test"].shape == (2, 28, 28)  # untouched
        assert [str(c) for c in d["classes"]] == CLASSES


def test_augment_npz_no_captures_is_noop_copy(tmp_path) -> None:
    base = tmp_path / "base.npz"
    _write_base_npz(base, n_train=4)
    (tmp_path / "empty").mkdir()
    out = tmp_path / "aug.npz"

    added = augment_npz(base, tmp_path / "empty", out, min_confidence=0.7, per_class_cap=100)

    assert added == 0
    with np.load(out) as d:
        assert d["x_train"].shape == (4, 28, 28)  # unchanged
