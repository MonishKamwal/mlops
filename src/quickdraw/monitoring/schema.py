"""Pandera schema for the current-window drift dataframe (PLAN.md Phase 4, task 2).

The prediction logs are external, untrusted input — a malformed record or an out-of-range
probability should fail the drift job loudly, not silently skew the comparison. The reference
is our own trusted artifact, so only the current window is validated.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from quickdraw.config import load_data_params


def current_schema(classes: tuple[str, ...]) -> pa.DataFrameSchema:
    """Schema for the current-window frame produced by ``drift.records_to_frame``."""
    return pa.DataFrameSchema(
        {
            # predicted labels can only be model classes (they come from the model's own head)
            "predicted_label": pa.Column(str, pa.Check.isin(classes)),
            "confidence": pa.Column(float, pa.Check.in_range(0.0, 1.0)),
            # margin = top1 - top2; top1 is the max, so it's always in [0, 1]
            "margin": pa.Column(float, pa.Check.in_range(0.0, 1.0)),
            "source": pa.Column(str),
            "model_sha256": pa.Column(str, nullable=True),
        },
        coerce=True,
    )


def validate_current(df: pd.DataFrame, params_path: str | Path = "params.yaml") -> pd.DataFrame:
    """Validate the current-window frame; raise loudly on bad data or an empty window."""
    if df.empty:
        raise ValueError("no prediction-log records in the window — nothing to compare")
    classes = load_data_params(params_path).classes
    return current_schema(classes).validate(df)
