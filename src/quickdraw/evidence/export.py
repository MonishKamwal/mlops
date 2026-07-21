"""Render the static evidence hub (PLAN.md §5 Phase 2, task 6).

The hub reads the MLflow **registry** as the source of truth for what is champion and
its metrics — not the git-tracked eval snapshot, which is one machine's artifact and
drifts from the CI-trained champion (e.g. laptop v1 @ 0.9157 vs CI v2 @ 0.9170). It
emits a self-contained static site: the champion header, the experiment/runs table,
per-class F1 and accuracy-history charts, the confusion matrix, and the gate policy
with a link to a real blocked-gate CI run. Runs in CI (evidence-pages.yml) against the
S3-synced DB, and locally against a throwaway or pulled ``mlflow.db``.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import markdown
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader, select_autoescape
from mlflow.tracking import MlflowClient

from quickdraw.config import GateParams, load_gate_params
from quickdraw.training.registry import (
    CHALLENGER,
    CHAMPION,
    DEFAULT_TRACKING_URI,
    MLFLOW_EXPERIMENT,
    MODEL_NAME,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
EVAL_METRICS_PATH = Path("reports/eval/metrics.json")
CONFUSION_MATRIX_PATH = Path("reports/eval/confusion_matrix.png")
MODEL_CARD_PATH = Path("MODEL_CARD.md")
REPO_SLUG = "MonishKamwal/mlops"
# A real run where the gate blocked a deliberately crippled challenger (Phase 2 DoD).
BLOCKED_GATE_RUN_URL = f"https://github.com/{REPO_SLUG}/actions/runs/29757829275"
# Matched to plotly>=5.24 (emits plotly.js 2.x figures); loaded once in the template.
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


@dataclass(frozen=True)
class RunRow:
    """One MLflow run, flattened for the experiment table."""

    run_id: str
    started: str
    epochs: int | None
    learning_rate: float | None
    val_accuracy: float | None
    test_accuracy: float | None
    macro_f1: float | None
    version: str | None
    alias: str | None


def resolve_tracking_uri(explicit: str | None = None) -> str:
    return explicit or DEFAULT_TRACKING_URI


def _as_int(value: str | None) -> int | None:
    try:
        return int(value)  # MLflow stores params as strings
    except (TypeError, ValueError):
        return None


def _as_float(value: str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aliases_by_version(client: MlflowClient) -> dict[str, list[str]]:
    """version -> [alias, ...] for whichever of champion/challenger currently resolve.

    A single version can hold both aliases (the bootstrap run), so this is a list —
    collapsing to one would hide that champion and challenger are the same model.
    """
    out: dict[str, list[str]] = {}
    for alias in (CHAMPION, CHALLENGER):
        try:
            version = client.get_model_version_by_alias(MODEL_NAME, alias)
        except Exception:
            continue  # alias not set yet (e.g. a fresh DB) — simply absent from the map
        out.setdefault(str(version.version), []).append(alias)
    return out


def _version_by_run(client: MlflowClient) -> dict[str, str]:
    """run_id -> registry version, for runs that got registered as a model version.

    MLflow types ``version`` as an int; we stringify so it keys cleanly against the
    alias map and renders as ``v2`` rather than a bare number.
    """
    return {v.run_id: str(v.version) for v in client.search_model_versions(f"name='{MODEL_NAME}'")}


def gather_runs(client: MlflowClient) -> list[RunRow]:
    """Every run in the experiment, newest first, joined to its registry version/alias."""
    experiment = client.get_experiment_by_name(MLFLOW_EXPERIMENT)
    if experiment is None:
        return []
    version_by_run = _version_by_run(client)
    aliases_by_version = _aliases_by_version(client)
    rows: list[RunRow] = []
    for run in client.search_runs(
        [experiment.experiment_id], order_by=["attributes.start_time DESC"]
    ):
        metrics = run.data.metrics
        params = run.data.params
        version = version_by_run.get(run.info.run_id)
        aliases = aliases_by_version.get(version, []) if version else []
        rows.append(
            RunRow(
                run_id=run.info.run_id,
                started=datetime.fromtimestamp(run.info.start_time / 1000, UTC).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                epochs=_as_int(params.get("epochs")),
                learning_rate=_as_float(params.get("learning_rate")),
                val_accuracy=metrics.get("best_val_accuracy"),
                test_accuracy=metrics.get("test_accuracy"),
                macro_f1=metrics.get("macro_f1"),
                version=version,
                alias=" / ".join(aliases) if aliases else None,
            )
        )
    return rows


def load_eval_metrics(path: Path | None = None) -> dict | None:
    """The git-tracked eval snapshot (per-class table + confusion-matrix context)."""
    path = path or EVAL_METRICS_PATH  # resolve at call time so the module const stays patchable
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_model_card_html(path: Path | None = None) -> str | None:
    """Render MODEL_CARD.md to HTML for the hub. The raw ``.md`` is also copied to the
    site (render()), so the portfolio can consume the source instead of this markup."""
    path = path or MODEL_CARD_PATH
    if not path.exists():
        return None
    return markdown.markdown(path.read_text(), extensions=["tables", "fenced_code", "sane_lists"])


def _base_layout(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title=title,
        template="plotly_white",
        margin={"l": 90, "r": 20, "t": 50, "b": 40},
        font={"family": "system-ui, sans-serif"},
        colorway=["#4f46e5"],
    )
    return fig


def f1_bar_chart(per_class: dict) -> str:
    """Horizontal per-class F1 bars, worst at the bottom — where errors concentrate."""
    ordered = sorted(per_class, key=lambda c: per_class[c]["f1"])
    fig = go.Figure(
        go.Bar(
            x=[per_class[c]["f1"] for c in ordered],
            y=ordered,
            orientation="h",
            marker_color="#4f46e5",
        )
    )
    _base_layout(fig, "Per-class F1")
    fig.update_xaxes(range=[0, 1])
    fig.update_layout(height=460)
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id="f1-chart")


def accuracy_history_chart(runs: list[RunRow]) -> str:
    """Test accuracy across runs in chronological order — the experiment's trajectory."""
    scored = [r for r in reversed(runs) if r.test_accuracy is not None]
    labels = [f"v{r.version}" if r.version else r.run_id[:8] for r in scored]
    fig = go.Figure(
        go.Scatter(
            x=labels,
            y=[r.test_accuracy for r in scored],
            mode="lines+markers",
            marker={"size": 10, "color": "#4f46e5"},
            line={"color": "#4f46e5"},
        )
    )
    _base_layout(fig, "Test accuracy over runs")
    fig.update_layout(height=360)
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id="acc-chart")


def build_data(runs: list[RunRow], eval_metrics: dict | None, gate_params: GateParams) -> dict:
    """The hub's content as plain JSON — the contract the portfolio site consumes to
    render its own styled components later. Deliberately free of any presentation (no
    HTML, no chart markup), so data and styling evolve independently. Runs and champion
    are dicts (not ``RunRow``) precisely so this round-trips through ``json.dumps``."""
    run_dicts = [asdict(r) for r in runs]
    champion = next((r for r in run_dicts if r["alias"] and CHAMPION in r["alias"]), None)
    per_class = (eval_metrics or {}).get("per_class", {})
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "model_name": MODEL_NAME,
        "repo_slug": REPO_SLUG,
        "champion": champion,
        "runs": run_dicts,
        "per_class": [{"cls": c, **per_class[c]} for c in per_class],
        "gate": {
            "min_test_accuracy": gate_params.min_test_accuracy,
            "epsilon": gate_params.epsilon,
        },
        "blocked_gate_run_url": BLOCKED_GATE_RUN_URL,
    }


# Keys layered on top of build_data for the HTML — stripped back out to recover the JSON
# data contract, so evidence.json and index.html are always rendered from the same data.
# (The model card also ships as raw MODEL_CARD.md, so its rendered HTML isn't data either.)
_PRESENTATION_KEYS = frozenset(
    {"plotly_cdn", "f1_chart", "accuracy_chart", "has_confusion_matrix", "model_card_html"}
)


def build_context(runs: list[RunRow], eval_metrics: dict | None, gate_params: GateParams) -> dict:
    """The Jinja context: the JSON data plus presentation-only extras (chart HTML, the
    plotly CDN, and the confusion-matrix flag render() flips once the PNG is copied in).
    Jinja resolves ``champion.version`` etc. against the dicts via its item fallback."""
    per_class = (eval_metrics or {}).get("per_class", {})
    return {
        **build_data(runs, eval_metrics, gate_params),
        "plotly_cdn": PLOTLY_CDN,
        "f1_chart": f1_bar_chart(per_class) if per_class else None,
        "accuracy_chart": (
            accuracy_history_chart(runs) if any(r.test_accuracy for r in runs) else None
        ),
        "has_confusion_matrix": False,
    }


def render(
    out_dir: Path | str,
    tracking_uri: str | None = None,
    gate_params: GateParams | None = None,
) -> Path:
    """Read the registry + eval snapshot and write the static site into ``out_dir``:
    ``index.html`` (the standalone hub), ``evidence.json`` (the data contract for the
    portfolio site), ``style.css``, ``MODEL_CARD.md``, and the confusion matrix when
    present."""
    client = MlflowClient(tracking_uri=resolve_tracking_uri(tracking_uri))
    runs = gather_runs(client)
    context = build_context(runs, load_eval_metrics(), gate_params or load_gate_params())
    context["model_card_html"] = load_model_card_html()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATES_DIR / "style.css", out_dir / "style.css")
    if CONFUSION_MATRIX_PATH.exists():
        shutil.copy(CONFUSION_MATRIX_PATH, out_dir / "confusion_matrix.png")
        context["has_confusion_matrix"] = True
    if MODEL_CARD_PATH.exists():
        shutil.copy(MODEL_CARD_PATH, out_dir / "MODEL_CARD.md")  # raw source for the portfolio

    data = {k: v for k, v in context.items() if k not in _PRESENTATION_KEYS}
    (out_dir / "evidence.json").write_text(json.dumps(data, indent=2))

    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"])
    )
    index = out_dir / "index.html"
    index.write_text(env.get_template("index.html.j2").render(**context))
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the static evidence hub.")
    parser.add_argument("--out", default="_site", help="output directory (default: _site)")
    parser.add_argument("--tracking-uri", default=None, help="MLflow tracking URI override")
    args = parser.parse_args(argv)
    index = render(args.out, tracking_uri=args.tracking_uri)
    print(f"wrote {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
