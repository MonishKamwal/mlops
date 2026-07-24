import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FakeS3Client
from fastapi.testclient import TestClient

from quickdraw.serving.app import create_app
from quickdraw.serving.capture_log import CaptureLog, capture_log_from_env
from quickdraw.serving.feedback_log import FeedbackLog

STROKES = [[[10.0, 20.0, 30.0], [10.0, 15.0, 20.0]]]


@pytest.fixture
def capture_client(
    onnx_model_path: Path, serving_classes: list[str]
) -> Iterator[tuple[TestClient, FakeS3Client, FakeS3Client, list[str]]]:
    fb, cap = FakeS3Client(), FakeS3Client()
    app = create_app(
        onnx_model_path,
        feedback_log=FeedbackLog("fb-bucket", client=fb),
        capture_log=CaptureLog("cap-bucket", client=cap),
    )
    with TestClient(app) as client:
        yield client, fb, cap, serving_classes


def _body(label: str, **over) -> dict:
    return {
        "predicted_label": label,
        "confidence": 0.83,
        "correct": True,
        "source": "strokes",
        **over,
    }


def test_thumbs_up_with_strokes_captures_labeled_by_guess(capture_client) -> None:
    client, fb, cap, classes = capture_client
    r = client.post("/feedback", json=_body(classes[0], strokes=STROKES))
    assert r.status_code == 204
    assert len(fb.puts) == 1  # verdict still logged
    assert len(cap.puts) == 1
    record = json.loads(cap.puts[0]["Body"])
    assert record["label"] == classes[0]  # 👍 -> label is the guess
    assert record["predicted_label"] == classes[0]
    assert record["strokes"] == STROKES
    assert cap.puts[0]["Key"].startswith("captures/dt=")


def test_thumbs_down_uses_the_correction_as_label(capture_client) -> None:
    client, fb, cap, classes = capture_client
    body = _body(classes[0], correct=False, true_label=classes[1], strokes=STROKES)
    assert client.post("/feedback", json=body).status_code == 204
    record = json.loads(cap.puts[0]["Body"])
    assert record["label"] == classes[1]  # 👎 -> label is the user's correction
    assert record["predicted_label"] == classes[0]
    assert record["correct"] is False


def test_thumbs_down_without_correction_logs_verdict_but_no_capture(capture_client) -> None:
    client, fb, cap, classes = capture_client
    body = _body(classes[0], correct=False, strokes=STROKES)  # no true_label
    assert client.post("/feedback", json=body).status_code == 204
    assert len(fb.puts) == 1  # verdict logged
    assert cap.puts == []  # unlabeled error is not captured


def test_no_strokes_means_no_capture(capture_client) -> None:
    client, fb, cap, classes = capture_client
    assert client.post("/feedback", json=_body(classes[0])).status_code == 204
    assert len(fb.puts) == 1
    assert cap.puts == []


def test_invalid_correction_label_is_rejected(capture_client) -> None:
    client, fb, cap, classes = capture_client
    body = _body(classes[0], correct=False, true_label="not-a-class", strokes=STROKES)
    assert client.post("/feedback", json=body).status_code == 404


def test_capture_is_fail_open(onnx_model_path: Path, serving_classes: list[str]) -> None:
    # A capture-write outage costs a training example, never the request.
    app = create_app(
        onnx_model_path,
        feedback_log=FeedbackLog("fb", client=FakeS3Client()),
        capture_log=CaptureLog("cap", client=FakeS3Client(fail=True)),
    )
    with TestClient(app) as client:
        body = _body(serving_classes[0], strokes=STROKES)
        assert client.post("/feedback", json=body).status_code == 204


def test_capture_disabled_still_accepts_feedback(
    onnx_model_path: Path, serving_classes: list[str]
) -> None:
    # feedback on, capture off (no capture_log) — request still succeeds.
    app = create_app(onnx_model_path, feedback_log=FeedbackLog("fb", client=FakeS3Client()))
    with TestClient(app) as client:
        body = _body(serving_classes[0], strokes=STROKES)
        assert client.post("/feedback", json=body).status_code == 204


def test_capture_log_from_env_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PREDICTION_LOG_BUCKET", raising=False)
    assert capture_log_from_env() is None
    monkeypatch.setenv("PREDICTION_LOG_BUCKET", "bkt")
    log = capture_log_from_env()
    assert isinstance(log, CaptureLog) and log.bucket == "bkt"


def test_capture_log_record_shape() -> None:
    fake = FakeS3Client()
    CaptureLog("b", client=fake).log(
        strokes=STROKES,
        label="cat",
        predicted_label="dog",
        correct=False,
        confidence=0.4,
        model_sha256="abc",
        service_version="9.9",
    )
    (put,) = fake.puts
    assert put["ContentType"] == "application/x-ndjson"
    record = json.loads(put["Body"])
    assert set(record) == {
        "ts",
        "strokes",
        "label",
        "predicted_label",
        "correct",
        "confidence",
        "model_sha256",
        "service_version",
    }
