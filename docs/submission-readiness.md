# Submission-readiness report

## Organization changes

- Preserved the existing top-level implementation layout: `ml/`, `mobile/`, `edge/`, `shared/`, and `docs/` were already correctly scoped.
- Moved the tracked Stage 12 evidence to `docs/benchmarks/iovnbd-driver-b-stage12/` and updated its default generator path and documentation links.
- Grouped supporting material under `docs/audits/`, `docs/safety/`, `docs/validation/`, and `docs/screenshots/`.
- Added a top-level README, MIT license, technical approach, compliance matrix, current test-results report, and a reviewed-screenshot placeholder directory.
- Expanded `.gitignore` for local environments, checkpoints, Android signing files, build products, coverage, secrets, and local coding-tool state.
- No uncertain files were deleted. Portable ONNX/TFLite models and benchmark evidence remain tracked because they are submission evidence.

## Security and repository hygiene

- The tracked-file scan found no API keys, credentials, private keys, keystores, or environment files. The only `token` match is a local Python variable used for column-name matching.
- A machine-specific local runtime path was removed from documentation. No tracked tooling-specific metadata was found; `.meridian/` is ignored for future local state.
- Commit messages reviewed are professional and describe changes rather than change history. Authorship was inspected but not rewritten. The reviewed ML commit retains its requested Aryan and Saanvi co-author trailers.
- No local recovery refs or unreachable Git objects were deleted. They are not part of a fresh clone and removing them would be destructive.

## Known issues found during the organization pass

These were not changed as part of documentation/organization work:

1. `ml/src/idr_ml/__init__.py` declares contract version `0.1.0`, while the shared feature specification and portable model manifests use `0.2.0`. The current validator does not compare those values.
2. The Android release build is configured with debug signing. A proper release-signing plan is required before distributing a release APK/AAB.
3. The mobile map uses OpenStreetMap tiles but needs a visible `© OpenStreetMap contributors` attribution before public/demo use.
4. The mobile runtime needs a native Android GNSS-provider/satellite-status bridge before a quality Android location can be claimed as satellite-verified GNSS. Until then, cache and one-shot locations are display-only and startup requires two fresh quality stream fixes before it can establish a DR origin.
5. The GNSS bootstrap/runtime fix needs a final connected-phone verification for coarse bootstrap, aid-quality location, and transition into GNSS-aided mode.

## Evidence and compliance status

- The required IO-VNBD position plot, metrics CSV/JSON, and trajectory are present as tracked files in [`docs/benchmarks/iovnbd-driver-b-stage12/`](benchmarks/iovnbd-driver-b-stage12/).
- The README claims were checked against source code and tracked benchmark metrics; no second dataset, live UKF, live runtime map matcher, or real road-validation result is claimed.
- See [`docs/compliance-matrix.md`](compliance-matrix.md) for each requirement marked complete, partial, or pending with its proof location.

## Team checks before submission

Verify SIH's current requirements for repository links, proposal/PPT or demo-video links, mandatory disclosures, and any rules about narration or video production. Do not remove required competition disclosures merely to clean repository metadata.
