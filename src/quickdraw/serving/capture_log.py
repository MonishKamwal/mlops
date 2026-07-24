"""Labeled-drawing capture for retraining (PLAN.md Phase 4, task 5).

When a visitor gives feedback *and* the drawing can be labeled, we store the drawing itself —
so real doodles can flow back into training (the data flywheel). Records land under
``captures/dt=YYYY-MM-DD/`` in the logs bucket, separate from the lightweight ``feedback/``
stream (verdict only) so the proxy-accuracy sync never pulls these heavier stroke payloads.

The label is *resolved* by the caller: a 👍 confirms the guess (label = predicted class); a 👎
carries the user's correction (label = what they say they drew). Either way the record is a
``(strokes, label)`` training example. Strokes are stored raw — the retrain step rasterizes them
through the same shared preprocess the model trains on, so captures enter training with no skew.

Consent, not surveillance: the canvas tells users their drawing + answer train the model, and
nothing identifying is collected. Same fail-open / env-switched design as the other logs.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

KEY_PREFIX = "captures"

# QuickDraw stroke shape: per stroke, two parallel arrays [[x...], [y...]].
Strokes = list[list[list[float]]]


class PutObjectClient(Protocol):
    """The one sliver of the S3 API this module needs (tests substitute a fake)."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> Any: ...


class CaptureLog:
    """Writes one JSONL object per labeled capture under ``captures/dt=YYYY-MM-DD/``."""

    def __init__(self, bucket: str, client: PutObjectClient | None = None) -> None:
        self.bucket = bucket
        if client is None:
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
        strokes: Strokes,
        label: str,
        predicted_label: str,
        correct: bool,
        confidence: float,
        model_sha256: str,
        service_version: str,
    ) -> None:
        """Write one capture; never raises (fail-open — a lost capture must not fail a request)."""
        now = datetime.now(UTC)
        record = {
            "ts": now.isoformat(),
            "strokes": strokes,
            # the *true* label for training: the guess when 👍, the correction when 👎
            "label": label,
            "predicted_label": predicted_label,
            "correct": bool(correct),
            "confidence": round(confidence, 6),
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
            logger.exception("capture log write failed (bucket=%s, key=%s)", self.bucket, key)


def capture_log_from_env() -> CaptureLog | None:
    """``PREDICTION_LOG_BUCKET`` set -> a live logger; unset -> capture disabled.

    Shares the one logs bucket (different prefix), so a single switch governs predictions,
    feedback, and captures, and ``docker run``/tests stay AWS-free.
    """
    bucket = os.environ.get("PREDICTION_LOG_BUCKET")
    return CaptureLog(bucket) if bucket else None
