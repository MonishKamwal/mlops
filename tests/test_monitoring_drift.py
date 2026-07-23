import json

import numpy as np
import pandas as pd
import pytest

from quickdraw.monitoring.drift import build_drift, read_ndjson_dir, records_to_frame
from quickdraw.monitoring.schema import validate_current


def _record(label: str, p1: float, p2: float, source: str = "strokes") -> dict:
    return {
        "top3": [{"label": label, "probability": p1}, {"label": "dog", "probability": p2}],
        "source": source,
        "model_sha256": "abc123",
    }


def test_records_to_frame_extracts_columns_and_margin() -> None:
    df = records_to_frame([_record("cat", 0.9, 0.05), _record("fish", 0.6, 0.3)])

    assert list(df["predicted_label"]) == ["cat", "fish"]
    assert df["confidence"].tolist() == [0.9, 0.6]
    assert df["margin"].tolist() == [pytest.approx(0.85), pytest.approx(0.3)]
    assert list(df["source"]) == ["strokes", "strokes"]


def test_records_to_frame_skips_empty_top3() -> None:
    df = records_to_frame([{"top3": [], "source": "png"}, _record("cat", 0.8, 0.1)])
    assert len(df) == 1


def test_read_ndjson_dir(tmp_path) -> None:
    (tmp_path / "dt=2026-07-20").mkdir()
    (tmp_path / "dt=2026-07-20" / "a.jsonl").write_text(json.dumps(_record("cat", 0.8, 0.1)) + "\n")
    (tmp_path / "b.jsonl").write_text(json.dumps(_record("dog", 0.7, 0.2)) + "\n")

    records = read_ndjson_dir(tmp_path)
    assert len(records) == 2


def test_validate_current_rejects_empty() -> None:
    with pytest.raises(ValueError, match="nothing to compare"):
        validate_current(
            pd.DataFrame(columns=["predicted_label", "confidence", "margin", "source"])
        )


def test_validate_current_rejects_out_of_range_confidence(tmp_path) -> None:
    import pandera.errors

    df = records_to_frame([_record("cat", 1.5, 0.1)])  # confidence > 1
    with pytest.raises(pandera.errors.SchemaError):
        validate_current(df)


def test_build_drift_contract_shape_and_detects_shift() -> None:
    rng = np.random.default_rng(0)
    classes = ["cat", "dog", "fish"]
    # reference: high confidence (test set); current: much lower (real doodles) -> should drift
    reference = pd.DataFrame(
        {
            "predicted_label": rng.choice(classes, 400),
            "confidence": rng.beta(9, 1, 400),
            "margin": rng.beta(7, 1, 400),
        }
    )
    current = pd.DataFrame(
        {
            "predicted_label": rng.choice(classes, 200),
            "confidence": rng.beta(2, 2, 200),
            "margin": rng.beta(2, 3, 200),
            "model_sha256": ["abc"] * 200,
        }
    )

    contract, html = build_drift(reference, current)

    assert set(contract) == {"generated_at", "window", "model_sha256", "dataset_drift", "columns"}
    assert contract["window"] == {"n_reference": 400, "n_current": 200}
    assert contract["model_sha256"] == "abc"
    assert contract["dataset_drift"]["drift_detected"] is True
    # confidence dropped hard -> that column must be flagged, with a distribution attached
    conf = contract["columns"]["confidence"]
    assert conf["drifted"] is True
    assert conf["distribution"]["reference"]["mean"] > conf["distribution"]["current"]["mean"]
    assert len(conf["distribution"]["bin_edges"]) == 21  # 20 bins
    assert contract["columns"]["predicted_label"]["type"] == "categorical"
    assert "<html" in html.lower()
