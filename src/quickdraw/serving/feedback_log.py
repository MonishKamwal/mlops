"""User-feedback logging (PLAN.md Phase 4, task 3): one JSONL record per 👍/👎 to S3.

The canvas asks "did I guess right?" after each prediction; the answer is a real,
ground-truth-ish signal from real users. Records land under ``feedback/dt=YYYY-MM-DD/`` in
the logs bucket, and the weekly drift report turns them into a proxy-accuracy series.

Deliberately mirrors :mod:`quickdraw.serving.prediction_log` — same fail-open, append-only,
env-switched design (a feedback write must never break the request path), just a different
prefix and payload. The event is self-contained (the client sends the prediction context it
already has), so there is no join back to the prediction logs and no PII.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

KEY_PREFIX = "feedback"


class PutObjectClient(Protocol):
    """The one sliver of the S3 API this module needs (tests substitute a fake)."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> Any: ...


class FeedbackLog:
    """Writes one JSONL object per feedback event under ``feedback/dt=YYYY-MM-DD/``."""

    def __init__(self, bucket: str, client: PutObjectClient | None = None) -> None:
        self.bucket = bucket
        if client is None:
            # Imported here, not at module top: keeps boto3's ~0.2 s import out of cold
            # starts when feedback is disabled, and out of tests using fakes.
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                config=Config(connect_timeout=1, read_timeout=3, retries={"max_attempts": 2}),
            )
        self._client = client

    def log(
        self,
        *,
        predicted_label: str,
        confidence: float,
        correct: bool,
        source: str,
        model_sha256: str,
        service_version: str,
    ) -> None:
        """Write one record; never raises (fail-open — a lost 👍 must not fail the request)."""
        now = datetime.now(UTC)
        record = {
            "ts": now.isoformat(),
            "predicted_label": predicted_label,
            "confidence": round(confidence, 6),
            "correct": bool(correct),
            "source": source,
            # ties the verdict to the exact model that produced the prediction — proxy
            # accuracy must segment by model, not by wall clock
            "model_sha256": model_sha256,
            "service_version": service_version,
        }
        key = f"{KEY_PREFIX}/dt={now:%Y-%m-%d}/{now:%Y%m%dT%H%M%S%f}Z-{uuid.uuid4().hex[:8]}.jsonl"
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=(json.dumps(record) + "\n").encode(),
                ContentType="application/x-ndjson",
            )
        except Exception:
            logger.exception("feedback log write failed (bucket=%s, key=%s)", self.bucket, key)


def feedback_log_from_env() -> FeedbackLog | None:
    """``PREDICTION_LOG_BUCKET`` set -> a live logger; unset -> feedback logging disabled.

    Shares the prediction-log bucket variable: feedback and predictions live in the same
    logs bucket (different prefixes), so one switch governs both and ``docker run``/tests
    stay AWS-free.
    """
    bucket = os.environ.get("PREDICTION_LOG_BUCKET")
    return FeedbackLog(bucket) if bucket else None
