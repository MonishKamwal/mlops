import numpy as np
import pytest

from quickdraw.monitoring.reference import summarize_predictions


def test_summarize_extracts_label_confidence_and_margin() -> None:
    probs = np.array([[0.7, 0.2, 0.1], [0.1, 0.6, 0.3]])
    df = summarize_predictions(probs, ["cat", "dog", "fish"])

    assert list(df["predicted_label"]) == ["cat", "dog"]
    assert df["confidence"].tolist() == [0.7, 0.6]
    # margin = top1 - top2: (0.7-0.2)=0.5 and (0.6-0.3)=0.3
    assert df["margin"].tolist() == [pytest.approx(0.5), pytest.approx(0.3)]


def test_summarize_columns_and_row_count() -> None:
    rng = np.random.default_rng(0)
    probs = rng.dirichlet(np.ones(4), size=25)  # 25 valid probability rows
    df = summarize_predictions(probs, ["a", "b", "c", "d"])

    assert list(df.columns) == ["predicted_label", "confidence", "margin"]
    assert len(df) == 25
    assert ((df["confidence"] >= 0) & (df["confidence"] <= 1)).all()
    assert (df["margin"] >= 0).all()  # top1 is never below top2
