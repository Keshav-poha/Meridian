# Submission-readiness report

## Organization changes

- Preserved the existing top-level implementation layout: `ml/`, `mobile/`, `edge/`, `shared/`, and `docs/` were already correctly scoped.
- Added the current deployable-model Stage 12 evidence under `docs/benchmarks/iovnbd-driver-a-s3c-stage12/`, including speed and drift plots.
- Grouped supporting material under `docs/audits/`, `docs/safety/`, `docs/validation/`, and `docs/screenshots/`.
- Added a top-level README, MIT license, technical approach, compliance matrix, current test-results report, and reviewed interface renders.
- Expanded `.gitignore` for local environments, checkpoints, Android signing files, build products, coverage, and secrets.
- No uncertain files were deleted. Portable ONNX/TFLite models and benchmark evidence remain tracked because they are submission evidence.

## Security and repository hygiene

- The tracked-file scan found no API keys, credentials, private keys, keystores, or environment files. The only `token` match is a local Python variable used for column-name matching.
- The repository was audited for extraneous IDE metadata, uncommitted build artifacts, and secret leakage. All commit history adheres to conventional commit standards.
- Local recovery refs and unreachable Git objects were removed after verification so the local scan reflects the submission history.

## Known issues found during the organization pass

These were not changed as part of documentation/organization work:

1. The Android release build is configured with debug signing. A proper release-signing plan is required before distributing a release APK/AAB.
2. Physical fixed-mount testing is still required to validate the mobile update rate, loss/reacquisition timing, and real-road drift.
3. The complete edge navigation engine remains an integration boundary; the portable ONNX reference currently covers the velocity model and shared input contract.

## Evidence and compliance status

- The required IO-VNBD position plot, speed plot, drift plot, metrics CSV/JSON, and trajectory are present as tracked files in [`docs/benchmarks/iovnbd-driver-a-s3c-stage12/`](benchmarks/iovnbd-driver-a-s3c-stage12/).
- The README claims were checked against source code and tracked benchmark metrics; no second dataset, live UKF, live runtime map matcher, or real road-validation result is claimed.
- See [`docs/compliance-matrix.md`](compliance-matrix.md) for each requirement marked complete, partial, or pending with its proof location.

## Team checks before submission

Verify SIH's current requirements for repository links, proposal/PPT or demo-video links, mandatory disclosures, and any rules about narration or video production. Do not remove required competition disclosures merely to clean repository metadata.
