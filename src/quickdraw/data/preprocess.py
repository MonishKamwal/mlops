"""Preprocessing shared between the training pipeline and the serving API.

Train/serve skew is this project's headline design rule (PLAN.md §2): the browser sends
the raw drawing (stroke list and/or a 280x280 PNG), and *everything* from there down to
the 28x28 model input happens in this module — the same code the training pipeline
uses. There is no JavaScript reimplementation to drift out of sync.

Two input formats, one output contract:

- Training data: Google's precomputed 28x28 grayscale bitmaps (white ink on black
  background), downloaded by :mod:`quickdraw.data.download`.
- Serve time: a stroke list in QuickDraw format (``[[[x...], [y...]], ...]``) or PNG
  bytes from the canvas. Both are reduced to the same 28x28 white-on-black bitmap.

Every path then ends in :func:`bitmap_to_model_input` — the single normalization step
shared by training batches and live requests alike. Processed data is stored as uint8
bitmaps precisely so that training must also pass through that function at load time:
parity by construction, not by discipline.

The stroke rasterizer approximates how Google rendered the dataset bitmaps (ink scaled
and centered in a square canvas, then anti-alias downsampled). Exact reproduction of
their renderer is impossible; the residual skew between real canvas drawings and the
QuickDraw distribution is a known, monitored risk (PLAN.md §7) that Evidently gets to
detect in Phase 4.
"""

from __future__ import annotations

import argparse
import io
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from quickdraw.config import DataParams, load_data_params

BITMAP_SIZE = 28

# Stroke-rendering geometry, chosen to approximate the dataset bitmaps: ink is scaled
# to fit a 256px canvas with a 16px margin and drawn with 16px-wide lines, so after the
# 256 -> 28 downsample strokes are ~1.75px wide with anti-aliased edges — visually
# close to Google's rendering of the numpy_bitmap files.
RENDER_SIZE = 256
RENDER_MARGIN = 16
LINE_WIDTH = 16

# Ink darker than this (out of 255) does not count when locating a PNG's bounding box,
# so stray anti-aliased edge pixels can't stretch the crop.
INK_THRESHOLD = 25

# One drawing: one sequence per stroke, each stroke [[x0, x1, ...], [y0, y1, ...]]
# (the QuickDraw raw/simplified JSON format; the canvas frontend sends the same shape).
Strokes = Sequence[Sequence[Sequence[float]]]


def bitmap_to_model_input(bitmap: np.ndarray) -> np.ndarray:
    """Normalize 28x28 uint8 bitmap(s) to the model's input tensor.

    This is the single shared final step of every path into the model — training
    batches and serve-time drawings alike end here. Accepts ``(28, 28)`` or
    ``(N, 28, 28)`` uint8 arrays (white ink on black) and returns float32 in [0, 1]
    with a channel axis: ``(1, 28, 28)`` or ``(N, 1, 28, 28)``.
    """
    arr = np.asarray(bitmap)
    if arr.ndim not in (2, 3) or arr.shape[-2:] != (BITMAP_SIZE, BITMAP_SIZE):
        raise ValueError(f"expected (..., {BITMAP_SIZE}, {BITMAP_SIZE}) bitmap, got {arr.shape}")
    if arr.dtype != np.uint8:
        raise ValueError(f"expected uint8 bitmap, got {arr.dtype}")
    return np.expand_dims(arr.astype(np.float32) / 255.0, axis=-3)


def rasterize_strokes(strokes: Strokes, *, line_width: int = LINE_WIDTH) -> np.ndarray:
    """Render a stroke drawing to a 28x28 uint8 bitmap, QuickDraw-style.

    Coordinates may be in any range (browser canvas, QuickDraw's 256-space, ...): the
    drawing is normalized to its own bounding box, scaled to fit the render canvas
    with margin (aspect ratio preserved), centered, drawn white-on-black, and
    anti-alias downsampled to 28x28.
    """
    points_per_stroke: list[list[tuple[float, float]]] = []
    for stroke in strokes:
        if len(stroke) != 2:
            raise ValueError("each stroke must be [[x0, x1, ...], [y0, y1, ...]]")
        points_per_stroke.append(list(zip(stroke[0], stroke[1], strict=True)))
    all_points = [point for points in points_per_stroke for point in points]
    if not all_points:
        raise ValueError("empty drawing: no stroke points")

    xs = [x for x, _ in all_points]
    ys = [y for _, y in all_points]
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    extent = max(max(xs) - min(xs), max(ys) - min(ys))
    box = RENDER_SIZE - 2 * RENDER_MARGIN
    scale = box / extent if extent > 0 else 1.0  # extent 0 = a single dot; draw as-is

    canvas = Image.new("L", (RENDER_SIZE, RENDER_SIZE), color=0)
    draw = ImageDraw.Draw(canvas)
    half = RENDER_SIZE / 2
    for points in points_per_stroke:
        mapped = [
            ((x - center_x) * scale + half, (y - center_y) * scale + half) for x, y in points
        ]
        if len(mapped) == 1:
            x, y = mapped[0]
            radius = line_width / 2
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=255)
        else:
            draw.line(mapped, fill=255, width=line_width, joint="curve")
    small = canvas.resize((BITMAP_SIZE, BITMAP_SIZE), Image.Resampling.BILINEAR)
    return np.asarray(small, dtype=np.uint8)


def png_to_bitmap(png_bytes: bytes) -> np.ndarray:
    """Reduce canvas PNG bytes to a 28x28 uint8 bitmap via the same geometry as strokes.

    Canvas convention: dark ink on a white or transparent background. The image is
    composited over white, inverted to the dataset's white-on-black polarity, cropped
    to the ink's bounding box, scaled into the render canvas with margin (aspect ratio
    preserved), centered, and downsampled to 28x28.
    """
    with Image.open(io.BytesIO(png_bytes)) as img:
        rgba = img.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    gray = Image.alpha_composite(background, rgba).convert("L")
    ink = 255 - np.asarray(gray, dtype=np.uint8)

    ink_rows, ink_cols = np.nonzero(ink > INK_THRESHOLD)
    if ink_rows.size == 0:
        raise ValueError("empty drawing: no ink in image")
    top, bottom = ink_rows.min(), ink_rows.max() + 1
    left, right = ink_cols.min(), ink_cols.max() + 1
    crop = Image.fromarray(ink[top:bottom, left:right])

    box = RENDER_SIZE - 2 * RENDER_MARGIN
    scale = box / max(crop.width, crop.height)
    new_width = max(1, round(crop.width * scale))
    new_height = max(1, round(crop.height * scale))
    resized = crop.resize((new_width, new_height), Image.Resampling.BILINEAR)
    canvas = Image.new("L", (RENDER_SIZE, RENDER_SIZE), color=0)
    canvas.paste(resized, ((RENDER_SIZE - new_width) // 2, (RENDER_SIZE - new_height) // 2))
    small = canvas.resize((BITMAP_SIZE, BITMAP_SIZE), Image.Resampling.BILINEAR)
    return np.asarray(small, dtype=np.uint8)


def strokes_to_model_input(strokes: Strokes) -> np.ndarray:
    """Serve-path entry point: stroke list -> (1, 28, 28) float32 model input."""
    return bitmap_to_model_input(rasterize_strokes(strokes))


def png_to_model_input(png_bytes: bytes) -> np.ndarray:
    """Serve-path entry point: canvas PNG bytes -> (1, 28, 28) float32 model input."""
    return bitmap_to_model_input(png_to_bitmap(png_bytes))


def load_class_bitmaps(path: Path, n_samples: int, rng: np.random.Generator) -> np.ndarray:
    """Sample ``n_samples`` drawings from one raw class archive -> (n, 28, 28) uint8.

    The archive is memory-mapped so sampling 10k rows never loads the full ~100 MB
    file; indices are sorted before the fancy read for sequential access.
    """
    data = np.load(path, mmap_mode="r")
    if data.ndim != 2 or data.shape[1] != BITMAP_SIZE * BITMAP_SIZE:
        raise ValueError(f"{path}: expected (n, {BITMAP_SIZE * BITMAP_SIZE}), got {data.shape}")
    total = data.shape[0]
    if n_samples > total:
        raise ValueError(f"{path}: has {total} drawings, need {n_samples}")
    indices = np.sort(rng.choice(total, size=n_samples, replace=False))
    return np.asarray(data[indices]).reshape(n_samples, BITMAP_SIZE, BITMAP_SIZE)


def train_val_test_split(
    images: np.ndarray,
    labels: np.ndarray,
    *,
    train_fraction: float,
    val_fraction: float,
    seed: int,
) -> dict[str, np.ndarray]:
    """Deterministic, stratified train/val/test split.

    Per class: shuffle, allocate ``train_fraction`` then ``val_fraction``; the
    remainder becomes the test split, so every sample lands somewhere. Each split is
    shuffled once more so consumers never see class-sorted data. Same inputs and seed
    give the same split on every machine — that determinism is what will make DVC's
    stage caching meaningful in Phase 2.
    """
    if len(images) != len(labels):
        raise ValueError(f"images ({len(images)}) and labels ({len(labels)}) length mismatch")
    if not 0 < train_fraction + val_fraction < 1:
        raise ValueError("train_fraction + val_fraction must leave room for the test split")
    rng = np.random.default_rng(seed)
    parts: dict[str, list[np.ndarray]] = {"train": [], "val": [], "test": []}
    for cls in np.unique(labels):
        cls_indices = rng.permutation(np.nonzero(labels == cls)[0])
        n_train = round(len(cls_indices) * train_fraction)
        n_val_end = n_train + round(len(cls_indices) * val_fraction)
        parts["train"].append(cls_indices[:n_train])
        parts["val"].append(cls_indices[n_train:n_val_end])
        parts["test"].append(cls_indices[n_val_end:])
    splits: dict[str, np.ndarray] = {}
    for name, chunks in parts.items():
        indices = rng.permutation(np.concatenate(chunks))
        splits[f"x_{name}"] = images[indices]
        splits[f"y_{name}"] = labels[indices]
    return splits


def preprocess_dataset(params: DataParams, raw_dir: Path, processed_dir: Path) -> Path:
    """Raw per-class archives -> ``processed_dir/quickdraw.npz``.

    The artifact keeps bitmaps as uint8 (4x smaller than float32) plus int64 labels
    and the class-name array. Normalization to model input happens at load time via
    :func:`bitmap_to_model_input` — the same function serving calls, by construction.

    Per-class sampling uses ``default_rng([seed, label])``: an independent stream per
    class, unaffected by class order or by other classes' draws.
    """
    images_per_class = []
    labels_per_class = []
    for label, name in enumerate(params.classes):
        rng = np.random.default_rng([params.seed, label])
        bitmaps = load_class_bitmaps(raw_dir / f"{name}.npy", params.samples_per_class, rng)
        images_per_class.append(bitmaps)
        labels_per_class.append(np.full(len(bitmaps), label, dtype=np.int64))
    images = np.concatenate(images_per_class)
    labels = np.concatenate(labels_per_class)
    splits = train_val_test_split(
        images,
        labels,
        train_fraction=params.train_fraction,
        val_fraction=params.val_fraction,
        seed=params.seed,
    )
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / "quickdraw.npz"
    np.savez_compressed(out_path, classes=np.array(params.classes), **splits)
    return out_path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Sample, normalize, and split QuickDraw data.")
    parser.add_argument("--params", type=Path, default=Path("params.yaml"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    args = parser.parse_args(argv)
    params = load_data_params(args.params)
    out_path = preprocess_dataset(params, args.raw_dir, args.processed_dir)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
