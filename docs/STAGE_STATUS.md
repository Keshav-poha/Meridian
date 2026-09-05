# MERIDIAN build milestones

| Stage | Scope | Status | Required validation |
| --- | --- | --- | --- |
| 1 | Repository scaffold and shared contracts | Complete | Contract is valid JSON and all target roots exist. |
| 2 | IO-VNBD ingestion, synchronization, and sanity plot | Complete | `M (Driver B)` 5,000-row source subset yields 4,999 synchronized 10 Hz samples, 2,490 2-second windows (`20 × 9`), and `ml/reports/stage2/sanity.svg`. |
| 3 | Static/dynamic mounting calibration | Complete | Driver B fit uses 27 idle and 3,337 turn samples: gravity residual 0.00°, roll 0.02°, pitch −0.04°, yaw 48.99°, turn-kinematics correlation 0.467. |
| 4 | Classical strapdown DR plus NHC | Complete | Driver B 60-second blackout: 394.6 m travelled, 846.9 m end error, 214.6% drift, 10 Hz. This deliberate no-ML baseline is the number Stage 6 must beat. |
| 5 | Learn velocity estimator | Complete | 961-parameter 1-D CNN on Driver B temporal split over gravity-compensated, vehicle-frame inputs: 2.73 m/s test MAE versus 10.18 m/s classical implied-speed MAE. |
| 6 | Use learned speed in DR | Complete | On the held-out 60-second blackout, learned speed reduces end error from 460.8 m (68.1%) to 107.9 m (15.9%) at 10 Hz. |
| 7 | OSM map matching | Complete | Cached 1,438-segment OSM extract plus Viterbi HMM reduces held-out endpoint error from 107.89 m to 103.22 m (15.94% to 15.25% drift). |
| 8 | GNSS+INS fusion and masked outages | Complete | Held-out 60-second GNSS mask with the calibrated feature contract: 676.9 m travelled, 53.50 m end error, 7.90% drift at 10 Hz — passes the <10% target. |
| 9 | Mode-switch blending | Complete | GNSS loss/reacquisition decision path: 0.026 ms maximum; correction blending preserves the normal 1.0 m / 10 Hz display step. |
| 10 | TFLite/ONNX export and target stubs | Complete | `shared/models` contains a 5,387-byte ONNX model and 10,276-byte float32 TFLite model for the calibrated feature contract. PyTorch parity error is ≤5.96e−8 (ONNX) and ≤8.94e−8 (TFLite); the Python ONNX Runtime reference sustains 2,649 Hz when fed a 200 Hz calibrated synthetic IMU stream. The exported TFLite model is compiled into the Flutter debug APK. |
| 11 | Flutter MERIDIAN UI | Complete | Android debug APK builds successfully (173,619,646 bytes) with Flutter 3.47.2; `flutter analyze` is clean and the live-engine controller test passes. Splash, map/navigation, GNSS-loss state, Developer Mode, and More are present. The device sensor/GNSS streams feed the developer cards; a physical Android run remains required to validate permissions, sensor rates, and map-network availability. |
| 12 | Reproducible evaluation deliverable | Complete | `docs/evaluation/stage12/` contains a ground-truth-versus-inference SVG, trajectory CSV, and metrics table. The held-out 60-second Driver B mask travels 676.92 m; adaptive GNSS+INS replay ends 53.50 m from ground truth (7.90% drift) at 10 Hz, passing the <10% target. |
