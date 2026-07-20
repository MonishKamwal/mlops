"""Tests for params.yaml loading and validation."""

from pathlib import Path

import pytest

from quickdraw.config import load_data_params, load_gate_params, load_training_params

REPO_ROOT = Path(__file__).parent.parent

VALID = """\
data:
  classes: [cat, dog]
  samples_per_class: 100
  seed: 7
  splits: {train: 0.8, val: 0.1, test: 0.1}
"""

VALID_TRAINING = """\
training:
  epochs: 8
  batch_size: 256
  learning_rate: 0.001
  weight_decay: 0.0001
  dropout: 0.3
  seed: 42
"""

VALID_GATE = """\
gate:
  min_test_accuracy: 0.85
  epsilon: 0.005
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


def test_loads_valid_training_params(tmp_path: Path) -> None:
    params = load_training_params(write_params(tmp_path, VALID_TRAINING))
    assert params.epochs == 8
    assert params.batch_size == 256
    assert params.learning_rate == 0.001
    assert params.weight_decay == 0.0001
    assert params.dropout == 0.3
    assert params.seed == 42


def test_committed_training_params_are_valid() -> None:
    params = load_training_params(REPO_ROOT / "params.yaml")
    assert params.epochs > 0


def test_rejects_zero_epochs(tmp_path: Path) -> None:
    bad = VALID_TRAINING.replace("epochs: 8", "epochs: 0")
    with pytest.raises(ValueError, match="epochs"):
        load_training_params(write_params(tmp_path, bad))


def test_rejects_dropout_of_one(tmp_path: Path) -> None:
    bad = VALID_TRAINING.replace("dropout: 0.3", "dropout: 1.0")
    with pytest.raises(ValueError, match="dropout"):
        load_training_params(write_params(tmp_path, bad))


def test_rejects_negative_learning_rate(tmp_path: Path) -> None:
    bad = VALID_TRAINING.replace("learning_rate: 0.001", "learning_rate: -0.001")
    with pytest.raises(ValueError, match="learning_rate"):
        load_training_params(write_params(tmp_path, bad))


def test_loads_valid_gate_params(tmp_path: Path) -> None:
    params = load_gate_params(write_params(tmp_path, VALID_GATE))
    assert params.min_test_accuracy == 0.85
    assert params.epsilon == 0.005


def test_committed_gate_params_are_valid() -> None:
    params = load_gate_params(REPO_ROOT / "params.yaml")
    assert 0 < params.min_test_accuracy <= 1
    assert params.epsilon >= 0


def test_rejects_negative_epsilon(tmp_path: Path) -> None:
    bad = VALID_GATE.replace("epsilon: 0.005", "epsilon: -0.01")
    with pytest.raises(ValueError, match="epsilon"):
        load_gate_params(write_params(tmp_path, bad))


def test_rejects_floor_above_one(tmp_path: Path) -> None:
    bad = VALID_GATE.replace("min_test_accuracy: 0.85", "min_test_accuracy: 1.5")
    with pytest.raises(ValueError, match="min_test_accuracy"):
        load_gate_params(write_params(tmp_path, bad))
