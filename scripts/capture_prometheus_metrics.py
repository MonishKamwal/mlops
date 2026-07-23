#!/usr/bin/env python3
"""Capture the observability dashboard's underlying time-series as a JSON data contract.

Runs in the eks-demo workflow after the k6 load window, against a port-forwarded Prometheus.
The Grafana PNG is a baked visual — fine as a dev artifact, but the portfolio site can't
restyle it. This emits the *data* behind the panels (RPS, latency percentiles, error rate,
requests-by-status, node CPU/mem) via Prometheus `query_range`, so the site renders its own
styled charts. Public-facing artifacts ship a data contract, never a pre-rendered image.

Standard library only: the workflow runner has no `uv sync`, so this must run on bare python3.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any


def _latency(q: float) -> str:
    """p{q} of request latency, from the high-resolution histogram (matches the dashboard)."""
    return (
        f"histogram_quantile({q}, "
        "sum(rate(http_request_duration_highr_seconds_bucket[1m])) by (le))"
    )


# name -> (PromQL, unit). Mirrors deploy/grafana/dashboards/quickdraw-api.json plus a cluster
# CPU/mem view from node-exporter. Keep names stable — the portfolio site keys off them.
QUERIES: dict[str, tuple[str, str]] = {
    "rps_total": ("sum(rate(http_requests_total[1m]))", "reqps"),
    "rps_by_handler": ("sum by (handler) (rate(http_requests_total[1m]))", "reqps"),
    "requests_by_status": ("sum by (status) (rate(http_requests_total[1m]))", "reqps"),
    "latency_p50": (_latency(0.50), "seconds"),
    "latency_p90": (_latency(0.90), "seconds"),
    "latency_p95": (_latency(0.95), "seconds"),
    "latency_p99": (_latency(0.99), "seconds"),
    "error_rate_5xx": (
        'sum(rate(http_requests_total{status=~"5.."}[1m])) / sum(rate(http_requests_total[1m]))',
        "ratio",
    ),
    "node_cpu_utilization": (
        '1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m]))',
        "ratio",
    ),
    "node_mem_utilization": (
        "1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
        "ratio",
    ),
}


def _to_float(raw: str) -> float | None:
    """Prometheus sample value -> float, mapping NaN/Inf (JSON-illegal) to null."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def result_to_series(result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert a Prometheus `query_range` `data.result` into `[{labels, points}]`.

    Pure (no I/O) so it can be unit-tested. Each point is `[unix_ts, value|null]`.
    """
    series = []
    for entry in result:
        points = [[int(ts), _to_float(val)] for ts, val in entry.get("values", [])]
        series.append({"labels": entry.get("metric", {}), "points": points})
    return series


def query_range(
    base_url: str, promql: str, start: int, end: int, step: int
) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"query": promql, "start": start, "end": end, "step": step})
    url = f"{base_url.rstrip('/')}/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (localhost port-forward)
        payload = json.load(resp)
    if payload.get("status") != "success":
        raise RuntimeError(f"query failed: {promql}: {payload.get('error')}")
    return payload["data"]["result"]


def capture(base_url: str, minutes: int, step: int) -> dict[str, Any]:
    end = int(time.time())
    start = end - minutes * 60
    queries: dict[str, Any] = {}
    for name, (promql, unit) in QUERIES.items():
        try:
            series = result_to_series(query_range(base_url, promql, start, end, step))
        except Exception as exc:  # best-effort per query — one bad metric shouldn't sink the rest
            queries[name] = {"promql": promql, "unit": unit, "error": str(exc), "series": []}
            continue
        queries[name] = {"promql": promql, "unit": unit, "series": series}
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "window": {"start": start, "end": end, "step_seconds": step, "minutes": minutes},
        "queries": queries,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:9090")
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--step", type=int, default=15)
    parser.add_argument("--out", default="eks-evidence/api-metrics.json")
    args = parser.parse_args(argv)

    data = capture(args.base_url, args.minutes, args.step)
    with open(args.out, "w") as fh:
        json.dump(data, fh, indent=2)
    n = sum(len(q.get("series", [])) for q in data["queries"].values())
    print(f"wrote {args.out}: {len(data['queries'])} queries, {n} series")


if __name__ == "__main__":
    main()
