# MERIDIAN — Intelligent Dead Reckoning

MERIDIAN is a two-target implementation of the ISRO SIH 26168 Intelligent Dead Reckoning (IDR) system. It replays and trains on IO-VNBD driving data, builds a GNSS-aided/dead-reckoning trajectory, and exposes the same inference contract to a Flutter mobile app and a portable edge runtime.

## Repository layout

| Directory | Responsibility |
| --- | --- |
| `ml/` | Offline IO-VNBD ingestion, calibration, training, replay, and evaluation. |
| `mobile/` | Flutter MERIDIAN app and the on-device sensor/inference adapters. |
| `edge/` | Portable Python reference runtime and C++/ONNX Runtime hand-off point. |
| `shared/` | Versioned feature, telemetry, and model-manifest contracts used by both targets. |

## Canonical coordinate and time conventions

- All timestamps are UTC epoch seconds, stored as `timestamp_s`.
- IMU vectors use the phone body frame: `x` right, `y` up, `z` toward the user/screen normal. The calibration stage produces the body-to-vehicle rotation.
- Vehicle frame is `x` forward, `y` right, `z` down. Navigation output is local ENU meters anchored at the first valid ground-truth/GNSS fix.
- Model inputs are fixed 2.0-second, 100 Hz windows. The contract lives in `shared/config/feature_spec.json`; it is the source of truth for Python, Dart, and edge implementations.

## Staged execution

Implementation follows the numbered milestones requested in the problem statement. Each runnable stage writes its metric report to `ml/reports/` and fails loudly when its acceptance check cannot be evaluated. Stage progress is tracked in [`docs/STAGE_STATUS.md`](docs/STAGE_STATUS.md).

The current local runtime is intentionally dependency-light. Install the optional packages in `ml/requirements.txt` before running model training or PNG plotting. The first ingestion stage includes an SVG plot fallback so its sanity check can run without matplotlib.
