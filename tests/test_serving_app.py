"""API tests — every /predict path must run through the shared preprocess module."""

import base64
import io
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FakeS3Client
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from quickdraw.serving.app import create_app
from quickdraw.serving.prediction_log import PredictionLog

# a closed square, in canvas-like coordinates
STROKES = [[[20.0, 240.0, 240.0, 20.0, 20.0], [20.0, 20.0, 240.0, 240.0, 20.0]]]


def drawing_png_base64() -> str:
    """A 280x280 canvas-style PNG (dark ink on white) with a diagonal stroke."""
    image = Image.new("RGB", (280, 280), "white")
    ImageDraw.Draw(image).line([(40, 40), (240, 240)], fill="black", width=12)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture(scope="module")
def client(onnx_model_path: Path) -> Iterator[TestClient]:
    app = create_app(onnx_model_path)
    with TestClient(app) as client:  # the context manager runs the lifespan
        yield client


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_model_info(client: TestClient, serving_classes: list[str]) -> None:
    body = client.get("/model-info").json()
    assert body["classes"] == serving_classes
    assert body["val_accuracy"] == 0.5
    assert len(body["model_sha256"]) == 64
    assert body["service_version"]


def test_predict_from_strokes(client: TestClient, serving_classes: list[str]) -> None:
    response = client.post("/predict", json={"strokes": STROKES})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "strokes"
    labels = [p["label"] for p in body["predictions"]]
    probabilities = [p["probability"] for p in body["predictions"]]
    assert sorted(labels) == sorted(serving_classes)
    assert probabilities == sorted(probabilities, reverse=True)
    assert sum(probabilities) == pytest.approx(1.0, rel=1e-5)


def test_predict_from_png(client: TestClient) -> None:
    response = client.post("/predict", json={"png_base64": drawing_png_base64()})
    assert response.status_code == 200
    assert response.json()["source"] == "png"


def test_predict_prefers_strokes_when_both_sent(client: TestClient) -> None:
    payload = {"strokes": STROKES, "png_base64": drawing_png_base64()}
    assert client.post("/predict", json=payload).json()["source"] == "strokes"


def test_predict_requires_some_input(client: TestClient) -> None:
    response = client.post("/predict", json={})
    assert response.status_code == 400
    assert "strokes" in response.json()["detail"]


def test_predict_empty_strokes_is_400(client: TestClient) -> None:
    response = client.post("/predict", json={"strokes": []})
    assert response.status_code == 400
    assert "empty drawing" in response.json()["detail"]


def test_predict_invalid_base64_is_400(client: TestClient) -> None:
    response = client.post("/predict", json={"png_base64": "not-base64!!!"})
    assert response.status_code == 400


def test_predict_non_png_bytes_is_400(client: TestClient) -> None:
    response = client.post(
        "/predict", json={"png_base64": base64.b64encode(b"plainly not an image").decode()}
    )
    assert response.status_code == 400


def test_predict_blank_png_is_400(client: TestClient) -> None:
    image = Image.new("RGB", (280, 280), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    payload = {"png_base64": base64.b64encode(buffer.getvalue()).decode()}
    response = client.post("/predict", json=payload)
    assert response.status_code == 400
    assert "no ink" in response.json()["detail"]


def test_metrics_endpoint_reports_requests(client: TestClient) -> None:
    client.post("/predict", json={"strokes": STROKES})
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_request" in response.text  # instrumentator's default metric family


@pytest.fixture()
def logging_client(onnx_model_path: Path) -> Iterator[tuple[TestClient, FakeS3Client]]:
    """An app instance with prediction logging wired to a fake S3 client."""
    fake = FakeS3Client()
    app = create_app(onnx_model_path, prediction_log=PredictionLog("test-bucket", client=fake))
    with TestClient(app) as client:
        yield client, fake


def test_predict_writes_one_matching_log_record(
    logging_client: tuple[TestClient, FakeS3Client],
) -> None:
    client, fake = logging_client
    body = client.post("/predict", json={"strokes": STROKES}).json()
    (put,) = fake.puts
    record = json.loads(put["Body"])
    assert record["source"] == "strokes"
    assert record["top3"] == [
        {"label": p["label"], "probability": pytest.approx(p["probability"], abs=1e-6)}
        for p in body["predictions"][:3]
    ]
    assert len(record["input_sha256"]) == 64
    assert len(record["model_sha256"]) == 64
    assert record["latency_ms"] > 0


def test_identical_drawings_log_identical_digests(
    logging_client: tuple[TestClient, FakeS3Client],
) -> None:
    """The digest covers the canonical model input, not the request encoding."""
    client, fake = logging_client
    client.post("/predict", json={"strokes": STROKES})
    client.post("/predict", json={"strokes": STROKES})
    first, second = (json.loads(put["Body"]) for put in fake.puts)
    assert first["input_sha256"] == second["input_sha256"]


def test_rejected_requests_are_not_logged(
    logging_client: tuple[TestClient, FakeS3Client],
) -> None:
    client, fake = logging_client
    assert client.post("/predict", json={"strokes": []}).status_code == 400
    assert fake.puts == []


def test_predict_survives_a_logging_outage(onnx_model_path: Path) -> None:
    """Fail-open end to end: S3 down must not cost a single prediction."""
    log = PredictionLog("test-bucket", client=FakeS3Client(fail=True))
    app = create_app(onnx_model_path, prediction_log=log)
    with TestClient(app) as client:
        response = client.post("/predict", json={"strokes": STROKES})
    assert response.status_code == 200


def test_serving_never_imports_torch() -> None:
    """The serving image ships without the training stack (PLAN.md §2): importing
    the app must not pull in torch, or the Docker build breaks by design."""
    code = "import sys; import quickdraw.serving.app; sys.exit('torch' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, "quickdraw.serving.app transitively imports torch"
