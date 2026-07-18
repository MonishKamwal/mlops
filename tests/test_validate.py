"""The validation stage must pass healthy data and fail loudly on every listed defect."""

import json

import numpy as np
import pandera.pandas as pa
import pytest

from quickdraw.config import DataParams
from quickdraw.data import validate

PARAMS = DataParams(
    classes=("apple", "banana", "cat"),
    samples_per_class=20,
    seed=1,
    train_fraction=0.8,
    val_fraction=0.1,
    test_fraction=0.1,
)


def doodle_batch(count: int) -> np.ndarray:
    """Sparse white-on-black strokes: ink fraction ~0.1, inside the healthy band."""
    images = np.zeros((count, 28, 28), dtype=np.uint8)
    images[:, 10:14, 4:24] = 255
    return images


def make_dataset() -> dict[str, np.ndarray]:
    dataset: dict[str, np.ndarray] = {"classes": np.array(PARAMS.classes)}
    for split, per_class in validate.split_expectations(PARAMS).items():
        images = np.concatenate([doodle_batch(per_class) for _ in PARAMS.classes])
        labels = np.repeat(np.arange(len(PARAMS.classes), dtype=np.int64), per_class)
        dataset[f"x_{split}"] = images
        dataset[f"y_{split}"] = labels
    return dataset


def write_npz(tmp_path, dataset):
    path = tmp_path / "quickdraw.npz"
    np.savez(path, **dataset)
    return path


def test_healthy_dataset_passes(tmp_path):
    report = validate.validate_dataset(write_npz(tmp_path, make_dataset()), PARAMS)
    assert report["result"] == "passed"
    assert report["split_totals"] == {"train": 48, "val": 6, "test": 6}


def test_missing_class_in_split_fails(tmp_path):
    dataset = make_dataset()
    keep = dataset["y_val"] != 2
    dataset["x_val"], dataset["y_val"] = dataset["x_val"][keep], dataset["y_val"][keep]
    with pytest.raises(pa.errors.SchemaErrors):
        validate.validate_dataset(write_npz(tmp_path, dataset), PARAMS)


def test_wrong_split_size_fails(tmp_path):
    dataset = make_dataset()
    dataset["x_train"] = dataset["x_train"][:-5]
    dataset["y_train"] = dataset["y_train"][:-5]
    with pytest.raises(pa.errors.SchemaErrors):
        validate.validate_dataset(write_npz(tmp_path, dataset), PARAMS)


def test_blank_images_fail_pixel_checks(tmp_path):
    dataset = make_dataset()
    dataset["x_test"] = np.zeros_like(dataset["x_test"])  # no ink anywhere
    with pytest.raises(pa.errors.SchemaErrors):
        validate.validate_dataset(write_npz(tmp_path, dataset), PARAMS)


def test_class_list_mismatch_fails_structurally(tmp_path):
    dataset = make_dataset()
    dataset["classes"] = np.array(tuple(reversed(PARAMS.classes)))  # order matters
    with pytest.raises(ValueError, match="order matters"):
        validate.validate_dataset(write_npz(tmp_path, dataset), PARAMS)


def test_wrong_dtype_fails_structurally(tmp_path):
    dataset = make_dataset()
    dataset["x_train"] = dataset["x_train"].astype(np.float32)
    with pytest.raises(ValueError, match="expected uint8"):
        validate.validate_dataset(write_npz(tmp_path, dataset), PARAMS)


def test_cli_writes_deterministic_report_and_fails_loudly(tmp_path, capsys):
    params_file = tmp_path / "params.yaml"
    params_file.write_text(
        "data:\n"
        "  classes: [apple, banana, cat]\n"
        "  samples_per_class: 20\n"
        "  seed: 1\n"
        "  splits: {train: 0.8, val: 0.1, test: 0.1}\n"
    )
    data = write_npz(tmp_path, make_dataset())
    out = tmp_path / "report.json"
    argv = ["--params", str(params_file), "--data", str(data), "--out", str(out)]

    validate.main(argv)
    first = out.read_bytes()
    validate.main(argv)
    assert out.read_bytes() == first, "report must be byte-identical run to run (DVC out)"
    assert json.loads(first)["result"] == "passed"

    bad = make_dataset()
    bad["x_val"] = np.zeros_like(bad["x_val"])
    bad_path = tmp_path / "bad.npz"
    np.savez(bad_path, **bad)
    with pytest.raises(SystemExit) as excinfo:
        validate.main(["--params", str(params_file), "--data", str(bad_path), "--out", str(out)])
    assert excinfo.value.code == 1
    assert "DATA VALIDATION FAILED" in capsys.readouterr().out
