# Model card — QuickDraw sketch classifier

A small convolutional network that sorts a hand-drawn sketch into one of 15 everyday
categories. It is the model at the centre of an end-to-end MLOps platform; the
engineering around it — reproducible training, a quality gate, automated
train→deploy, and a live evidence hub — is the real subject of the project, and the
model is kept deliberately simple so that story stays legible.

- **Owner:** Monish Kamwal
- **Repository:** <https://github.com/MonishKamwal/mlops>
- **Live demo:** the drawing canvas at <https://monishkamwal.github.io>
- **Evidence hub:** <https://monishkamwal.github.io/mlops/> — live metrics and run history
- **License:** MIT

## Model details

- **Architecture:** `QuickDrawCNN` — two 3×3 convolutional blocks (1→32→64 channels,
  ReLU, 2×2 max-pool: 28→14→7) into a dropout-regularized fully-connected head
  (3136→128→15), ~422k parameters.
- **Input:** a single-channel 28×28 grayscale bitmap, float32 in [0, 1]. Canvas strokes
  or a PNG are converted to this tensor by one shared preprocessing module
  (`quickdraw.data.preprocess`) used identically at train and serve time — so there is
  no train/serve skew by construction, and parity tests enforce it.
- **Output:** 15 raw logits (softmax is the consumer's job). The class↔index mapping
  rides inside the model artifact (ONNX metadata), so label order can never drift from
  the weights.
- **Formats:** trained in PyTorch, exported to ONNX (~1.6 MB), served with ONNX Runtime.
- **Versioning:** every trained model is registered in MLflow; the `champion` alias
  marks the best model ever validated — the quality bar. The current champion and live
  per-run metrics are on the evidence hub.

## Intended use

- **Intended:** an interactive demo — recognise a quick doodle of one of the 15
  supported classes drawn in a browser canvas. It exists to showcase an MLOps pipeline,
  not to be a production sketch-recognition service.
- **Out of scope:** anything safety-, security-, or rights-affecting; recognising
  classes outside the trained set (it always returns one of the 15, however confidently
  wrong); photographs or natural images; handwriting or text.

## Classes (15)

airplane, apple, banana, bicycle, bird, car, cat, clock, dog, face, fish, house, star,
tree, umbrella.

Order is fixed: a class's position in this list *is* its label index. New classes are
only ever appended and the model retrained — never reordered.

## Training data

- **Source:** Google's [Quick, Draw!](https://quickdraw.withgoogle.com/data) dataset —
  millions of crowd-sourced doodles, used here as the pre-rendered 28×28 bitmaps.
- **Sampling:** 10,000 examples per class (150,000 total), split 80/10/10 into
  train/validation/test with a fixed seed, stratified by class.
- **Validation gate:** a Pandera schema checks the processed dataset (exact split sizes,
  full label set per split, pixel/ink sanity) before training is allowed to run.

## Training procedure

- 8 epochs, batch size 256, Adam (learning rate 1e-3, weight decay 1e-4), dropout 0.3,
  seed 42; the best-validation-epoch checkpoint is kept. Trains in minutes on a laptop
  CPU.
- The whole pipeline (download → preprocess → validate → train → evaluate → export) is a
  DVC DAG, so `dvc repro` reproduces it end to end; seeded runs are byte-identical on the
  same machine.

## Evaluation

- **Data:** the held-out 15,000-example test split (1,000 per class), unseen in training
  or validation.
- **Headline metrics (reference champion):** test accuracy ≈ **0.917**, macro F1 ≈
  **0.916**. Live per-class precision/recall/F1 and the confusion matrix are on the
  evidence hub and refresh on every deploy.
- **Quality gate:** a challenger ships only if it clears an absolute floor
  (test accuracy ≥ 0.85) *and* does not regress on the champion beyond ε = 0.5pp; the
  `champion` alias moves only on a strict improvement.

## Limitations

- **Closed world:** exactly 15 classes. Anything else — an unsupported object, a
  scribble, an empty canvas — is forced into one of the 15, sometimes with high
  confidence.
- **Hardest classes:** the visually similar animals are the weak spot — dog (F1 ≈ 0.77),
  bird (≈ 0.79), and cat (≈ 0.84) are confused with each other far more than the rigid
  shapes (house, clock, apple all F1 ≈ 0.96+).
- **Domain:** trained on QuickDraw's fast, single-colour doodles at 28×28. Detailed,
  shaded, or very differently-styled drawings are out of distribution.
- **Resolution:** 28×28 grayscale discards colour and fine detail by design.

## Ethical considerations

- QuickDraw doodles are crowd-sourced and culturally skewed — how people draw a "house"
  or a "face" varies by region and background, so the model reflects the drawing
  conventions over-represented in the source data.
- The public demo logs each prediction (input hash, top-3, latency, model hash) to
  support monitoring and drift analysis. No personal data is collected, and the raw
  drawing is not stored as an image.

## Caveats

- `champion` is the quality bar, not necessarily the *deployed* version — a challenger
  can ship within ε without re-crowning, so the live model may sit fractionally below
  champion. The deployed image is the source of truth for what is live.
- The metrics quoted here are a reference point; the evidence hub is authoritative and
  current.
