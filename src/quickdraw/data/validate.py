"""Validate the processed dataset before training consumes it (Phase 2, task 2).

Pandera validates dataframes, not image tensors — so the npz is reduced to a
per-(split, class) *metadata* dataframe (sample count, pixel range, mean ink
fraction) and the schema describes what a healthy dataset looks like. Structural
facts a dataframe can't carry (array dtypes/shapes, the class list embedded in
the artifact matching params.yaml, order included) are checked directly first.

The stage sits between preprocess and train, not before preprocess as PLAN.md
originally sketched: every check here describes the processed artifact, and
malformed *raw* data already crashes preprocess loudly on its own. Training is
the expensive, silent consumer — so the gate goes at its door.

The report this writes is deliberately timestamp-free: it is a DVC out, and a
deterministic input must produce byte-identical output or the stage would never
be cache-stable.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pandera.pandas as pa

from quickdraw.config import DataParams, load_data_params

SPLITS = ("train", "val", "test")

# Loose-then-calibrate bounds (same philosophy as the task-1 parity thresholds):
# QuickDraw doodles are sparse white-on-black ink, so aggregated over 1000+
# drawings a class must show true black background, near-white ink somewhere,
# and a mean ink fraction well away from "blank" and "scribbled solid".
MIN_INK_PEAK = 200
MEAN_INK_BOUNDS = (0.01, 0.40)


def split_expectations(params: DataParams) -> dict[str, int]:
    """Exact per-class sample count for each split, as preprocess constructs them."""
    n_train = round(params.samples_per_class * params.train_fraction)
    n_val = round(params.samples_per_class * params.val_fraction)
    return {
        "train": n_train,
        "val": n_val,
        "test": params.samples_per_class - n_train - n_val,
    }


def dataset_metadata(dataset: dict[str, np.ndarray], classes: Sequence[str]) -> pd.DataFrame:
    """Reduce the tensors to one metadata row per (split, class)."""
    rows = []
    for split in SPLITS:
        images, labels = dataset[f"x_{split}"], dataset[f"y_{split}"]
        for label in np.unique(labels):
            class_images = images[labels == label]
            rows.append(
                {
                    "split": split,
                    "label": int(label),
                    "class_name": classes[label] if label < len(classes) else "<unknown>",
                    "count": len(class_images),
                    "pixel_min": int(class_images.min()),
                    "pixel_max": int(class_images.max()),
                    "mean_ink": float(class_images.mean() / 255.0),
                }
            )
    return pd.DataFrame(rows)


def metadata_schema(params: DataParams) -> pa.DataFrameSchema:
    """What a healthy processed dataset looks like, one row per (split, class)."""
    expected = split_expectations(params)
    n_classes = len(params.classes)

    def full_label_set_per_split(df: pd.DataFrame) -> bool:
        return all(
            set(df.loc[df["split"] == split, "label"]) == set(range(n_classes)) for split in SPLITS
        )

    return pa.DataFrameSchema(
        columns={
            "split": pa.Column(str, pa.Check.isin(SPLITS)),
            "label": pa.Column(int, pa.Check.in_range(0, n_classes - 1)),
            "class_name": pa.Column(str, pa.Check.isin(params.classes)),
            "count": pa.Column(int),
            "pixel_min": pa.Column(int, pa.Check.eq(0), description="true black background"),
            "pixel_max": pa.Column(
                int,
                [pa.Check.le(255), pa.Check.ge(MIN_INK_PEAK)],
                description="near-white ink present",
            ),
            "mean_ink": pa.Column(
                float,
                pa.Check.in_range(*MEAN_INK_BOUNDS),
                description="not blank, not scribbled solid",
            ),
        },
        checks=[
            pa.Check(
                lambda df: df["count"] == df["split"].map(expected),
                name="split_sizes_exact",
                error="per-class count must match the split fractions exactly",
            ),
            pa.Check(
                lambda df: df["class_name"] == df["label"].map(dict(enumerate(params.classes))),
                name="label_class_mapping",
                error="label index must map to its params.yaml class (order matters)",
            ),
            pa.Check(
                full_label_set_per_split,
                name="all_classes_in_every_split",
                error="every split must contain every class",
            ),
        ],
        strict=True,
    )


def check_structure(dataset: dict[str, np.ndarray], params: DataParams) -> None:
    """Tensor-level facts the dataframe can't carry; raises ValueError on violation."""
    artifact_classes = tuple(dataset["classes"])
    if artifact_classes != params.classes:
        raise ValueError(
            "class list in the artifact differs from params.yaml (order matters): "
            f"{artifact_classes} != {params.classes}"
        )
    for split in SPLITS:
        images, labels = dataset[f"x_{split}"], dataset[f"y_{split}"]
        if images.dtype != np.uint8 or images.ndim != 3 or images.shape[1:] != (28, 28):
            raise ValueError(
                f"x_{split}: expected uint8 (N, 28, 28), got {images.dtype} {images.shape}"
            )
        if labels.dtype != np.int64 or labels.shape != (len(images),):
            raise ValueError(
                f"y_{split}: expected int64 labels matching x_{split}, "
                f"got {labels.dtype} {labels.shape}"
            )


def validate_dataset(data_path: Path, params: DataParams) -> dict:
    """Validate the artifact; returns a deterministic report dict, raises on bad data."""
    with np.load(data_path, allow_pickle=False) as npz:
        dataset = {key: npz[key] for key in npz.files}
    check_structure(dataset, params)
    metadata = dataset_metadata(dataset, params.classes)
    schema = metadata_schema(params)
    schema.validate(metadata, lazy=True)
    return {
        "artifact": data_path.name,
        "n_classes": len(params.classes),
        "rows_validated": len(metadata),
        "split_totals": {split: int(len(dataset[f"y_{split}"])) for split in SPLITS},
        "checks": ["structure", *sorted(check.name for check in schema.checks)],
        "result": "passed",
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate the processed QuickDraw dataset.")
    parser.add_argument("--params", type=Path, default=Path("params.yaml"))
    parser.add_argument("--data", type=Path, default=Path("data/processed/quickdraw.npz"))
    parser.add_argument("--out", type=Path, default=Path("reports/data_validation.json"))
    args = parser.parse_args(argv)
    params = load_data_params(args.params)
    try:
        report = validate_dataset(args.data, params)
    except pa.errors.SchemaErrors as err:
        print("DATA VALIDATION FAILED — training must not see this dataset:")
        print(err.failure_cases.to_string())
        raise SystemExit(1) from err
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"validation passed ({report['rows_validated']} metadata rows); wrote {args.out}")


if __name__ == "__main__":
    main()
