"""Tests for params.yaml loading and validation."""

from pathlib import Path

import pytest

from quickdraw.config import load_data_params

REPO_ROOT = Path(__file__).parent.parent

VALID = """\
data:
  classes: [cat, dog]
  samples_per_class: 100
  seed: 7
  splits: {train: 0.8, val: 0.1, test: 0.1}
"""


def write_params(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "params.yaml"
    path.write_text(text)
    return path


def test_loads_valid_params(tmp_path: Path) -> None:
    params = load_data_params(write_params(tmp_path, VALID))
    assert params.classes == ("cat", "dog")
    assert params.samples_per_class == 100
    assert params.seed == 7
    assert params.train_fraction == 0.8
    assert params.val_fraction == 0.1
    assert params.test_fraction == 0.1


def test_committed_params_file_is_valid() -> None:
    params = load_data_params(REPO_ROOT / "params.yaml")
    assert len(params.classes) == 15
    assert params.samples_per_class == 10000


def test_rejects_bad_split_sum(tmp_path: Path) -> None:
    bad = VALID.replace("train: 0.8", "train: 0.7")
    with pytest.raises(ValueError, match="sum"):
        load_data_params(write_params(tmp_path, bad))


def test_rejects_duplicate_classes(tmp_path: Path) -> None:
    bad = VALID.replace("[cat, dog]", "[cat, cat]")
    with pytest.raises(ValueError, match="duplicate"):
        load_data_params(write_params(tmp_path, bad))


def test_rejects_empty_classes(tmp_path: Path) -> None:
    bad = VALID.replace("[cat, dog]", "[]")
    with pytest.raises(ValueError, match="empty"):
        load_data_params(write_params(tmp_path, bad))
