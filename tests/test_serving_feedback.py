import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FakeS3Client
from fastapi.testclient import TestClient

from quickdraw.serving.app import create_app
from quickdraw.serving.feedback_log import FeedbackLog, feedback_log_from_env


@pytest.fixture
def feedback_client(
    onnx_model_path: Path, serving_classes: list[str]
) -> Iterator[tuple[TestClient, FakeS3Client, list[str]]]:
    fake = FakeS3Client()
    app = create_app(onnx_model_path, feedback_log=FeedbackLog("test-bucket", client=fake))
    with TestClient(app) as client:
        yield client, fake, serving_classes


def _body(label: str) -> dict:
    return {"predicted_label": label, "confidence": 0.83, "correct": True, "source": "strokes"}


def test_feedback_accepts_and_logs_one_record(feedback_client) -> None:
    client, fake, classes = feedback_client
    response = client.post("/feedback", json=_body(classes[0]))

    assert response.status_code == 204
    assert len(fake.puts) == 1
    record = json.loads(fake.puts[0]["Body"])
    assert record["predicted_label"] == classes[0]
    assert record["correct"] is True
    assert record["confidence"] == 0.83
    assert fake.puts[0]["Key"].startswith("feedback/dt=")


def test_feedback_records_a_downvote(feedback_client) -> None:
    client, fake, classes = feedback_client
    body = _body(classes[0]) | {"correct": False}
    assert client.post("/feedback", json=body).status_code == 204
    assert json.loads(fake.puts[0]["Body"])["correct"] is False


def test_feedback_rejects_unknown_label(feedback_client) -> None:
    client, fake, _ = feedback_client
    response = client.post("/feedback", json=_body("not-a-real-class"))
    assert response.status_code == 404
    assert fake.puts == []  # bad label never reaches the log


def test_feedback_rejects_out_of_range_confidence(feedback_client) -> None:
    client, fake, classes = feedback_client
    body = _body(classes[0]) | {"confidence": 1.5}
    assert client.post("/feedback", json=body).status_code == 422
    assert fake.puts == []


def test_feedback_is_fail_open(onnx_model_path: Path, serving_classes: list[str]) -> None:
    # An S3 outage costs a feedback line, never the request (mirrors prediction logging).
    log = FeedbackLog("test-bucket", client=FakeS3Client(fail=True))
    with TestClient(create_app(onnx_model_path, feedback_log=log)) as client:
        assert client.post("/feedback", json=_body(serving_classes[0])).status_code == 204


def test_feedback_disabled_still_accepts(onnx_model_path: Path, serving_classes: list[str]) -> None:
    # No configured log (feedback disabled) — the endpoint still succeeds, just doesn't persist.
    with TestClient(create_app(onnx_model_path)) as client:
        assert client.post("/feedback", json=_body(serving_classes[0])).status_code == 204


def test_feedback_log_from_env_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PREDICTION_LOG_BUCKET", raising=False)
    assert feedback_log_from_env() is None
    monkeypatch.setenv("PREDICTION_LOG_BUCKET", "some-bucket")
    log = feedback_log_from_env()
    assert isinstance(log, FeedbackLog) and log.bucket == "some-bucket"


def test_feedback_log_key_and_record_shape() -> None:
    fake = FakeS3Client()
    FeedbackLog("b", client=fake).log(
        predicted_label="cat",
        confidence=0.9,
        correct=True,
        source="png",
        model_sha256="abc",
        service_version="9.9",
    )
    (put,) = fake.puts
    assert put["ContentType"] == "application/x-ndjson"
    assert put["Key"].endswith(".jsonl") and "/dt=" in put["Key"]
    record = json.loads(put["Body"])
    assert set(record) == {
        "ts",
        "predicted_label",
        "confidence",
        "correct",
        "source",
        "model_sha256",
        "service_version",
    }


def test_feedback_log_never_raises_on_write_failure() -> None:
    # The log helper itself swallows write errors (the endpoint relies on this).
    FeedbackLog("b", client=FakeS3Client(fail=True)).log(
        predicted_label="cat",
        confidence=0.5,
        correct=False,
        source="strokes",
        model_sha256="abc",
        service_version="9.9",
    )
