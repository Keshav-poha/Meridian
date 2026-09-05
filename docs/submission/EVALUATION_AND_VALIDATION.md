# MERIDIAN Evaluation and Validation

## Evaluation objective

The Stage 12 evaluator measures endpoint drift during a simulated GNSS outage using the exact ONNX model exported for deployment. It validates the SIH offline criterion that dead-reckoning drift should remain below 10% of distance travelled during the selected deficit interval.

## Dataset and split

| Role | Data source | Use |
| --- | --- | --- |
| Training | IO-VNBD Driver A S1 and S3a; one quality-audited STRIDE driving session | Vehicle telemetry is the primary speed reference. STRIDE phone GNSS speed is secondary domain supervision. |
| Validation | IO-VNBD Driver A S3b | Recording-disjoint validation during model selection. |
| Evaluation | IO-VNBD Driver A S3c | Held-out Stage 12 replay and benchmark evidence. |

The evaluator fits the phone-to-vehicle timing relationship, limits mount calibration to samples before 300 seconds, and begins the blackout at 1,200.1 seconds. During the 59.9-second masked interval, ground-truth position, speed, heading, and yaw-rate labels are excluded from the model and propagation input.

## Current benchmark result

| Method | Distance travelled | Endpoint error | Drift | Update rate | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Forward-acceleration INS | 1,867.62 m | 96.34 m | 5.16% | 10 Hz | Baseline |
| GNSS-anchored INS with learned rate correction | 1,867.62 m | 94.88 m | **5.08%** | 10 Hz | Below SIH offline 10% ceiling |

The learned correction improves the endpoint result from 96.34 m to 94.88 m on the selected held-out replay. The raw learned velocity prior is not used as an absolute speed reset because absolute speed is not directly observable from a phone IMU during steady motion. The deployed state starts from GNSS speed and limits the model to a rate correction.

## Required evidence files

| Artifact | Review purpose |
| --- | --- |
| [Position plot](../benchmarks/iovnbd-driver-a-s3c-stage12/position_plot.svg) | Ground truth, forward-acceleration INS, and GNSS-anchored INS trajectories. |
| [Speed tracking plot](../benchmarks/iovnbd-driver-a-s3c-stage12/speed_tracking_plot.svg) | Ground-truth speed and navigation speed through changing drive conditions. |
| [Drift versus distance plot](../benchmarks/iovnbd-driver-a-s3c-stage12/drift_vs_distance_plot.svg) | Cumulative drift with the dashed 10% benchmark ceiling. |
| [Metrics CSV](../benchmarks/iovnbd-driver-a-s3c-stage12/metrics.csv) | Compact numerical result table. |
| [Metrics JSON](../benchmarks/iovnbd-driver-a-s3c-stage12/metrics.json) | Machine-readable metrics, model hash, calibration evidence, and provenance. |
| [Trajectory CSV](../benchmarks/iovnbd-driver-a-s3c-stage12/trajectory.csv) | Sample-level reference and estimated trajectory data. |

## Reproduction

From the repository root, download the public Driver A S3c pair and run:

```powershell
py -3.13 ml/scripts/fetch_iovnbd_subset.py --recording driver-a-s3c
$env:PYTHONPATH = "ml/src;edge/python/src"
py -3.13 ml/scripts/stage12_evaluate.py
```

The evaluator checks model provenance before scoring and exits with a failure status if the evaluated drift is greater than or equal to 10%. It regenerates the tracked evidence files in `docs/benchmarks/iovnbd-driver-a-s3c-stage12/`.

## Engineering verification

| Area | Current check |
| --- | --- |
| ML pipeline | Contract validation and 22 unit tests pass. |
| Edge reference | 6 unit tests pass for windowing, frame validation, gap handling, and structured predictions. |
| Flutter application | Static analysis reports no issues and 19 tests pass. |
| Android package | Debug and release APK builds complete. The release package was inspected as non-debuggable and installed on a connected Android phone. |
| Model portability | TFLite and ONNX exports use the same normalization and manifest contract. |
| Edge input rate | The Python ONNX Runtime reference accepts a 200 Hz stream and profiles at about 2,763 velocity inferences per second. |

## Field-validation plan

The next validation step is a real fixed-mount drive. The Developer Mode recorder captures live IMU, GNSS reference, predicted state, confidence, and manual GNSS outage transitions. The field evaluator reports drift and switch timing for 10, 30, and 60-second intervals. The procedure is documented in [the physical-drive validation guide](../validation/real-world-validation.md).

## Interpretation limits

The 5.08% result is a reproducible offline result on one held-out recording. It does not establish lane-level accuracy, performance on all phones, or performance in actual tunnels. Real-road validation must measure mount calibration, mobile update rate, outage and recovery latency, and drift for the target device and vehicle.
