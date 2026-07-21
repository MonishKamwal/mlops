// k6 load test for the QuickDraw serving API (Phase 3 task 3).
// Runs against a `kubectl port-forward` to the in-cluster Service, so it exercises the
// same /predict path the public site uses. TARGET is set by the workflow (e.g.
// http://localhost:8080). HTML report comes from k6's web dashboard export.
import http from "k6/http";
import { check } from "k6";

const TARGET = __ENV.TARGET || "http://localhost:8080";

// A single QuickDraw stroke ([[xs], [ys]]) — the same shape the canvas frontend sends;
// the server bbox-normalizes raw coords, so exact values don't matter.
const payload = JSON.stringify({
  strokes: [
    [
      [10, 128, 246, 50, 200, 10],
      [10, 90, 90, 150, 150, 10],
    ],
  ],
});
const params = { headers: { "Content-Type": "application/json" } };

export const options = {
  vus: 20,
  duration: "3m",
  thresholds: {
    http_req_failed: ["rate<0.01"], // < 1% errors
    http_req_duration: ["p(95)<1500"], // p95 under 1.5s
  },
};

export default function () {
  const res = http.post(`${TARGET}/predict`, payload, params);
  check(res, {
    "status is 200": (r) => r.status === 200,
    "body is non-empty": (r) => r.body && r.body.length > 0,
  });
}
