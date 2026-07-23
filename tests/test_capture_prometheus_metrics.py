import importlib.util
from pathlib import Path

# scripts/ isn't a package; load the module by path.
_spec = importlib.util.spec_from_file_location(
    "capture_prometheus_metrics",
    Path(__file__).resolve().parent.parent / "scripts" / "capture_prometheus_metrics.py",
)
capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capture)


def test_result_to_series_shapes_points_and_labels() -> None:
    result = [
        {"metric": {"handler": "/predict"}, "values": [[1700000000, "1.5"], [1700000015, "2.0"]]},
    ]
    series = capture.result_to_series(result)

    assert series == [
        {"labels": {"handler": "/predict"}, "points": [[1700000000, 1.5], [1700000015, 2.0]]},
    ]


def test_result_to_series_maps_nan_and_inf_to_null() -> None:
    # error-rate queries return NaN when there's no traffic; JSON can't hold NaN/Inf.
    result = [{"metric": {}, "values": [[1, "NaN"], [2, "+Inf"], [3, "0.25"]]}]
    (only,) = capture.result_to_series(result)

    assert only["points"] == [[1, None], [2, None], [3, 0.25]]


def test_result_to_series_handles_empty() -> None:
    assert capture.result_to_series([]) == []
