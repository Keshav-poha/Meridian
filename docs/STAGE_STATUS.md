# MERIDIAN build milestones

| Stage | Scope | Status | Required validation |
| --- | --- | --- | --- |
| 1 | Repository scaffold and shared contracts | Complete | Contract is valid JSON and all target roots exist. |
| 2 | IO-VNBD ingestion, synchronization, and sanity plot | Complete | `M (Driver B)` 5,000-row source subset yields 4,999 synchronized 10 Hz samples, 2,490 2-second windows (`20 × 9`), and `ml/reports/stage2/sanity.svg`. |
| 3 | Static/dynamic mounting calibration | Pending | Idle and turn segments produce a finite body-to-vehicle orientation. |
| 4 | Classical strapdown DR plus NHC | Pending | Replay reports drift metres and drift percent against ground truth. |
| 5 | Learn velocity estimator | Pending | Held-out speed error is compared with classical implied speed. |
| 6 | Use learned speed in DR | Pending | Same blackout segments report comparable drift. |
| 7 | OSM map matching | Pending | Map-matched trajectory error is compared with raw DR. |
| 8 | GNSS+INS fusion and masked outages | Pending | Blackout metrics are evaluated against the 10% drift target. |
| 9 | Mode-switch blending | Pending | Transition latency and discontinuity are logged. |
| 10 | TFLite/ONNX export and target stubs | Pending | Mobile and edge contract checks load the exported model. |
| 11 | Flutter MERIDIAN UI | Pending | Every specified state is reachable and developer telemetry is live. |
| 12 | Reproducible evaluation deliverable | Pending | Figure, metrics table, and update-rate report are generated. |
