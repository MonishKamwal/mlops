"""Prediction logging v0 — record shape, key layout, fail-open, env switch."""

import json
from datetime import UTC, datetime

import pytest
from conftest import FakeS3Client

from quickdraw.serving.prediction_log import PredictionLog, prediction_log_from_env

RANKED = [("cat", 0.7), ("dog", 0.2), ("bird", 0.05), ("fish", 0.03), ("star", 0.02)]


def write_one(client: FakeS3Client) -> None:
    PredictionLog("test-bucket", client=client).log(
        input_sha256="ab" * 32,
        source="strokes",
        ranked=RANKED,
        latency_ms=12.3456,
        model_sha256="cd" * 32,
        service_version="0.1.0",
    )


def test_record_is_one_jsonl_line_with_the_v0_fields() -> None:
    client = FakeS3Client()
    write_one(client)
    (put,) = client.puts
    assert put["Bucket"] == "test-bucket"
    assert put["ContentType"] == "application/x-ndjson"
    body = put["Body"].decode()
    assert body.endswith("\n") and body.count("\n") == 1
    record = json.loads(body)
    assert set(record) == {
        "ts",
        "input_sha256",
        "source",
        "top3",
        "latency_ms",
        "model_sha256",
        "service_version",
    }
    assert record["input_sha256"] == "ab" * 32
    assert record["source"] == "strokes"
    assert record["latency_ms"] == 12.35
    # full ISO-8601 UTC timestamp, parseable round-trip
    assert datetime.fromisoformat(record["ts"]).tzinfo is not None


def test_top3_truncates_the_full_ranking() -> None:
    client = FakeS3Client()
    write_one(client)
    record = json.loads(client.puts[0]["Body"])
    assert record["top3"] == [
        {"label": "cat", "probability": 0.7},
        {"label": "dog", "probability": 0.2},
        {"label": "bird", "probability": 0.05},
    ]


def test_key_is_date_partitioned_jsonl() -> None:
    client = FakeS3Client()
    write_one(client)
    key = client.puts[0]["Key"]
    assert key.startswith(f"predictions/dt={datetime.now(UTC):%Y-%m-%d}/")
    assert key.endswith(".jsonl")


def test_keys_are_unique_per_record() -> None:
    client = FakeS3Client()
    write_one(client)
    write_one(client)
    assert client.puts[0]["Key"] != client.puts[1]["Key"]


def test_s3_failure_does_not_raise() -> None:
    write_one(FakeS3Client(fail=True))  # fail-open: no exception may escape


def test_from_env_is_disabled_without_the_bucket_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PREDICTION_LOG_BUCKET", raising=False)
    assert prediction_log_from_env() is None


def test_from_env_builds_a_logger_when_the_bucket_var_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PREDICTION_LOG_BUCKET", "some-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")  # boto3 client needs a region
    log = prediction_log_from_env()
    assert log is not None
    assert log.bucket == "some-bucket"
