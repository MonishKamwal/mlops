"""Prediction logging v0 (PLAN.md Phase 1, task 6): one JSONL record per prediction to S3.

Written from day one so Phase 4's drift monitoring inherits months of real traffic.
The design is shaped by Lambda's execution model and the free-tier budget:

- **Synchronous, in the request path.** Lambda freezes the container the moment the
  response is returned, so background tasks may run late or never. A same-region S3
  PUT costs ~tens of ms — cheap insurance against silent data loss.
- **One object per prediction.** A Lambda container serves one request at a time;
  buffering records across requests would park them in memory that a freeze or
  reap can discard. Objects are keyed ``predictions/dt=YYYY-MM-DD/...`` (Hive-style
  partition) so date-ranged reads — Athena or Phase 4 batch jobs — never scan the
  whole bucket.
- **Fail-open.** A prediction that can't be logged is still a good prediction: the
  S3 write is wrapped, failures go to stdlib logging (CloudWatch on Lambda).
- **No PII by construction.** The record carries a digest of the model input, never
  the raw request, and nothing from headers (source IP, user agent) is ever read.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

KEY_PREFIX = "predictions"


class PutObjectClient(Protocol):
    """The one sliver of the S3 API this module needs (tests substitute a fake)."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> Any: ...


class PredictionLog:
    """Writes one JSONL object per prediction under ``predictions/dt=YYYY-MM-DD/``."""

    def __init__(self, bucket: str, client: PutObjectClient | None = None) -> None:
        self.bucket = bucket
        if client is None:
            # Imported here, not at module top: keeps boto3's ~0.2 s import out of
            # cold starts when logging is disabled, and out of tests using fakes.
            import boto3
            from botocore.config import Config

            # Tight timeouts: logging shares the request path, so an S3 hiccup must
            # cost the caller a moment, not a chunk of Lambda's 30 s budget.
            client = boto3.client(
                "s3",
                config=Config(connect_timeout=1, read_timeout=3, retries={"max_attempts": 2}),
            )
        self._client = client

    def log(
        self,
        *,
        input_sha256: str,
        source: str,
        ranked: list[tuple[str, float]],
        latency_ms: float,
        model_sha256: str,
        service_version: str,
    ) -> None:
        """Write one record; never raises (fail-open — see module docstring).

        ``latency_ms`` is preprocess + inference as measured by the handler; the
        log write itself is deliberately not part of the number.
        """
        now = datetime.now(UTC)
        record = {
            "ts": now.isoformat(),
            "input_sha256": input_sha256,
            "source": source,
            "top3": [{"label": label, "probability": round(p, 6)} for label, p in ranked[:3]],
            "latency_ms": round(latency_ms, 2),
            # ties every record to the exact model artifact that produced it —
            # Phase 4 drift analysis must segment by model, not by wall clock
            "model_sha256": model_sha256,
            "service_version": service_version,
        }
        # compact timestamp in the key (no colons — legal in S3, hostile everywhere
        # else); the uuid suffix keeps concurrent containers collision-free
        key = f"{KEY_PREFIX}/dt={now:%Y-%m-%d}/{now:%Y%m%dT%H%M%S%f}Z-{uuid.uuid4().hex[:8]}.jsonl"
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=(json.dumps(record) + "\n").encode(),
                ContentType="application/x-ndjson",
            )
        except Exception:
            logger.exception("prediction log write failed (bucket=%s, key=%s)", self.bucket, key)


def prediction_log_from_env() -> PredictionLog | None:
    """``PREDICTION_LOG_BUCKET`` set -> a live logger; unset -> logging disabled.

    Presence-of-config as the feature switch: Terraform sets the variable on the
    Lambda (it alone knows the bucket name), while ``docker run``, tests, and any
    environment without the var get a no-AWS, no-op configuration for free.
    """
    bucket = os.environ.get("PREDICTION_LOG_BUCKET")
    return PredictionLog(bucket) if bucket else None
