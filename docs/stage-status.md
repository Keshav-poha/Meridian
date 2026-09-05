# MERIDIAN build milestones

Statuses distinguish a shipped, verified implementation from an offline-only
proof. “Partial” does not mean absent; it identifies a requirement that still
needs the listed validation or runtime integration.

| Stage | Scope | Status | Current evidence / remaining work |
| --- | --- | --- | --- |
| 1 | Repository scaffold and shared contracts | Complete | ML, mobile, edge, and shared roots exist; telemetry schema is versioned. |
| 2 | IO-VNBD ingestion, synchronization, and sanity plot | Complete | Timestamp interpolation uses an auditable fitted clock offset, m/s phone-speed contract, and a 0.25 s maximum interpolation gap. |
| 3 | Static/dynamic mounting calibration | Complete offline | The Driver A S3c replay uses the first 300 s only, with 148 turning samples, 0.299 turn correlation, and 172.55° heading coverage. Live calibration still requires duration, course coverage, GNSS quality, and mount-integrity checks. |
| 4 | Classical strapdown DR plus NHC | Complete | Held-out 59.9 s forward-acceleration baseline: 1,867.62 m travelled, 96.34 m endpoint error, 5.16% drift, 10 Hz. |
| 5 | Learn velocity estimator | Current artifact retrained; field validation pending | The v2 961-parameter model uses runtime-equivalent gravity + GNSS-kinematic yaw and a recording-disjoint multi-session corpus. Its bounded raw prior is not used as an absolute speed reset; see [`model-training-v2.md`](model-training-v2.md). The historical 2.41 m/s result belongs to the legacy static-calibration artifact. |
| 6 | Use learned speed in DR | Complete offline | The runtime-equivalent GNSS-anchored state applies bounded learned rate corrections: 94.88 m endpoint error, 5.08% drift at 10 Hz on held-out Driver A S3c. |
| 7 | OSM map matching | Partial — offline safety gate | On the corrected hold-out, 282/600 points (47%) were safely accepted; RMSE fell 31.73→31.64 m while endpoint error stayed raw because the last snap was rejected. A causal, directed road-graph matcher is not yet wired into Flutter/edge runtime. |
| 8 | GNSS+INS fusion and masked outages | Partial — runtime-equivalent offline replay | The provenance-verified 59.9 s mask passes at 5.08% with the deployable ONNX model. A live quality-aware EKF/UKF measurement-update implementation remains required. |
| 9 | Mode-switch blending | Implemented; physical validation pending | Mobile uses a unified loss/reacquisition state, a 500 ms blend, and requires a post-loss timestamped quality GNSS fix before recovery. Physical loss/reacquisition latency is still to be logged on a moving drive. |
| 10 | TFLite/ONNX export and target stubs | Partial — portable velocity runtime | Hash-bound 5,725-byte ONNX and 11,072-byte TFLite exports pass parity; the hardened Python ONNX reference sustained about 2,763 Hz fed at 200 Hz. Full edge mount calibration, INS, map matching, and fusion remain required. |
| 11 | Flutter MERIDIAN UI | Core UI complete; field validation pending | The specified screen set remains intact. Developer Mode adds confidence and a field logger; three-button Android navigation has been visually verified. Gesture-mode visual verification and a mounted road drive remain pending. |
| 12 | Reproducible evaluation deliverable | Complete offline | `docs/benchmarks/iovnbd-driver-a-s3c-stage12/` contains position, speed, and drift SVGs; CSVs; hash-bound model provenance; and a 5.08% / 10 Hz result. |

The next required validation is a real fixed-mount drive using the Developer
Mode recorder and `ml/scripts/evaluate_live_drive.py`; see
`docs/validation/real-world-validation.md`.
