import json

from quickdraw.monitoring.feedback import (
    append_history,
    build_feedback,
    records_to_frame,
)


def _rec(label: str, correct: bool, source: str = "strokes", conf: float = 0.8) -> dict:
    return {
        "predicted_label": label,
        "correct": correct,
        "confidence": conf,
        "source": source,
        "model_sha256": "abc",
    }


def test_overall_and_per_class_accuracy() -> None:
    frame = records_to_frame(
        [
            _rec("cat", True),
            _rec("cat", True),
            _rec("cat", False),
            _rec("dog", False),
        ]
    )
    contract = build_feedback(frame)

    assert contract["window"] == {"n": 4, "n_correct": 2}
    assert contract["accuracy"] == 0.5
    assert contract["model_sha256"] == "abc"
    by_class = {c["predicted_label"]: c for c in contract["by_class"]}
    assert by_class["cat"] == {
        "predicted_label": "cat",
        "n": 3,
        "n_correct": 2,
        "accuracy": round(2 / 3, 4),
    }
    assert by_class["dog"]["accuracy"] == 0.0
    # sorted by sample size desc — cat (3) before dog (1)
    assert [c["predicted_label"] for c in contract["by_class"]] == ["cat", "dog"]


def test_by_source_split() -> None:
    frame = records_to_frame([_rec("cat", True, "strokes"), _rec("cat", False, "png")])
    by_source = {s["source"]: s for s in build_feedback(frame)["by_source"]}
    assert by_source["strokes"]["accuracy"] == 1.0
    assert by_source["png"]["accuracy"] == 0.0


def test_empty_window_is_not_an_error() -> None:
    # Feedback is sparse — a window with no verdicts must produce n=0/accuracy=None,
    # not raise (else a quiet week would fail the whole drift workflow).
    contract = build_feedback(records_to_frame([]))
    assert contract["window"] == {"n": 0, "n_correct": 0}
    assert contract["accuracy"] is None
    assert contract["by_class"] == []


def test_records_without_correct_are_skipped() -> None:
    frame = records_to_frame([_rec("cat", True), {"predicted_label": "dog"}])
    assert len(frame) == 1


def test_append_history_adds_and_overwrites_same_day() -> None:
    c1 = {
        "generated_at": "2026-07-20T12:00:00+00:00",
        "window": {"n": 4, "n_correct": 2},
        "accuracy": 0.5,
        "model_sha256": "abc",
    }
    c2 = {
        "generated_at": "2026-07-27T12:00:00+00:00",
        "window": {"n": 10, "n_correct": 9},
        "accuracy": 0.9,
        "model_sha256": "abc",
    }
    hist = append_history(c2, append_history(c1, []))
    assert [h["date"] for h in hist] == ["2026-07-20", "2026-07-27"]
    assert hist[-1]["accuracy"] == 0.9

    # same date replaces, not duplicates
    c2b = {**c2, "accuracy": 0.95, "window": {"n": 12, "n_correct": 11}}
    updated = append_history(c2b, hist)
    assert len(updated) == 2
    assert updated[-1]["accuracy"] == 0.95


def test_main_writes_json_and_history(tmp_path) -> None:
    logs = tmp_path / "feedback" / "dt=2026-07-23"
    logs.mkdir(parents=True)
    (logs / "a.jsonl").write_text(
        "\n".join(json.dumps(_rec("cat", i % 2 == 0)) for i in range(4)) + "\n"
    )
    out = tmp_path / "feedback.json"
    hist = tmp_path / "feedback_history.json"

    from quickdraw.monitoring.feedback import main

    main(
        [
            "--current-dir",
            str(tmp_path / "feedback"),
            "--out-json",
            str(out),
            "--history",
            str(hist),
        ]
    )

    contract = json.loads(out.read_text())
    assert contract["window"]["n"] == 4
    assert len(json.loads(hist.read_text())) == 1
