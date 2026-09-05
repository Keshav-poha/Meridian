# Stage 12 — reproducible IO-VNBD evaluation

This folder is the final offline validation deliverable for MERIDIAN. It was
generated with:

```powershell
$env:PYTHONPATH = 'ml/.vendor;ml/src'
py -3.13 ml/scripts/stage12_evaluate.py
```

The replay uses the public IO-VNBD `M (Driver B)` phone/vehicle pair. It aligns
the first 5,000 source rows at 10 Hz, calibrates from the prior pipeline stage,
and masks GNSS over the held-out interval from 430 s to 490 s. The residual
calibration uses only the preceding 60 seconds; ground truth is used solely for
the recorded evaluation metrics and plot.

| Method | Travelled | Endpoint error | Drift | Position update rate |
| --- | ---: | ---: | ---: | ---: |
| Classical NHC | 676.92 m | 460.84 m | 68.08% | 10 Hz |
| Learned-speed NHC | 676.92 m | 107.89 m | 15.94% | 10 Hz |
| Masked-GNSS adaptive fusion | 676.92 m | 53.50 m | **7.90%** | 10 Hz |

The final row passes the required less-than-10-percent dead-reckoning-drift
benchmark. This is a replay result, not a substitute for the upcoming
on-device live-phone validation.

Files:

- `position_plot.svg` — ground truth, learned DR, and fusion trajectory in a
  common local-ENU coordinate frame.
- `metrics.csv` and `metrics.json` — tabular and machine-readable measures.
- `trajectory.csv` — sample-by-sample coordinates for independent plotting.
