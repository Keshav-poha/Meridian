# MERIDIAN benchmark evidence

The canonical SIH 26168 offline evidence is the current deployable-model replay in [`iovnbd-driver-a-s3c-stage12/`](iovnbd-driver-a-s3c-stage12/). It evaluates the tracked `shared/models/velocity_cnn.onnx` artifact on IO-VNBD Driver A S3c, which is recording-disjoint from the model's training and validation drives.

| Metric | Result |
| --- | ---: |
| Simulated GNSS blackout | 59.9 s |
| Reference distance | 1,867.62 m |
| AI-assisted endpoint error | 94.88 m |
| AI-assisted drift | **5.08%** |
| SIH ceiling | `<10%` |
| Replay update rate | 10 Hz |

The benchmark passes the offline drift target. It is not a substitute for a fixed-mount road-drive validation.

## Reproduce

Download the public synchronized IO-VNBD Driver A S3c pair into the expected directory, then run the evaluator from the repository root:

```powershell
py -3.13 ml/scripts/fetch_iovnbd_subset.py --recording driver-a-s3c
$env:PYTHONPATH = "ml/src;edge/python/src"
py -3.13 ml/scripts/stage12_evaluate.py
```

The evaluator checks the deployable ONNX manifest and writes the following deterministic evidence files to `iovnbd-driver-a-s3c-stage12/`:

- [`position_plot.svg`](iovnbd-driver-a-s3c-stage12/position_plot.svg) — ground truth and both INS trajectories.
- [`speed_tracking_plot.svg`](iovnbd-driver-a-s3c-stage12/speed_tracking_plot.svg) — ground-truth speed and the AI-assisted INS state across the selected acceleration, cruise, and braking changes.
- [`drift_vs_distance_plot.svg`](iovnbd-driver-a-s3c-stage12/drift_vs_distance_plot.svg) — cumulative error with a dashed 10% ceiling.
- [`metrics.csv`](iovnbd-driver-a-s3c-stage12/metrics.csv) and [`metrics.json`](iovnbd-driver-a-s3c-stage12/metrics.json) — results, provenance, and acceptance status.
- [`trajectory.csv`](iovnbd-driver-a-s3c-stage12/trajectory.csv) — sample-by-sample data for independent inspection.

![Ground truth and INS trajectory](iovnbd-driver-a-s3c-stage12/position_plot.svg)

![Ground-truth speed and AI-assisted INS speed](iovnbd-driver-a-s3c-stage12/speed_tracking_plot.svg)

![Cumulative drift against the 10% ceiling](iovnbd-driver-a-s3c-stage12/drift_vs_distance_plot.svg)

The older [`iovnbd-driver-b-stage12/`](iovnbd-driver-b-stage12/) directory is retained solely as legacy evidence for an earlier feature contract. It is not the default evaluator output and must not be used to describe the deployable v2 artifact.
