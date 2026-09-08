# MERIDIAN SIH 26168 Project Report

## Project identity

| Item | Details |
| --- | --- |
| Problem statement | Smart India Hackathon 26168: AI-ML based Intelligent Dead Reckoning system for seamless navigation |
| Organization | Indian Space Research Organisation, Department of Space |
| Theme | Smart Vehicles |
| Category | Software |
| Project | MERIDIAN |

## Project summary

MERIDIAN is a smartphone-first Intelligent Dead Reckoning system for road navigation during GNSS loss. It uses the phone's accelerometer, gyroscope, magnetometer, and GNSS observations without a vehicle OBD-II connection. When GNSS is available, the runtime establishes a quality-gated aided state. When an outage occurs, it preserves the last valid speed and course, removes gravity, transforms IMU measurements into the vehicle frame, and continues the trajectory through non-holonomic dead reckoning. A bounded learned velocity prior can make only a rate-limited correction to the GNSS-anchored speed state.

The system has two deployment paths. The Android application runs the TFLite model on live phone sensors and presents GNSS-aided, dead-reckoning, and recovery states. The Python edge reference accepts validated external IMU frames and runs the same ONNX model from 100 Hz or 200 Hz input streams. Both paths use the same feature order, normalization values, and model metadata.

## Problem addressed

Satellite positioning can become unavailable in tunnels, underpasses, parking structures, dense urban corridors, and heavily obstructed roads. A phone mounted in a vehicle is also exposed to vibration, acceleration, braking, potholes, and changes in mount orientation. These conditions make direct double integration of consumer IMU data unreliable. MERIDIAN addresses this by combining mount calibration, gravity compensation, vehicle-frame processing, motion-quality gates, a bounded velocity model, GNSS anchoring, and vehicle kinematic constraints.

## Measured offline result

The current deployable model was evaluated on the recording-disjoint IO-VNBD Driver A S3c drive. GNSS was masked for 59.9 seconds after calibration was restricted to samples before the outage. During the masked interval, reference labels were excluded from the model and inertial propagation inputs.

| Method | Reference distance | Endpoint error | Drift | Replay rate |
| --- | ---: | ---: | ---: | ---: |
| Forward-acceleration INS | 1,867.62 m | 96.34 m | 5.16% | 10 Hz |
| GNSS-anchored INS with learned rate correction | 1,867.62 m | 94.88 m | **5.08%** | 10 Hz |

The measured 5.08% endpoint drift is below the SIH offline threshold of 10%. The benchmark is reproducible through the tracked evaluator and evidence files. It does not replace fixed-mount road testing across phones, vehicles, and physical GNSS outages.

## Delivered components

| Deliverable | Current implementation |
| --- | --- |
| Android mobile application | Flutter application with live phone sensor input, TFLite inference, navigation display, GNSS-loss handling, Developer Mode, and trip recording. |
| Training and evaluation pipeline | Python modules for IO-VNBD synchronization, calibration, feature generation, model training, export, and held-out replay. |
| Portable model artifacts | Hash-bound ONNX and TFLite exports with shared normalization and model manifests. |
| Edge reference | Python ONNX Runtime velocity-inference reference for validated 100 Hz or 200 Hz external IMU streams. |
| Benchmark evidence | Position, speed, and drift SVG plots; CSV and JSON metrics; source/model provenance; and sample trajectory data. |
| Validation support | Developer Mode recorder, manual GNSS-outage simulator, live-drive scoring script, and negative-motion data preparation tooling. |

## Current scope

MERIDIAN has a verified offline velocity and GNSS-anchored dead-reckoning result, a working Android application, and a portable velocity-inference reference. The following boundaries are kept explicit:

- The mobile runtime has a conservative local road-segment constraint for dead reckoning, but the HMM matcher remains offline and edge map matching is not yet integrated.
- The current live fusion state is GNSS-anchored inertial integration with bounded learned residuals. It is not a completed live EKF or UKF measurement-update implementation.
- The 10 Hz mobile loop is configured in the application. The external-IMU reference accepts 200 Hz input, but a complete 200 Hz FOG navigation engine remains an integration task.
- A fixed-mount real-road blackout evaluation is still needed for physical drift, update-rate, and transition-latency claims.

## Team

| Member | Contribution area |
| --- | --- |
| Keshav | Mobile application and runtime engineering |
| Aryan6600 and Saanvi-Tayal | Model training and evaluation pipeline |
| crazysoulyt123 | UI and UX |
| its-anshika-sharma | Research and business work |
| ridhijain001 | Presentation and non-code deliverables |

## Evidence links

- [Requirement coverage](PROBLEM_STATEMENT_COVERAGE.md)
- [System architecture](SYSTEM_ARCHITECTURE.md)
- [Evaluation and validation](EVALUATION_AND_VALIDATION.md)
- [Current benchmark evidence](../docs/benchmarks/README.md)
