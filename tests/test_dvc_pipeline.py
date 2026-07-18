"""Guard dvc.yaml against drifting from the code it orchestrates.

DVC only discovers a broken stage definition when the stage runs — these tests
catch renamed modules, moved files, or params.yaml keys at test time instead.
"""

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_pipeline() -> dict:
    return yaml.safe_load((REPO_ROOT / "dvc.yaml").read_text())["stages"]


def load_params() -> dict:
    return yaml.safe_load((REPO_ROOT / "params.yaml").read_text())


def out_paths(stage: dict) -> list[str]:
    """Out entries are either plain strings or {path: {flags}} mappings."""
    paths = []
    for section in ("outs", "metrics"):
        for entry in stage.get(section, []):
            paths.append(entry if isinstance(entry, str) else next(iter(entry)))
    return paths


def test_stages_present_in_pipeline_order():
    assert list(load_pipeline()) == ["download", "preprocess", "train", "evaluate", "export"]


def test_every_code_dep_exists():
    for name, stage in load_pipeline().items():
        for dep in stage.get("deps", []):
            if dep.startswith("src/"):
                assert (REPO_ROOT / dep).is_file(), f"stage {name}: missing dep {dep}"


def test_every_cmd_module_resolves():
    for name, stage in load_pipeline().items():
        module = stage["cmd"].split("-m ")[1].split()[0]
        assert importlib.util.find_spec(module), f"stage {name}: cmd module {module} not found"


def test_every_params_key_exists():
    params = load_params()
    for name, stage in load_pipeline().items():
        for key in stage.get("params", []):
            node = params
            for part in key.split("."):
                assert part in node, f"stage {name}: params.yaml has no key {key}"
                node = node[part]


def test_artifact_deps_are_produced_upstream():
    """Every non-code dep must be some earlier stage's out — the DAG has no gaps."""
    produced: set[str] = set()
    for name, stage in load_pipeline().items():
        for dep in stage.get("deps", []):
            if not dep.startswith("src/"):
                assert dep in produced, f"stage {name}: dep {dep} is not produced upstream"
        produced.update(out_paths(stage))
