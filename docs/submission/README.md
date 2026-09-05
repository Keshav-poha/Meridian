# MERIDIAN SIH 26168 Submission Documents

This directory is the review entry point for MERIDIAN, an Intelligent Dead Reckoning and GNSS fusion system developed for Smart India Hackathon Problem Statement 26168. It collects the material needed to understand the system, inspect its evidence, run the software, and distinguish demonstrated capabilities from pending field work.

| Document | Purpose |
| --- | --- |
| [Project report](PROJECT_REPORT.md) | Concise project context, solution summary, measured result, deliverables, and current scope. |
| [Word project report](MERIDIAN_SIH_26168_PROJECT_REPORT.docx) | Five-page portable report for review or portal attachment. |
| [Problem statement coverage](PROBLEM_STATEMENT_COVERAGE.md) | Requirement-by-requirement mapping to implementation and evidence. |
| [System architecture](SYSTEM_ARCHITECTURE.md) | Mobile, training, shared-model, and edge-engine architecture. |
| [Features and additional engineering](FEATURES_AND_ADDITIONAL_ENGINEERING.md) | Core features and work undertaken beyond the stated minimum scope. |
| [Evaluation and validation](EVALUATION_AND_VALIDATION.md) | Dataset split, benchmark method, plots, results, tests, and validation limits. |
| [Demonstration and deployment](DEMONSTRATION_AND_DEPLOYMENT.md) | Android app, edge reference, build steps, and safe demonstration flow. |
| [Submission checklist](SUBMISSION_CHECKLIST.md) | Repository, model, evidence, and presentation checklist. |

## Key evidence

The current deployable ONNX model was evaluated on the recording-disjoint IO-VNBD Driver A S3c drive with a 59.9-second simulated GNSS blackout. The replay reported 94.88 m endpoint error over 1,867.62 m, or **5.08% drift** at 10 Hz. This is below the SIH offline 10% drift ceiling. The result is an offline replay and is not presented as a fixed-mount road result.

- [Benchmark index and reproduction steps](../benchmarks/README.md)
- [Position trajectory](../benchmarks/iovnbd-driver-a-s3c-stage12/position_plot.svg)
- [Speed tracking plot](../benchmarks/iovnbd-driver-a-s3c-stage12/speed_tracking_plot.svg)
- [Drift versus distance plot](../benchmarks/iovnbd-driver-a-s3c-stage12/drift_vs_distance_plot.svg)
- [Metrics and model provenance](../benchmarks/iovnbd-driver-a-s3c-stage12/metrics.json)

## Suggested review order

1. Read the [project report](PROJECT_REPORT.md) and [problem statement coverage](PROBLEM_STATEMENT_COVERAGE.md).
2. Review the [architecture](SYSTEM_ARCHITECTURE.md) and [features](FEATURES_AND_ADDITIONAL_ENGINEERING.md).
3. Inspect the [evaluation evidence](EVALUATION_AND_VALIDATION.md) and linked benchmark files.
4. Use the [demonstration guide](DEMONSTRATION_AND_DEPLOYMENT.md) and [submission checklist](SUBMISSION_CHECKLIST.md) for the app, edge reference, and final packaging.
