"""Prediction-drift report (PLAN.md Phase 4, task 2).

Compares the model's *live* output distribution (from the prediction logs) against the frozen
reference (`quickdraw.monitoring.reference`, the model's behaviour on the QuickDraw test split).
Because real visitor doodles are out-of-distribution, the model is measurably less confident on
them — so `confidence` and `margin` shift down and the predicted-class mix shifts. That's the
drift this surfaces.

Reads local NDJSON (the workflow `aws s3 sync`s the `predictions/dt=…` logs to a directory, so
this module never touches S3 and stays fully testable). Emits **`drift.json`** — a styling-
agnostic data contract the portfolio site renders (per-column drift scores + self-computed
reference-vs-current distributions) — plus Evidently's HTML as a functional/dev artifact.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

NUMERICAL = ("confidence", "margin")
CATEGORICAL = "predicted_label"
# Pin distance metrics rather than statistical tests: with ~15k reference vs a few hundred
# live rows, K-S/chi-square p-values are hypersensitive (large n -> always "drift"), while
# Wasserstein/Jensen-Shannon measure drift *magnitude* independent of window size. Distance
# semantics: drifted when score > threshold (the opposite direction from a p-value).
NUM_METHOD, CAT_METHOD, DRIFT_THRESHOLD = "wasserstein", "jensenshannon", 0.1


def records_to_frame(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Prediction-log JSONL records -> the drift dataframe (same columns as the reference).

    Pure. `top3` is `[{label, probability}, ...]`; `margin` is top1-top2 (0 if only one class).
    """
    rows = []
    for rec in records:
        top3 = rec.get("top3", [])
        if not top3:
            continue
        conf = float(top3[0]["probability"])
        second = float(top3[1]["probability"]) if len(top3) > 1 else 0.0
        rows.append(
            {
                "predicted_label": top3[0]["label"],
                "confidence": conf,
                "margin": conf - second,
                "source": rec.get("source", "unknown"),
                "model_sha256": rec.get("model_sha256", ""),
            }
        )
    return pd.DataFrame(
        rows, columns=["predicted_label", "confidence", "margin", "source", "model_sha256"]
    )


def read_ndjson_dir(directory: Path) -> list[dict[str, Any]]:
    """Read every ``*.jsonl`` record under ``directory`` (one JSON object per line)."""
    records = []
    for path in sorted(directory.rglob("*.jsonl")):
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _numeric_distribution(ref: pd.Series, cur: pd.Series, bins: int = 20) -> dict[str, Any]:
    """Shared-bin histograms + summary stats for a numeric column (both in [0, 1])."""
    edges = np.linspace(0.0, 1.0, bins + 1)
    ref_counts, _ = np.histogram(ref, bins=edges)
    cur_counts, _ = np.histogram(cur, bins=edges)
    return {
        "bin_edges": [round(e, 4) for e in edges.tolist()],
        "reference": {
            "counts": ref_counts.tolist(),
            "mean": round(float(ref.mean()), 4),
            "median": round(float(ref.median()), 4),
        },
        "current": {
            "counts": cur_counts.tolist(),
            "mean": round(float(cur.mean()), 4),
            "median": round(float(cur.median()), 4),
        },
    }


def _categorical_distribution(ref: pd.Series, cur: pd.Series) -> dict[str, Any]:
    """Normalised class shares for reference and current, over the union of labels."""
    labels = sorted(set(ref.unique()) | set(cur.unique()))
    ref_share = ref.value_counts(normalize=True)
    cur_share = cur.value_counts(normalize=True)
    return {
        "labels": labels,
        "reference": {lbl: round(float(ref_share.get(lbl, 0.0)), 4) for lbl in labels},
        "current": {lbl: round(float(cur_share.get(lbl, 0.0)), 4) for lbl in labels},
    }


def _parse_evidently(snapshot_dict: dict[str, Any]) -> tuple[dict[str, dict], dict[str, Any]]:
    """Pull per-column drift + the dataset-level drift count out of an Evidently snapshot dict."""
    per_column: dict[str, dict] = {}
    dataset: dict[str, Any] = {}
    for metric in snapshot_dict.get("metrics", []):
        config = metric.get("config", {})
        mtype = config.get("type", "")
        value = metric.get("value")
        if mtype.endswith("DriftedColumnsCount"):
            dataset = {
                "drifted_columns": int(value["count"]),
                "share": round(float(value["share"]), 4),
                "drift_detected": float(value["share"]) > 0,
            }
        elif mtype.endswith("ValueDrift"):
            score = float(value)
            threshold = float(config.get("threshold", DRIFT_THRESHOLD))
            per_column[config["column"]] = {
                "method": config.get("method", ""),
                "score": score,
                "threshold": threshold,
                # distance metric (see NUM/CAT_METHOD): drifted when the score exceeds threshold
                "drifted": score > threshold,
            }
    return per_column, dataset


def build_drift(reference: pd.DataFrame, current: pd.DataFrame) -> tuple[dict[str, Any], str]:
    """Run Evidently drift + assemble the `drift.json` contract. Returns (contract, html)."""
    # Imported here so importing this module (e.g. for records_to_frame in tests) doesn't pay
    # Evidently's heavy import, and the serving image never drags it in.
    from evidently import DataDefinition, Dataset, Report
    from evidently.presets import DataDriftPreset

    definition = DataDefinition(
        numerical_columns=list(NUMERICAL), categorical_columns=[CATEGORICAL]
    )
    ref_ds = Dataset.from_pandas(reference, data_definition=definition)
    cur_ds = Dataset.from_pandas(current, data_definition=definition)
    preset = DataDriftPreset(
        num_method=NUM_METHOD,
        num_threshold=DRIFT_THRESHOLD,
        cat_method=CAT_METHOD,
        cat_threshold=DRIFT_THRESHOLD,
    )
    snapshot = Report(metrics=[preset]).run(cur_ds, ref_ds)

    per_column, dataset = _parse_evidently(snapshot.dict())

    columns: dict[str, Any] = {}
    for col in NUMERICAL:
        columns[col] = {
            "type": "numerical",
            **per_column.get(col, {}),
            "distribution": _numeric_distribution(reference[col], current[col]),
        }
    columns[CATEGORICAL] = {
        "type": "categorical",
        **per_column.get(CATEGORICAL, {}),
        "distribution": _categorical_distribution(reference[CATEGORICAL], current[CATEGORICAL]),
    }

    contract = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {"n_reference": int(len(reference)), "n_current": int(len(current))},
        "model_sha256": _dominant_model(current),
        "dataset_drift": dataset,
        "columns": columns,
    }
    return contract, snapshot.get_html_str(as_iframe=False)


def _dominant_model(current: pd.DataFrame) -> str:
    """The model most of the current window was served by (drift segments by model, not clock)."""
    shas = current.get("model_sha256")
    if shas is None or shas.empty:
        return ""
    mode = shas.mode()
    return str(mode.iloc[0]) if not mode.empty else ""


def summarize_for_history(contract: dict[str, Any]) -> dict[str, Any]:
    """One compact trend point per run — the 'drift over weeks' series the site plots."""
    conf = contract["columns"]["confidence"]["distribution"]
    return {
        "date": contract["generated_at"][:10],
        "generated_at": contract["generated_at"],
        "drift_share": contract["dataset_drift"].get("share"),
        "drifted_columns": contract["dataset_drift"].get("drifted_columns"),
        "drift_detected": contract["dataset_drift"].get("drift_detected"),
        "n_current": contract["window"]["n_current"],
        "mean_confidence_reference": conf["reference"]["mean"],
        "mean_confidence_current": conf["current"]["mean"],
        "model_sha256": contract.get("model_sha256", ""),
    }


def append_history(
    contract: dict[str, Any], existing: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Append this run's trend point; re-running the same day overwrites (idempotent)."""
    entry = summarize_for_history(contract)
    history = [h for h in existing if h.get("date") != entry["date"]]
    history.append(entry)
    return sorted(history, key=lambda h: h["generated_at"])


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the prediction-drift report.")
    parser.add_argument("--reference", type=Path, default=Path("reports/monitoring/reference.csv"))
    parser.add_argument(
        "--current-dir", type=Path, required=True, help="dir of synced *.jsonl logs"
    )
    parser.add_argument("--out-json", type=Path, default=Path("reports/monitoring/drift.json"))
    parser.add_argument("--out-html", type=Path, default=Path("reports/monitoring/drift.html"))
    parser.add_argument(
        "--history", type=Path, help="drift_history.json to append this run to (read + rewrite)"
    )
    args = parser.parse_args(argv)

    from quickdraw.monitoring.schema import validate_current  # local: keeps import light

    reference = pd.read_csv(args.reference)
    current = validate_current(records_to_frame(read_ndjson_dir(args.current_dir)))
    contract, html = build_drift(reference, current)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(contract, indent=2) + "\n")
    args.out_html.write_text(html)
    if args.history:
        existing = json.loads(args.history.read_text()) if args.history.exists() else []
        args.history.write_text(json.dumps(append_history(contract, existing), indent=2) + "\n")
    drift = contract["dataset_drift"].get("drift_detected")
    print(
        f"drift report: {contract['window']['n_current']} current vs "
        f"{contract['window']['n_reference']} reference rows; drift_detected={drift}"
    )


if __name__ == "__main__":
    main()
