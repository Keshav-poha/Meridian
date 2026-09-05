# MERIDIAN — Intelligent Dead Reckoning

MERIDIAN is a two-target implementation of the ISRO SIH 26168 Intelligent Dead Reckoning (IDR) system. It replays and trains on IO-VNBD driving data, builds a GNSS-aided/dead-reckoning trajectory, and exposes the same inference contract to a Flutter mobile app and a portable edge runtime.

The complete offline pipeline is implemented. Its held-out 60-second IO-VNBD
Driver B GNSS blackout ends at **53.50 m error over 676.92 m (7.90% drift) at
10 Hz**, passing the required less-than-10-percent drift target. See the
[position plot and metrics table](docs/evaluation/stage12/README.md).

## Repository layout

| Directory | Responsibility |
| --- | --- |
| `ml/` | Offline IO-VNBD ingestion, calibration, training, replay, and evaluation. |
| `mobile/` | Flutter MERIDIAN app and the on-device sensor/inference adapters. |
| `edge/` | Portable Python reference runtime and C++/ONNX Runtime hand-off point. |
| `shared/` | Versioned feature, telemetry, and model-manifest contracts used by both targets. |
| `docs/` | Stage status, reproducible evaluation artifacts, and tracked test results. |

## Canonical coordinate and time conventions

- All timestamps are UTC epoch seconds, stored as `timestamp_s`.
- IMU vectors use the phone body frame: `x` right, `y` up, `z` toward the user/screen normal. The calibration stage produces the body-to-vehicle rotation.
- Vehicle frame is `x` forward, `y` right, `z` down. Navigation output is local ENU meters anchored at the first valid ground-truth/GNSS fix.
- Model inputs are fixed 2.0-second, 10 Hz windows of gravity-compensated, body-to-vehicle-calibrated IMU data. Mobile (100 Hz) and edge (200 Hz) adapters filter/aggregate their live streams to that contract; their fusion loops continue at native rate. The contract lives in `shared/config/feature_spec.json`.

## Staged execution

Implementation follows the numbered milestones requested in the problem statement. Each runnable stage writes its metric report to `ml/reports/` and fails loudly when its acceptance check cannot be evaluated. Stage progress is tracked in [`docs/STAGE_STATUS.md`](docs/STAGE_STATUS.md).

The current local runtime is intentionally dependency-light. Install the optional packages in `ml/requirements.txt` before running model training or PNG plotting. The first ingestion stage includes an SVG plot fallback so its sanity check can run without matplotlib.

## Verification

The repository has a GitHub Actions workflow for Python contracts, ML/edge unit
tests, Flutter analysis, Flutter tests, and an Android debug APK build. Local
commands and the latest verified outcomes are listed in
[`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md). The data-backed Stage 12 replay
is reproducible with `py -3.13 ml/scripts/stage12_evaluate.py` after the
documented dataset/model setup.

## Validation boundary

Offline IO-VNBD replay and package compilation are complete. A physical
Android smoke test has verified live sensor/GNSS telemetry and safeguards
against stationary or hand-held false speed; see
[`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md). A moving-device blackout run
is still required to measure physical drift, transition latency, and update
rate against the <10% target.
