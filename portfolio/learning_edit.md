---
title: Build journey
subtitle: Things I learned building an MLOps platform on a $0 budget
source: LEARNING.md
---

The portfolio cut of `LEARNING.md` — the devlog, newest first. Each `## YYYY-MM-DD — headline`
becomes one timeline entry; the converter reads the date and headline off the heading and copies
the body markdown through. Trim, reorder, or rewrite these freely; the deep versions stay in
`LEARNING.md`.

## 2026-07-24 — The retrain flywheel, proven end to end

Wired the whole loop shut: a graded drawing on the live site becomes labeled data, and an opt-in
training run folds it in, registers a challenger, and lets the gate decide. Watched it run —
captures synced, training set augmented, challenger registered, gate held the champion. The point
wasn't the accuracy delta (four test drawings against 120k is noise); it was proving the pipeline
carries real user labels from canvas back to a deployed model with no human in the middle.

## 2026-07-22 — "Control plane works" is not "EKS works"

The free plan blocks worker nodes three different ways, and I hit them one at a time: only a fixed
list of instance types is free-tier-eligible (my first pick wasn't), new accounts get a 1-vCPU
quota that blocks every eligible type, and my serving image is arm64-only so the nodes had to be
Graviton. A control-plane-only cluster had lulled me into thinking EKS was clear. Fix: `t4g.small`
Graviton on-demand — matches the image, eligible, and cheap enough to tear down guilt-free.

## 2026-07-20 — The quality gate: decouple "ship it" from "it's the new best"

The insight that made the gate click: "deployed" and "best-ever" are different facts and shouldn't
share a pointer. So the gate ships any challenger that clears the floor and doesn't regress, but
the `champion` alias only moves on a strict improvement. Champion stays the quality bar the next
challenger must beat, the baseline never drifts down, and nothing has to pretend the alias tracks
production. Proved both paths live: a strict improvement re-crowned, a crippled model was blocked.

## 2026-07-20 — First OIDC deploy: the merge→live path with zero long-lived keys

Stood up keyless deploys — GitHub Actions assumes an AWS role via OIDC, no stored credentials
anywhere. The first run exercised the whole chain: assume role, pull MLflow state, run the
pipeline, gate, build the arm64 image (cheap via QEMU because the serving image has no torch),
push by digest, update the Lambda, and smoke-test that the live model's sha matches what I just
built. After that, merging to main is the only manual step; everything downstream is automatic.

## 2026-07-06 — Prediction logging on Lambda: the freeze changes the design

Lambda freezes the execution environment the instant it returns a response, so the usual "log in a
background task" trick silently loses data. The log write has to be synchronous, in the request
path — but fail-open, so an S3 hiccup costs a log line, never a prediction. Also stored a hash of
the input, not the pixels: enough to detect drift on the output distribution later, without keeping
anyone's drawing.

## 2026-07-03 — One preprocessing path, or train/serve skew wins

The subtle bug that never fires a stack trace: training and serving preprocess inputs even slightly
differently, and accuracy quietly rots in production. The fix is structural — one shared
preprocessing module both sides import, plus parity tests that fail the build if the two ever
diverge. Boring, and exactly the point.
