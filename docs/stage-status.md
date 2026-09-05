# MERIDIAN build milestones

Statuses distinguish a shipped, verified implementation from an offline-only
proof. “Partial” does not mean absent; it identifies a requirement that still
needs the listed validation or runtime integration.

| Stage | Scope | Status | Current evidence / remaining work |
| --- | --- | --- | --- |
| 1 | Repository scaffold and shared contracts | Complete | ML, mobile, edge, and shared roots exist; telemetry schema is versioned. |
| 2 | IO-VNBD ingestion, synchronization, and sanity plot | Complete | Timestamp interpolation uses an auditable fitted clock offset, m/s phone-speed contract, and a 0.25 s maximum interpolation gap. |
| 3 | Static/dynamic mounting calibration | Complete offline | Corrected Driver B calibration uses 4,261 pre-blackout samples, has 0.00° static residual and 0.389 turn correlation. Live calibration now requires duration, course coverage, GNSS quality, and mount-integrity checks; a fixed-mount road run remains required. |
| 4 | Classical strapdown DR plus NHC | Complete | Corrected held-out 60 s baseline: 689.81 m travelled, 440.53 m endpoint error, 63.86% drift, 10 Hz. |
| 5 | Learn velocity estimator | Complete offline | Retrained 961-parameter CNN: 2.41 m/s held-out MAE versus 9.06 m/s classical speed MAE; it uses gap-safe windows and a 1.8 s split purge. |
| 6 | Use learned speed in DR | Complete offline | Corrected held-out learned-speed NHC: 54.71 m endpoint error, 7.93% drift at 10 Hz. |
| 7 | OSM map matching | Partial — offline safety gate | On the corrected hold-out, 282/600 points (47%) were safely accepted; RMSE fell 31.73→31.64 m while endpoint error stayed raw because the last snap was rejected. A causal, directed road-graph matcher is not yet wired into Flutter/edge runtime. |
| 8 | GNSS+INS fusion and masked outages | Partial — guarded offline replay | The provenance-verified 60 s mask passes at 7.93%. Weak pre-outage residual fits are withheld rather than making DR worse. A live quality-aware EKF/UKF measurement-update implementation remains required. |
| 9 | Mode-switch blending | Implemented; physical validation pending | Mobile uses a unified loss/reacquisition state, a 500 ms blend, and requires a post-loss timestamped quality GNSS fix before recovery. Physical loss/reacquisition latency is still to be logged on a moving drive. |
| 10 | TFLite/ONNX export and target stubs | Partial — portable velocity runtime | Hash-bound 5,387-byte ONNX and 10,276-byte TFLite exports pass parity; the hardened Python ONNX reference sustained 3,248 Hz fed at 200 Hz. Full edge mount calibration, INS, map matching, and fusion remain required. |
| 11 | Flutter MERIDIAN UI | Core UI complete; field validation pending | The specified screen set remains intact. Developer Mode adds confidence and a field logger; three-button Android navigation has been visually verified. Gesture-mode visual verification and a mounted road drive remain pending. |
| 12 | Reproducible evaluation deliverable | Complete offline | `docs/benchmarks/iovnbd-driver-b-stage12/` contains the corrected position SVG, CSVs, metrics, hash-bound model provenance, and a 7.93% / 10 Hz result. |

The next required validation is a real fixed-mount drive using the Developer
Mode recorder and `ml/scripts/evaluate_live_drive.py`; see
`docs/validation/real-world-validation.md`.
