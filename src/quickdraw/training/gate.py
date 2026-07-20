"""Quality gate: the challenger ships only if it doesn't regress on the champion.

``champion`` is the best model validated so far — the quality *high-water mark*, not
whatever shipped last; ``challenger`` is what training just produced (``registry.py``
owns both aliases). This gate reads each alias's *test* accuracy from its MLflow run
and applies two rules from the ``gate:`` section of params.yaml:

* **Absolute floor** — the challenger must clear ``min_test_accuracy`` outright. A
  model that is merely "better than a bad champion" still isn't good enough to serve.
* **No regression beyond epsilon** — the challenger must stay within ``epsilon`` of
  the champion (``challenger >= champion - epsilon``). A sub-epsilon dip is seed
  noise, not a real regression, so this keeps byte-level nondeterminism from
  blocking every deploy.

Pass → the process exits 0 and the deploy step ships the challenger. Fail → a
metric-diff summary is printed (and appended to ``$GITHUB_STEP_SUMMARY`` when set)
and the process exits non-zero, failing the workflow before anything ships. A
deliberately-bad model blocked here is the CI/CD demo (PLAN.md §5 Phase 2 task 4).

**Deploy and re-crown are decoupled.** Passing means "ship this challenger"; the
champion alias only moves on a *strict* improvement. So the gate's baseline is the
best model ever validated and can never ratchet down — a within-epsilon challenger
may deploy, but it does not lower the bar the next challenger must clear. The
trade-off, taken deliberately: the live model may sit up to epsilon below champion,
and no alias tracks "currently deployed" (the workflow's built image is the source
of truth for what's live; champion is the quality reference).

This is a CLI run *after* ``dvc repro``, not a DVC stage: it mutates registry state
(the champion alias) and reads it from S3-synced MLflow — neither is a pure,
file-hashable pipeline step.

Usage: ``uv run python -m quickdraw.training.gate``
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from quickdraw.config import GateParams, load_gate_params
from quickdraw.training.registry import (
    CHALLENGER,
    CHAMPION,
    DEFAULT_TRACKING_URI,
    alias_test_accuracy,
    promote_to_champion,
)


@dataclass(frozen=True)
class GateDecision:
    """The gate's verdict, carrying everything the summary and exit code need."""

    passed: bool  # cleared to deploy?
    promoted: bool  # did the challenger re-crown champion (a strict improvement)?
    champion_version: str
    challenger_version: str
    champion_accuracy: float
    challenger_accuracy: float
    params: GateParams
    reasons: tuple[str, ...]  # why it failed; empty on pass


def decide(
    champion_accuracy: float, challenger_accuracy: float, params: GateParams
) -> tuple[bool, tuple[str, ...]]:
    """Apply the floor and no-regression rules; return ``(passed, failure_reasons)``.

    Pure and side-effect-free so the policy is trivially unit-testable in isolation
    from MLflow. Both rules are checked so the summary can report every reason at once.
    """
    reasons: list[str] = []
    if challenger_accuracy < params.min_test_accuracy:
        reasons.append(
            f"below floor: test_accuracy {challenger_accuracy:.4f} < {params.min_test_accuracy:.4f}"
        )
    if challenger_accuracy < champion_accuracy - params.epsilon:
        reasons.append(
            f"regression: test_accuracy {challenger_accuracy:.4f} < champion "
            f"{champion_accuracy:.4f} - epsilon {params.epsilon:.4f}"
        )
    return not reasons, tuple(reasons)


def run_gate(tracking_uri: str, params: GateParams) -> GateDecision:
    """Read both aliases, decide whether to ship, and re-crown on a strict improvement.

    The champion is read *before* any promotion, so the decision records the model
    that held the bar going in — the summary compares against that, not against itself.
    Re-crowning is deliberately narrower than passing: a challenger deploys whenever it
    passes, but only *beats* the champion (``>``) moves the alias, so the quality bar
    tracks the best model ever validated and never ratchets down (module docstring).
    """
    champion_version, champion_accuracy = alias_test_accuracy(CHAMPION, tracking_uri)
    challenger_version, challenger_accuracy = alias_test_accuracy(CHALLENGER, tracking_uri)
    passed, reasons = decide(champion_accuracy, challenger_accuracy, params)
    promoted = passed and challenger_accuracy > champion_accuracy
    if promoted:
        promote_to_champion(challenger_version, tracking_uri)
    return GateDecision(
        passed=passed,
        promoted=promoted,
        champion_version=champion_version,
        challenger_version=challenger_version,
        champion_accuracy=champion_accuracy,
        challenger_accuracy=challenger_accuracy,
        params=params,
        reasons=reasons,
    )


def format_summary(decision: GateDecision) -> str:
    """Markdown metric-diff summary — readable on stdout and as a GitHub job summary."""
    delta = decision.challenger_accuracy - decision.champion_accuracy
    verdict = "PASS" if decision.passed else "FAIL"
    lines = [
        f"## Quality gate: {verdict}",
        "",
        f"- champion (v{decision.champion_version}): "
        f"test_accuracy = {decision.champion_accuracy:.4f}",
        f"- challenger (v{decision.challenger_version}): "
        f"test_accuracy = {decision.challenger_accuracy:.4f}",
        f"- delta: {delta:+.4f} (epsilon = {decision.params.epsilon:.4f}, "
        f"floor = {decision.params.min_test_accuracy:.4f})",
    ]
    if decision.passed and decision.promoted:
        lines.append(
            f"- ships; promoted challenger v{decision.challenger_version} to champion (new best)"
        )
    elif decision.passed:
        lines.append(
            f"- ships challenger v{decision.challenger_version}; champion "
            f"v{decision.champion_version} unchanged (within epsilon, not a new best)"
        )
    else:
        lines.append("- blocked; champion unchanged:")
        lines += [f"  - {reason}" for reason in decision.reasons]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate the challenger against the champion.")
    parser.add_argument("--params", type=Path, default=Path("params.yaml"))
    parser.add_argument("--tracking-uri", default=DEFAULT_TRACKING_URI)
    args = parser.parse_args(argv)

    decision = run_gate(args.tracking_uri, load_gate_params(args.params))
    summary = format_summary(decision)
    print(summary, end="")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as handle:
            handle.write(summary)

    return 0 if decision.passed else 1


if __name__ == "__main__":
    sys.exit(main())
