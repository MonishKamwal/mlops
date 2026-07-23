"""The serving API: FastAPI in front of the ONNX model (PLAN.md Phase 1, task 3).

- ``POST /predict`` — accepts the raw drawing (stroke list and/or base64 canvas PNG);
  *all* preprocessing down to the 28x28 model input happens here, through
  :mod:`quickdraw.data.preprocess` — the exact module the training pipeline uses.
  That shared code path is the project's no-train/serve-skew rule made concrete.
  With ``PREDICTION_LOG_BUCKET`` set, every prediction also writes one JSONL record
  to S3 (:mod:`quickdraw.serving.prediction_log` — digest, top-3, latency; no PII).
- ``POST /feedback`` — a visitor's 👍/👎 on a prediction (Phase 4, task 3); writes one
  JSONL record to S3 (:mod:`quickdraw.serving.feedback_log`) for the proxy-accuracy signal.
- ``GET /healthz`` — readiness: the Lambda Web Adapter polls it before forwarding
  traffic; EKS probes reuse it in Phase 3.
- ``GET /model-info`` — which model is loaded (classes, val_accuracy, sha256).
- ``GET /metrics`` — Prometheus format. Nothing scrapes it on Lambda, but it makes
  the image observability-ready for the Phase 3 EKS runs at zero extra cost.

One image, both tiers: uvicorn serves plain HTTP everywhere; on Lambda the Web
Adapter extension turns Function URL invocations into the same HTTP requests.

CORS is deliberately absent: the Function URL owns it in production (PLAN.md §2),
and doubled CORS headers break browsers. Revisit for local frontend dev in task 5.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

from quickdraw.data.preprocess import png_to_model_input, strokes_to_model_input
from quickdraw.serving.feedback_log import FeedbackLog, feedback_log_from_env
from quickdraw.serving.prediction_log import PredictionLog, prediction_log_from_env
from quickdraw.serving.predictor import Predictor

DEFAULT_MODEL_PATH = "models/model.onnx"


class PredictRequest(BaseModel):
    """A raw drawing, in either or both of the two formats the canvas produces.

    ``strokes`` is the QuickDraw shape ``[[[x...], [y...]], ...]``; ``png_base64``
    is the base64-encoded canvas PNG (no ``data:`` URL prefix). When both are
    present, strokes win: they carry the drawing order and render closer to how
    the training bitmaps were made.
    """

    strokes: list[list[list[float]]] | None = None
    png_base64: str | None = None


class Prediction(BaseModel):
    label: str
    probability: float


class PredictResponse(BaseModel):
    predictions: list[Prediction]  # every class, sorted by probability descending
    source: Literal["strokes", "png"]  # which input format was used


class ModelInfo(BaseModel):
    classes: list[str]
    val_accuracy: float
    model_sha256: str
    service_version: str


class FeedbackRequest(BaseModel):
    """A visitor's 👍/👎 on a prediction — the context the client already holds.

    Self-contained by design (see feedback_log): no prediction id, no join back to the
    prediction logs. ``correct`` is the whole signal; the rest lets proxy-accuracy be
    sliced by class/model. ``model_sha256`` is optional — the client gets it from
    ``/model-info`` and may not always have it.
    """

    predicted_label: str
    confidence: float = Field(ge=0.0, le=1.0)
    correct: bool
    source: Literal["strokes", "png"] = "strokes"
    model_sha256: str = ""


def create_app(
    model_path: Path | None = None,
    prediction_log: PredictionLog | None = None,
    feedback_log: FeedbackLog | None = None,
) -> FastAPI:
    """Build the app around one model file (env ``MODEL_PATH`` unless overridden).

    The model loads in the lifespan, not at import: importing the module is always
    safe, and a missing/corrupt model fails the server at startup — loudly, before
    the readiness check ever passes — instead of on the first request.

    ``prediction_log``/``feedback_log`` are injectable for tests; by default they come
    from the environment (``PREDICTION_LOG_BUCKET`` set -> log to S3, unset -> disabled).
    """
    path = Path(model_path or os.environ.get("MODEL_PATH", DEFAULT_MODEL_PATH))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.predictor = Predictor(path)
        app.state.prediction_log = prediction_log or prediction_log_from_env()
        app.state.feedback_log = feedback_log or feedback_log_from_env()
        yield

    app = FastAPI(title="QuickDraw sketch classifier", lifespan=lifespan)
    # /metrics and /healthz are probe traffic; keeping them out of the histograms
    # leaves the latency/RPS story about real predictions
    Instrumentator(excluded_handlers=["/metrics", "/healthz"]).instrument(app).expose(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/model-info")
    def model_info() -> ModelInfo:
        predictor: Predictor = app.state.predictor
        return ModelInfo(
            classes=predictor.classes,
            val_accuracy=predictor.val_accuracy,
            model_sha256=predictor.model_sha256,
            service_version=version("quickdraw"),
        )

    @app.post("/predict")
    def predict(request: PredictRequest) -> PredictResponse:
        if request.strokes is None and request.png_base64 is None:
            raise HTTPException(status_code=400, detail="provide strokes and/or png_base64")
        start = time.perf_counter()
        try:
            if request.strokes is not None:
                source: Literal["strokes", "png"] = "strokes"
                model_input = strokes_to_model_input(request.strokes)
            else:
                source = "png"
                png_bytes = base64.b64decode(request.png_base64, validate=True)
                model_input = png_to_model_input(png_bytes)
        # ValueError: empty drawing, malformed strokes, bad base64 (binascii.Error
        # is a ValueError). OSError: PIL can't identify the decoded bytes as an image.
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        predictor: Predictor = app.state.predictor
        ranked = predictor.predict(model_input)
        latency_ms = (time.perf_counter() - start) * 1000

        log: PredictionLog | None = app.state.prediction_log
        if log is not None:
            # digest of the canonical (1, 28, 28) float32 input, not the request
            # JSON: identical drawings hash identically however they were encoded
            log.log(
                input_sha256=hashlib.sha256(model_input.tobytes()).hexdigest(),
                source=source,
                ranked=ranked,
                latency_ms=latency_ms,
                model_sha256=predictor.model_sha256,
                service_version=version("quickdraw"),
            )

        return PredictResponse(
            predictions=[Prediction(label=label, probability=p) for label, p in ranked],
            source=source,
        )

    @app.post("/feedback", status_code=204)
    def feedback(request: FeedbackRequest) -> None:
        # Reject a label the model can't produce: it would only be a client bug or noise,
        # and it would pollute the per-class proxy-accuracy. 404 keeps bad data out.
        predictor: Predictor = app.state.predictor
        if request.predicted_label not in predictor.classes:
            raise HTTPException(
                status_code=404, detail=f"unknown label {request.predicted_label!r}"
            )

        log: FeedbackLog | None = app.state.feedback_log
        if log is not None:
            log.log(
                predicted_label=request.predicted_label,
                confidence=request.confidence,
                correct=request.correct,
                source=request.source,
                model_sha256=request.model_sha256 or predictor.model_sha256,
                service_version=version("quickdraw"),
            )

    return app


app = create_app()
