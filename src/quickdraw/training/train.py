"""Train the QuickDraw CNN; track every run in local MLflow.

Reads the processed dataset (``data/processed/quickdraw.npz``), trains
:class:`~quickdraw.training.model.QuickDrawCNN` per the ``training:`` section of
params.yaml, and writes the checkpoint of the best-validation-accuracy epoch to
``models/model.pt`` — the last epoch isn't necessarily the best one, and keeping the
best is a free, tiny form of early stopping.

Every batch is normalized by :func:`quickdraw.data.preprocess.bitmap_to_model_input` —
the same function the serving path calls. The processed artifact stores raw uint8
bitmaps precisely to force training through this shared step (PLAN.md §2: parity by
construction).

Runs land in MLflow's local backend (``sqlite:///mlflow.db`` + ``mlruns/`` for
artifacts, both gitignored). Inspect them with::

    uv run mlflow ui --backend-store-uri sqlite:///mlflow.db

Usage: ``uv run python -m quickdraw.training.train``
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import mlflow
import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset

from quickdraw.config import TrainingParams, load_training_params
from quickdraw.data.preprocess import bitmap_to_model_input
from quickdraw.training.model import QuickDrawCNN, save_checkpoint
from quickdraw.training.registry import (
    DEFAULT_TRACKING_URI,
    MLFLOW_EXPERIMENT,
    ensure_experiment,
    register_challenger,
)


def select_device() -> torch.device:
    """Best available device: Apple GPU (mps) > CUDA > CPU."""
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def load_split(data: np.lib.npyio.NpzFile, split: str) -> TensorDataset:
    """One split of the processed artifact -> tensors, via the shared normalization."""
    images = torch.from_numpy(bitmap_to_model_input(data[f"x_{split}"]))
    labels = torch.from_numpy(data[f"y_{split}"])
    return TensorDataset(images, labels)


def run_epoch(
    model: QuickDrawCNN,
    loader: DataLoader,
    device: torch.device,
    optimizer: torch.optim.Optimizer | None = None,
) -> tuple[float, float]:
    """One pass over ``loader``; trains when an optimizer is given, else evaluates.

    Returns ``(mean_loss, accuracy)`` over the whole pass.
    """
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    correct = 0
    seen = 0
    with torch.set_grad_enabled(training):
        for images, labels in loader:
            images, labels = images.to(device), labels.to(device)
            logits = model(images)
            loss = F.cross_entropy(logits, labels)
            if optimizer is not None:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
            total_loss += loss.item() * len(labels)
            correct += int((logits.argmax(dim=1) == labels).sum().item())
            seen += len(labels)
    return total_loss / seen, correct / seen


def train_model(
    params: TrainingParams,
    *,
    data_path: Path,
    model_dir: Path,
    tracking_uri: str = DEFAULT_TRACKING_URI,
) -> Path:
    """Full training run: fit, track in MLflow, write the best checkpoint.

    Seeded for reproducibility (weight init and batch shuffling); bit-exact repeats
    are only guaranteed on CPU — GPU backends may use non-deterministic kernels.
    """
    torch.manual_seed(params.seed)
    device = select_device()
    with np.load(data_path) as data:
        classes = [str(name) for name in data["classes"]]
        train_set = load_split(data, "train")
        val_set = load_split(data, "val")
    shuffle_rng = torch.Generator().manual_seed(params.seed)
    train_loader = DataLoader(
        train_set, batch_size=params.batch_size, shuffle=True, generator=shuffle_rng
    )
    val_loader = DataLoader(val_set, batch_size=params.batch_size)

    model = QuickDrawCNN(len(classes), dropout=params.dropout).to(device)
    optimizer = torch.optim.Adam(
        model.parameters(), lr=params.learning_rate, weight_decay=params.weight_decay
    )

    ensure_experiment(tracking_uri)
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "model.pt"
    best_accuracy = -1.0
    best_epoch = 0
    best_state: dict[str, torch.Tensor] = {}
    with mlflow.start_run() as run:
        mlflow.log_params(
            {
                **asdict(params),
                "num_classes": len(classes),
                "train_samples": len(train_set),
                "val_samples": len(val_set),
                "device": device.type,
            }
        )
        for epoch in range(1, params.epochs + 1):
            train_loss, train_accuracy = run_epoch(model, train_loader, device, optimizer)
            val_loss, val_accuracy = run_epoch(model, val_loader, device)
            mlflow.log_metrics(
                {
                    "train_loss": train_loss,
                    "train_accuracy": train_accuracy,
                    "val_loss": val_loss,
                    "val_accuracy": val_accuracy,
                },
                step=epoch,
            )
            print(
                f"epoch {epoch}/{params.epochs}: train_loss={train_loss:.4f} "
                f"val_loss={val_loss:.4f} val_accuracy={val_accuracy:.4f}"
            )
            if val_accuracy > best_accuracy:
                best_accuracy = val_accuracy
                best_epoch = epoch
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        save_checkpoint(
            model_path,
            best_state,
            classes=classes,
            dropout=params.dropout,
            val_accuracy=best_accuracy,
            epoch=best_epoch,
        )
        mlflow.log_metric("best_val_accuracy", best_accuracy)
        mlflow.log_artifact(str(model_path))
        version = register_challenger(run.info.run_id, tracking_uri)
    print(f"best epoch {best_epoch} (val_accuracy={best_accuracy:.4f}); wrote {model_path}")
    print(f"registered model '{MLFLOW_EXPERIMENT}' version {version} as challenger")
    return model_path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train the QuickDraw CNN.")
    parser.add_argument("--params", type=Path, default=Path("params.yaml"))
    parser.add_argument("--data", type=Path, default=Path("data/processed/quickdraw.npz"))
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--tracking-uri", default=DEFAULT_TRACKING_URI)
    args = parser.parse_args(argv)
    params = load_training_params(args.params)
    train_model(
        params, data_path=args.data, model_dir=args.model_dir, tracking_uri=args.tracking_uri
    )


if __name__ == "__main__":
    main()
