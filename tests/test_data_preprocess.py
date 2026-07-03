"""Tests for preprocessing — including the train/serve parity guarantees (PLAN.md §2)."""

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from quickdraw.config import DataParams
from quickdraw.data import preprocess

# A little house in a 280x280 canvas coordinate space, QuickDraw stroke format
# ([[xs], [ys]] per stroke): roof, walls, door — enough geometry that a parity
# failure is visible, not a single-pixel accident.
HOUSE_STROKES = [
    [[40, 140, 240], [120, 30, 120]],
    [[55, 55, 225, 225, 55], [120, 250, 250, 120, 120]],
    [[120, 120, 160, 160], [250, 180, 180, 250]],
]


# --- bitmap_to_model_input: the single shared normalization step ---


def test_bitmap_to_model_input_contract() -> None:
    bitmap = np.zeros((28, 28), dtype=np.uint8)
    bitmap[10, 10] = 255
    tensor = preprocess.bitmap_to_model_input(bitmap)
    assert tensor.shape == (1, 28, 28)
    assert tensor.dtype == np.float32
    assert tensor.min() == 0.0
    assert tensor.max() == 1.0


def test_bitmap_to_model_input_batches() -> None:
    batch = np.zeros((5, 28, 28), dtype=np.uint8)
    assert preprocess.bitmap_to_model_input(batch).shape == (5, 1, 28, 28)


def test_bitmap_to_model_input_rejects_wrong_shape() -> None:
    with pytest.raises(ValueError, match="expected"):
        preprocess.bitmap_to_model_input(np.zeros((32, 32), dtype=np.uint8))


def test_bitmap_to_model_input_rejects_non_uint8() -> None:
    with pytest.raises(ValueError, match="uint8"):
        preprocess.bitmap_to_model_input(np.zeros((28, 28), dtype=np.float32))


# --- stroke rasterization ---


def test_rasterize_strokes_bitmap_contract() -> None:
    bitmap = preprocess.rasterize_strokes(HOUSE_STROKES)
    assert bitmap.shape == (28, 28)
    assert bitmap.dtype == np.uint8
    assert bitmap.max() > 0, "no ink rendered"
    assert (bitmap > 0).mean() < 0.5, "mostly background expected"


def test_rasterize_is_deterministic() -> None:
    np.testing.assert_array_equal(
        preprocess.rasterize_strokes(HOUSE_STROKES),
        preprocess.rasterize_strokes(HOUSE_STROKES),
    )


def test_rasterize_is_scale_invariant() -> None:
    scaled = [[[x / 10 for x in xs], [y / 10 for y in ys]] for xs, ys in HOUSE_STROKES]
    a = preprocess.rasterize_strokes(HOUSE_STROKES).astype(int)
    b = preprocess.rasterize_strokes(scaled).astype(int)
    # drawings are normalized to their own bounding box, so canvas scale must not matter
    assert np.abs(a - b).mean() < 3


def test_rasterize_single_dot() -> None:
    bitmap = preprocess.rasterize_strokes([[[150], [150]]])
    assert bitmap.shape == (28, 28)
    assert bitmap.max() > 0


def test_rasterize_empty_drawing_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        preprocess.rasterize_strokes([])


def test_rasterize_rejects_malformed_stroke() -> None:
    with pytest.raises(ValueError):
        preprocess.rasterize_strokes([[[1, 2, 3], [4, 5]]])


# --- the parity guarantees ---


def test_strokes_serve_path_equals_train_path_exactly() -> None:
    """The headline guarantee: the serve path IS the train path.

    Serving normalizes drawings via strokes_to_model_input; training normalizes stored
    bitmaps via bitmap_to_model_input. For the same drawing the tensors must be
    bit-identical — any divergence means a second preprocessing path has appeared.
    """
    serve = preprocess.strokes_to_model_input(HOUSE_STROKES)
    train = preprocess.bitmap_to_model_input(preprocess.rasterize_strokes(HOUSE_STROKES))
    np.testing.assert_array_equal(serve, train)


def _house_png(size: int = 280, width: int = 18) -> bytes:
    """Draw HOUSE_STROKES the way the browser canvas will: dark ink on white."""
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    for xs, ys in HOUSE_STROKES:
        draw.line(list(zip(xs, ys, strict=True)), fill="black", width=width, joint="curve")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def test_png_and_stroke_paths_agree() -> None:
    """The two serve-time input formats must land on (nearly) the same tensor.

    Exact equality is impossible — one path rasterizes vectors, the other re-crops
    pixels — so this asserts closeness: high ink overlap, low mean difference.
    Thresholds are deliberately loose; tighten after calibrating against the real
    frontend canvas in Phase 1 task 5.
    """
    from_strokes = preprocess.strokes_to_model_input(HOUSE_STROKES)
    from_png = preprocess.png_to_model_input(_house_png())
    assert from_strokes.shape == from_png.shape

    ink_strokes = from_strokes > 0.2
    ink_png = from_png > 0.2
    iou = (ink_strokes & ink_png).sum() / (ink_strokes | ink_png).sum()
    assert iou > 0.4, f"stroke/PNG ink overlap too low: IoU={iou:.2f}"

    mad = np.abs(from_strokes - from_png).mean()
    assert mad < 0.15, f"stroke/PNG tensors diverge: mean abs diff={mad:.3f}"


def test_png_with_transparent_background() -> None:
    """Canvas PNGs may arrive with alpha instead of a white background."""
    rgba = Image.new("RGBA", (280, 280), (0, 0, 0, 0))
    draw = ImageDraw.Draw(rgba)
    draw.line([(40, 240), (240, 40)], fill=(0, 0, 0, 255), width=18)
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG")
    bitmap = preprocess.png_to_bitmap(buffer.getvalue())
    assert bitmap.shape == (28, 28)
    assert bitmap.max() > 0


def test_png_blank_raises() -> None:
    img = Image.new("RGB", (280, 280), "white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    with pytest.raises(ValueError, match="empty"):
        preprocess.png_to_bitmap(buffer.getvalue())


# --- splitting and the processed artifact ---

N_PER_CLASS = 100


def _toy_dataset(n_classes: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Blank images carrying a per-sample id in their first two pixels."""
    n = N_PER_CLASS * n_classes
    images = np.zeros((n, 28, 28), dtype=np.uint8)
    images[:, 0, 0] = np.arange(n) % 256
    images[:, 0, 1] = np.arange(n) // 256
    labels = np.repeat(np.arange(n_classes, dtype=np.int64), N_PER_CLASS)
    return images, labels


def _row_ids(x: np.ndarray) -> np.ndarray:
    return x[:, 0, 0].astype(int) + 256 * x[:, 0, 1].astype(int)


def test_split_fractions_and_stratification() -> None:
    images, labels = _toy_dataset()
    splits = preprocess.train_val_test_split(
        images, labels, train_fraction=0.8, val_fraction=0.1, seed=0
    )
    assert len(splits["y_train"]) == 240
    assert len(splits["y_val"]) == 30
    assert len(splits["y_test"]) == 30
    for key in ("y_train", "y_val", "y_test"):
        counts = np.bincount(splits[key], minlength=3)
        assert (counts == counts[0]).all(), f"{key} not stratified: {counts}"


def test_split_is_deterministic() -> None:
    images, labels = _toy_dataset()
    a = preprocess.train_val_test_split(
        images, labels, train_fraction=0.8, val_fraction=0.1, seed=123
    )
    b = preprocess.train_val_test_split(
        images, labels, train_fraction=0.8, val_fraction=0.1, seed=123
    )
    for key in a:
        np.testing.assert_array_equal(a[key], b[key])


def test_split_covers_everything_without_leakage() -> None:
    images, labels = _toy_dataset()
    splits = preprocess.train_val_test_split(
        images, labels, train_fraction=0.8, val_fraction=0.1, seed=7
    )
    id_sets = [set(_row_ids(splits[f"x_{name}"])) for name in ("train", "val", "test")]
    assert sum(len(ids) for ids in id_sets) == len(images), "sample duplicated across splits"
    assert set.union(*id_sets) == set(range(len(images))), "sample lost by the split"


def test_split_keeps_images_and_labels_aligned() -> None:
    images, labels = _toy_dataset()
    splits = preprocess.train_val_test_split(
        images, labels, train_fraction=0.8, val_fraction=0.1, seed=7
    )
    for name in ("train", "val", "test"):
        expected = _row_ids(splits[f"x_{name}"]) // N_PER_CLASS
        np.testing.assert_array_equal(expected, splits[f"y_{name}"])


def test_preprocess_dataset_end_to_end(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for name in ("cat", "dog"):
        archive = rng.integers(0, 256, size=(40, 784), dtype=np.uint8)
        np.save(raw_dir / f"{name}.npy", archive)

    params = DataParams(
        classes=("cat", "dog"),
        samples_per_class=20,
        seed=1,
        train_fraction=0.8,
        val_fraction=0.1,
        test_fraction=0.1,
    )
    out_path = preprocess.preprocess_dataset(params, raw_dir, tmp_path / "processed")
    with np.load(out_path) as data:
        assert list(data["classes"]) == ["cat", "dog"]
        assert data["x_train"].shape == (32, 28, 28)
        assert data["x_train"].dtype == np.uint8
        assert data["y_train"].dtype == np.int64
        assert data["y_val"].shape == (4,)
        assert data["y_test"].shape == (4,)
        assert set(np.unique(data["y_train"])) == {0, 1}


def test_preprocess_dataset_is_deterministic(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    np.save(raw_dir / "cat.npy", rng.integers(0, 256, size=(40, 784), dtype=np.uint8))

    params = DataParams(
        classes=("cat",),
        samples_per_class=20,
        seed=1,
        train_fraction=0.8,
        val_fraction=0.1,
        test_fraction=0.1,
    )
    first = preprocess.preprocess_dataset(params, raw_dir, tmp_path / "out1")
    second = preprocess.preprocess_dataset(params, raw_dir, tmp_path / "out2")
    with np.load(first) as a, np.load(second) as b:
        for key in ("x_train", "y_train", "x_val", "y_val", "x_test", "y_test"):
            np.testing.assert_array_equal(a[key], b[key])
