"""End-to-end training smoke test on a tiny synthetic dataset.

The dataset is trivially separable (each class is a bright horizontal band in a
different position), so a few epochs must push validation accuracy well above chance —
that catches wiring bugs (loss not decreasing, labels misaligned, best-epoch tracking
broken) without needing real data or minutes of runtime.
"""

from pathlib import Path

import mlflow
import numpy as np
import pytest

from quickdraw.config import TrainingParams
from quickdraw.training.model import load_checkpoint
from quickdraw.training.train import MLFLOW_EXPERIMENT, train_model

NUM_CLASSES = 3
CLASSES = ["cat", "dog", "fish"]


def make_split(rng: np.random.Generator, n_per_class: int) -> tuple[np.ndarray, np.ndarray]:
    images, labels = [], []
    for cls in range(NUM_CLASSES):
        bitmaps = rng.integers(0, 40, size=(n_per_class, 28, 28)).astype(np.uint8)
        bitmaps[:, cls * 9 : cls * 9 + 9, :] = 220  # the class-identifying band
        images.append(bitmaps)
        labels.append(np.full(n_per_class, cls, dtype=np.int64))
    return np.concatenate(images), np.concatenate(labels)


@pytest.fixture()
def data_path(tmp_path: Path) -> Path:
    rng = np.random.default_rng(0)
    x_train, y_train = make_split(rng, 30)
    x_val, y_val = make_split(rng, 10)
    path = tmp_path / "quickdraw.npz"
    np.savez(
        path,
        classes=np.array(CLASSES),
        x_train=x_train,
        y_train=y_train,
        x_val=x_val,
        y_val=y_val,
    )
    return path


def test_train_model_end_to_end(data_path: Path, tmp_path: Path) -> None:
    params = TrainingParams(
        epochs=4, batch_size=16, learning_rate=0.01, weight_decay=0.0, dropout=0.0, seed=0
    )
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    model_path = train_model(
        params, data_path=data_path, model_dir=tmp_path / "models", tracking_uri=tracking_uri
    )

    assert model_path.exists()
    model, checkpoint = load_checkpoint(model_path)
    assert checkpoint["classes"] == CLASSES
    assert checkpoint["val_accuracy"] > 0.6  # separable data; chance is 1/3
    assert 1 <= checkpoint["epoch"] <= params.epochs

    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    experiment = client.get_experiment_by_name(MLFLOW_EXPERIMENT)
    assert experiment is not None
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 1
    run = runs[0]
    assert run.data.metrics["best_val_accuracy"] == pytest.approx(checkpoint["val_accuracy"])
    assert run.data.params["epochs"] == "4"
