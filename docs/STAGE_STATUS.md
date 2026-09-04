# MERIDIAN build milestones

| Stage | Scope | Status | Required validation |
| --- | --- | --- | --- |
| 1 | Repository scaffold and shared contracts | Complete | Contract is valid JSON and all target roots exist. |
| 2 | IO-VNBD ingestion, synchronization, and sanity plot | Complete | `M (Driver B)` 5,000-row source subset yields 4,999 synchronized 10 Hz samples, 2,490 2-second windows (`20 × 9`), and `ml/reports/stage2/sanity.svg`. |
| 3 | Static/dynamic mounting calibration | Complete | Driver B fit uses 27 idle and 3,337 turn samples: gravity residual 0.00°, roll 0.02°, pitch −0.04°, yaw 48.99°, turn-kinematics correlation 0.467. |
| 4 | Classical strapdown DR plus NHC | Complete | Driver B 60-second blackout: 394.6 m travelled, 846.9 m end error, 214.6% drift, 10 Hz. This deliberate no-ML baseline is the number Stage 6 must beat. |
| 5 | Learn velocity estimator | Complete | 961-parameter 1-D CNN on Driver B temporal split: 2.58 m/s test MAE versus 10.18 m/s classical implied-speed MAE. |
| 6 | Use learned speed in DR | Pending | Same blackout segments report comparable drift. |
| 7 | OSM map matching | Pending | Map-matched trajectory error is compared with raw DR. |
| 8 | GNSS+INS fusion and masked outages | Pending | Blackout metrics are evaluated against the 10% drift target. |
| 9 | Mode-switch blending | Pending | Transition latency and discontinuity are logged. |
| 10 | TFLite/ONNX export and target stubs | Pending | Mobile and edge contract checks load the exported model. |
| 11 | Flutter MERIDIAN UI | Pending | Every specified state is reachable and developer telemetry is live. |
| 12 | Reproducible evaluation deliverable | Pending | Figure, metrics table, and update-rate report are generated. |
