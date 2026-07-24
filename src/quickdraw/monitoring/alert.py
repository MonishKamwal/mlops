"""Drift alerting-lite (PLAN.md Phase 4, task 4): decide whether to page a human.

The drift report always shows *some* distribution drift — real doodles are out-of-distribution
by construction, so a drift-*presence* alert would fire every week and mean nothing. What's
genuinely worth a GitHub issue is the model doing **badly on real drawings**: confidence
collapsing, or (when there's enough feedback to trust it) proxy accuracy falling below a floor.

Pure decision (`evaluate`) so it's unit-tested; the workflow turns an ``alert: true`` into a
labelled GitHub issue. Thresholds are parameters — tune them without touching the logic.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Thresholds:
    # Alert if the live window's mean top-1 confidence drops below this. The reference runs
    # ~0.90 and a healthy live window ~0.83, so 0.55 flags a real collapse, not normal drift.
    min_confidence: float = 0.55
    # Alert if proxy accuracy falls below this — but only once there are enough verdicts that
    # the number isn't just noise (a handful of 👎 shouldn't page anyone).
    min_accuracy: float = 0.5
    min_feedback: int = 10


def evaluate(
    drift: dict[str, Any], feedback: dict[str, Any] | None, thresholds: Thresholds
) -> dict[str, Any]:
    """Return an alert decision ``{alert, reasons, title, body}`` from the two reports."""
    reasons: list[str] = []

    conf = drift.get("columns", {}).get("confidence", {}).get("distribution", {})
    conf_mean = conf.get("current", {}).get("mean")
    if conf_mean is not None and conf_mean < thresholds.min_confidence:
        reasons.append(
            f"mean confidence on live drawings is {conf_mean:.3f} "
            f"(below the {thresholds.min_confidence} floor)"
        )

    if feedback and feedback.get("accuracy") is not None:
        n = feedback.get("window", {}).get("n", 0)
        acc = feedback["accuracy"]
        if n >= thresholds.min_feedback and acc < thresholds.min_accuracy:
            reasons.append(
                f"proxy accuracy is {acc:.3f} over {n} verdicts "
                f"(below the {thresholds.min_accuracy} floor)"
            )

    date = (drift.get("generated_at") or datetime.now(UTC).isoformat())[:10]
    return {
        "alert": bool(reasons),
        "reasons": reasons,
        "title": f"Drift alert — {date}",
        "body": _body(reasons, drift, feedback, date),
    }


def _body(
    reasons: list[str], drift: dict[str, Any], feedback: dict[str, Any] | None, date: str
) -> str:
    lines = [f"Automated drift alert from the weekly report ({date}).", "", "**Why:**"]
    lines += [f"- {r}" for r in reasons]
    ds = drift.get("dataset_drift", {})
    lines += [
        "",
        "**Context:**",
        f"- dataset drift: {ds.get('drifted_columns', '?')} columns drifted "
        f"(share {ds.get('share', '?')})",
    ]
    if feedback and feedback.get("accuracy") is not None:
        w = feedback.get("window", {})
        lines.append(f"- proxy accuracy: {feedback['accuracy']} over {w.get('n', 0)} verdicts")
    lines += [
        "",
        "See the evidence hub (`/mlops/drift.json`, `/mlops/feedback.json`) for the full report.",
    ]
    return "\n".join(lines)


def _load(path: Path | None) -> dict[str, Any] | None:
    if path and path.exists():
        return json.loads(path.read_text())
    return None


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Decide whether to open a drift alert.")
    parser.add_argument("--drift", type=Path, required=True)
    parser.add_argument("--feedback", type=Path, help="feedback.json (optional)")
    parser.add_argument("--out", type=Path, default=Path("alert.json"))
    parser.add_argument("--min-confidence", type=float, default=Thresholds.min_confidence)
    parser.add_argument("--min-accuracy", type=float, default=Thresholds.min_accuracy)
    parser.add_argument("--min-feedback", type=int, default=Thresholds.min_feedback)
    args = parser.parse_args(argv)

    drift = _load(args.drift)
    if drift is None:
        raise SystemExit(f"drift report not found: {args.drift}")
    thresholds = Thresholds(args.min_confidence, args.min_accuracy, args.min_feedback)
    decision = evaluate(drift, _load(args.feedback), thresholds)

    args.out.write_text(json.dumps(decision, indent=2) + "\n")
    print(f"alert={str(decision['alert']).lower()} reasons={len(decision['reasons'])}")


if __name__ == "__main__":
    main()
