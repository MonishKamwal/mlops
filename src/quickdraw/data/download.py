"""Download QuickDraw 28x28 bitmap archives from Google's public GCS bucket.

Each class is one ``.npy`` file of shape ``(n_drawings, 784)`` uint8 (n is 100k+ per
class, so files run ~100 MB; 15 classes is roughly 1.5 GB). The full archives are
downloaded and kept as-is — sampling down to ``samples_per_class`` happens in
:mod:`quickdraw.data.preprocess`, so re-sampling never means re-downloading.

Existing files are skipped and writes are atomic (``.part`` then rename), which makes
the command safe to interrupt and re-run. DVC takes over artifact management in
Phase 2; this stays the download stage it wraps.

Usage: ``uv run python -m quickdraw.data.download``
"""

from __future__ import annotations

import argparse
import shutil
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from quickdraw.config import load_data_params

BITMAP_URL_TEMPLATE = (
    "https://storage.googleapis.com/quickdraw_dataset/full/numpy_bitmap/{name}.npy"
)


def class_url(name: str) -> str:
    """URL of one class's bitmap archive (QuickDraw class names may contain spaces)."""
    return BITMAP_URL_TEMPLATE.format(name=urllib.parse.quote(name))


def download_class(name: str, raw_dir: Path, *, force: bool = False) -> Path:
    """Download one class archive to ``raw_dir/<name>.npy`` unless it already exists.

    The download streams to a ``.part`` file and renames on completion, so a partial
    download from an interrupted run is never mistaken for the real file.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"{name}.npy"
    if target.exists() and not force:
        return target
    part = target.parent / f"{target.name}.part"
    with urllib.request.urlopen(class_url(name)) as response, part.open("wb") as fh:
        shutil.copyfileobj(response, fh)
    part.replace(target)
    return target


def download_all(classes: Sequence[str], raw_dir: Path, *, force: bool = False) -> list[Path]:
    """Download every class archive, reporting per-file status."""
    paths = []
    for name in classes:
        existed = (raw_dir / f"{name}.npy").exists() and not force
        path = download_class(name, raw_dir, force=force)
        size_mb = path.stat().st_size / 2**20
        print(f"{'kept' if existed else 'downloaded'} {path} ({size_mb:.1f} MB)")
        paths.append(path)
    return paths


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Download QuickDraw bitmap archives.")
    parser.add_argument("--params", type=Path, default=Path("params.yaml"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    args = parser.parse_args(argv)
    params = load_data_params(args.params)
    download_all(params.classes, args.raw_dir, force=args.force)


if __name__ == "__main__":
    main()
