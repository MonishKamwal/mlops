"""Export the trained checkpoint to ONNX and verify parity with PyTorch.

ONNX is the serving format (PLAN.md §2): the API runs onnxruntime, so the ~2 GB torch
dependency never enters the serving image. That framework hop is a second place where
train and serve could silently diverge (the first — preprocessing — is closed by the
shared transform), so this module doesn't just export: it runs the same inputs through
both runtimes and fails loudly if the logits disagree beyond float tolerance.

The class list travels inside the ONNX file as metadata, making the artifact
self-describing: serving reads the label mapping from the model itself, not from a
params.yaml it must trust to be the right version.

Usage: ``uv run python -m quickdraw.training.export_onnx``
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import onnx
import onnxruntime
import torch

from quickdraw.training.model import load_checkpoint

# Same architecture, different kernels/instruction order -> bit-exact equality is not
# achievable; anything beyond this bound means a real export defect, not float noise.
PARITY_TOLERANCE = 1e-4

INPUT_NAME = "input"
OUTPUT_NAME = "logits"


def export_model(checkpoint_path: Path, onnx_path: Path) -> Path:
    """Checkpoint -> ONNX file with a dynamic batch axis and embedded metadata."""
    model, checkpoint = load_checkpoint(checkpoint_path)
    dummy = torch.zeros(1, 1, 28, 28)
    onnx_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        (dummy,),
        str(onnx_path),
        input_names=[INPUT_NAME],
        output_names=[OUTPUT_NAME],
        # keyed by the forward() argument name; makes the batch axis dynamic so the
        # same file serves single live requests and batched evaluation
        dynamic_shapes={"x": {0: torch.export.Dim("batch")}},
    )
    onnx_model = onnx.load(str(onnx_path))
    for key, value in (
        ("classes", json.dumps(checkpoint["classes"])),
        ("val_accuracy", str(checkpoint["val_accuracy"])),
    ):
        prop = onnx_model.metadata_props.add()
        prop.key, prop.value = key, value
    onnx.save(onnx_model, str(onnx_path))
    return onnx_path


def max_abs_difference(
    checkpoint_path: Path, onnx_path: Path, *, batch_size: int = 64, seed: int = 0
) -> float:
    """Largest |logit_pytorch - logit_onnx| over a random input batch, both on CPU."""
    model, _ = load_checkpoint(checkpoint_path)
    rng = np.random.default_rng(seed)
    inputs = rng.random((batch_size, 1, 28, 28), dtype=np.float32)
    with torch.no_grad():
        torch_logits = model(torch.from_numpy(inputs)).numpy()
    session = onnxruntime.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    (onnx_logits,) = session.run([OUTPUT_NAME], {INPUT_NAME: inputs})
    return float(np.max(np.abs(torch_logits - onnx_logits)))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export the QuickDraw model to ONNX.")
    parser.add_argument("--model", type=Path, default=Path("models/model.pt"))
    parser.add_argument("--out", type=Path, default=Path("models/model.onnx"))
    args = parser.parse_args(argv)
    export_model(args.model, args.out)
    difference = max_abs_difference(args.model, args.out)
    if difference > PARITY_TOLERANCE:
        raise SystemExit(
            f"parity FAILED: max abs logit difference {difference:.2e} "
            f"exceeds tolerance {PARITY_TOLERANCE:.0e}"
        )
    size_mb = args.out.stat().st_size / 2**20
    print(f"wrote {args.out} ({size_mb:.1f} MB)")
    print(f"parity OK: max abs logit difference {difference:.2e} <= {PARITY_TOLERANCE:.0e}")


if __name__ == "__main__":
    main()
