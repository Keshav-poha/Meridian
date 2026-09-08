# MERIDIAN Submission Checklist

## Repository and documentation

- [x] Root project overview and technical architecture are present.
- [x] SIH submission document index, project report, requirement coverage, architecture, feature summary, evaluation report, deployment guide, and checklist are present in `submission/`.
- [x] The repository states implementation boundaries for map matching, live fusion, edge navigation, and physical field validation.
- [x] OpenStreetMap, IO-VNBD, STRIDE, and software dependency notices are documented.
- [x] OpenStreetMap attribution is visible in the mobile map view.

## Model and benchmark evidence

- [x] `shared/models/velocity_cnn.onnx` is tracked with a model manifest and normalization file.
- [x] Matching TFLite model and manifest are included for the Android application.
- [x] Stage 12 evaluates the current deployable ONNX model rather than a legacy training artifact.
- [x] The current held-out IO-VNBD Driver A S3c evidence includes position, speed, and drift plots.
- [x] Metrics CSV, metrics JSON, and sample trajectory CSV are tracked with the benchmark output.
- [x] The current offline result is 5.08% endpoint drift over the selected 59.9-second GNSS blackout, below the 10% SIH ceiling.

## Mobile application

- [x] Flutter analysis and test suite pass.
- [x] Android debug and release APK builds complete.
- [x] The release APK has been installed and launched on a connected Android device.
- [x] Android high-rate sensor permission is declared.
- [x] Navigation, GNSS-loss handling, Developer Mode, confidence telemetry, and trip recording are included.
- [ ] Configure a team-owned production signing key before public distribution or app-store upload.
- [ ] Run and record the fixed-mount real-road validation protocol.

## Edge engine

- [x] Python ONNX Runtime reference accepts validated 100 Hz and 200 Hz external IMU input.
- [x] Edge unit tests cover frame validation, fixed windows, timestamp gaps, and prediction status.
- [x] The repository accurately describes `edge/cpp` as an integration seam.
- [ ] Integrate high-rate calibration, INS, fusion, and map matching for a complete FOG-grade edge navigation engine.

## Evaluator quick start

```powershell
$env:PYTHONPATH = "ml/src;edge/python/src"
py -3.13 ml/scripts/stage12_evaluate.py

cd mobile
flutter analyze
flutter test
flutter build apk --release
```

## Before uploading to the SIH portal

1. Confirm the current portal's required repository, proposal, presentation, demo-video, and disclosure formats.
2. Confirm the release APK was built from the intended commit and signed with the intended team key.
3. Re-run Stage 12 if the deployed ONNX model changes. The benchmark metrics and model hash must be regenerated together.
4. Add only reviewed screenshots, field logs, and team-approved media.
5. Do not present offline replay measurements as fixed-mount real-road results.
