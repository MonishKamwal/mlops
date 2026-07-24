"""Proxy-accuracy from user feedback (PLAN.md Phase 4, task 3).

The canvas logs a 👍/👎 per prediction (``feedback/dt=…/`` in the logs bucket via
:mod:`quickdraw.serving.feedback_log`). This turns a window of those verdicts into
**proxy accuracy** — the fraction the model got right on *real* drawings — overall, per
class, and by input source, plus a week-over-week trend. Emits ``feedback.json``, a
styling-agnostic data contract the portfolio site renders (like ``drift.json``).

Unlike drift, an empty window is normal (feedback is sparse — most visitors don't click),
so a window with no records is not an error: it produces a contract with ``n = 0`` and
``accuracy = null`` rather than raising.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from quickdraw.monitoring.drift import read_ndjson_dir

COLUMNS = ["predicted_label", "correct", "confidence", "source", "model_sha256"]


def records_to_frame(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Feedback-log JSONL records -> the proxy-accuracy dataframe. Pure."""
    rows = [
        {
            "predicted_label": rec.get("predicted_label", ""),
            "correct": bool(rec["correct"]),
            "confidence": float(rec.get("confidence", 0.0)),
            "source": rec.get("source", "unknown"),
            "model_sha256": rec.get("model_sha256", ""),
        }
        for rec in records
        if "correct" in rec
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def _accuracy(frame: pd.DataFrame) -> float | None:
    """Fraction correct, or None when the frame is empty (no verdicts to average)."""
    return round(float(frame["correct"].mean()), 4) if len(frame) else None


def _grouped(frame: pd.DataFrame, by: str) -> list[dict[str, Any]]:
    """Per-group n / correct / accuracy, sorted by sample size then name."""
    out = []
    for key, group in frame.groupby(by):
        out.append(
            {
                by: str(key),
                "n": int(len(group)),
                "n_correct": int(group["correct"].sum()),
                "accuracy": _accuracy(group),
            }
        )
    return sorted(out, key=lambda g: (-g["n"], g[by]))


def build_feedback(frame: pd.DataFrame) -> dict[str, Any]:
    """Assemble the ``feedback.json`` contract from a window of feedback rows."""
    model = ""
    if len(frame) and frame["model_sha256"].notna().any():
        mode = frame["model_sha256"].mode()
        model = str(mode.iloc[0]) if len(mode) else ""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {
            "n": int(len(frame)),
            "n_correct": int(frame["correct"].sum()) if len(frame) else 0,
        },
        "accuracy": _accuracy(frame),
        "by_class": _grouped(frame, "predicted_label"),
        "by_source": _grouped(frame, "source"),
        "model_sha256": model,
    }


def summarize_for_history(contract: dict[str, Any]) -> dict[str, Any]:
    """One compact trend point per run — the proxy-accuracy-over-weeks series."""
    return {
        "date": contract["generated_at"][:10],
        "generated_at": contract["generated_at"],
        "n": contract["window"]["n"],
        "n_correct": contract["window"]["n_correct"],
        "accuracy": contract["accuracy"],
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
    parser = argparse.ArgumentParser(description="Build the proxy-accuracy report from feedback.")
    parser.add_argument(
        "--current-dir", type=Path, required=True, help="dir of synced feedback *.jsonl logs"
    )
    parser.add_argument("--out-json", type=Path, default=Path("reports/monitoring/feedback.json"))
    parser.add_argument(
        "--history", type=Path, help="feedback_history.json to append this run to (read + rewrite)"
    )
    args = parser.parse_args(argv)

    frame = records_to_frame(read_ndjson_dir(args.current_dir))
    contract = build_feedback(frame)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(contract, indent=2) + "\n")
    if args.history:
        existing = json.loads(args.history.read_text()) if args.history.exists() else []
        args.history.write_text(json.dumps(append_history(contract, existing), indent=2) + "\n")
    print(
        f"feedback report: {contract['window']['n']} verdicts, "
        f"accuracy={contract['accuracy']} ({contract['window']['n_correct']} correct)"
    )


if __name__ == "__main__":
    main()
