# Physical-drive validation

MERIDIAN now has a deliberately small real-drive recorder in **Developer
Mode**. It does not change the navigation UI: choose a label, tap **Record**,
then tap **Stop & save** when the drive is complete. The saved JSONL file holds
10 Hz predictions, raw phone sensor values, calibration/health/confidence,
manual GNSS-aiding transitions, and a separately labelled physical GNSS
reference.

The reference GNSS remains in the file during the manual outage simulator so
that a later evaluator can score DR. It is never passed back into the live DR
engine while aiding is disabled. Phone GNSS is useful field reference, not
survey-grade ground truth; do not compare a field result directly with the
IO-VNBD benchmark without stating that difference.

## Fixed-mount blackout protocol

1. Firmly mount the phone in the vehicle. Do not hand-hold it for this test.
2. In Developer Mode, choose `Driving` and tap `Record`.
3. Drive normally with aiding enabled for at least 30–60 seconds. Wait until
   Developer Mode reports mount calibration and a nonzero confidence.
4. While continuing to drive safely, tap **Disable** on GPS/GNSS for one chosen
   duration: 10, 30, or 60 seconds. The physical GNSS stays reference-only.
5. Tap **Enable**, allow the smooth reacquisition to finish, then tap
   **Stop & save**. The saved path is displayed below the control.
6. Copy the JSONL log from the app documents `meridian_logs` directory to the
   repository computer and run:

```powershell
py -3.13 ml/scripts/evaluate_live_drive.py <trip-log.jsonl> --windows 10,30,60 --output-dir ml/reports/live_drive
```

The evaluator saves `live_drive_metrics.json` and
`live_drive_windows.csv`, reporting endpoint error, distance, drift percent,
update rate, confidence, and measured manual-switch latency for every available
window. A missing duration is reported as missing rather than extrapolated.

For the problem-statement target, assess an actual 60-second blackout only when
the reported reference path has enough movement. The required comparison is:

```text
drift percent = DR endpoint error / reference distance travelled × 100
```

## Required negative-motion collection

The current IO-VNBD data cannot honestly label a parked engine, pothole,
handheld shake, or loose mount. The app lets each dedicated short capture be
labelled as `Parked / idle`, `Pothole / bump`, `Handheld shake`, or `Mount
shift`. Capture each safely and separately, across more than one phone/mount,
car, and road surface; never create a hazardous driving scenario just to get a
label.

After copying those logs, prepare—not train—the future motion-suitability data:

```powershell
py -3.13 ml/scripts/prepare_motion_negatives.py <drive.jsonl> <idle.jsonl> <bump.jsonl> <handheld.jsonl> <mount-shift.jsonl> --output ml/artifacts/motion_negatives.npz
```

The command fails if any of the four real negative classes is absent. It only
creates labelled windows and a manifest; it does not silently retrain the speed
model or manufacture a robustness score. A separate classifier/head must be
trained with device/vehicle/route-held-out splits before a learned
non-navigation-motion claim is made.

## What to inspect before accepting a field run

- Prediction confidence should fall to zero for shock/mount-motion cooldown,
  stale IMU, uncalibrated/degraded mount, and unarmed vehicle motion.
- A poor or mocked GNSS fix must show as degraded and must not recalibrate the
  mount or replace the accepted aiding pose.
- The mode should become `dead_reckoning` without delay on disable and blend through
  `reacquiring` on enable, with the measured latency in the JSON report.
- A map line in the current mobile app is raw DR display. It must not be
  described as road-snapped until the runtime no-match map matcher is shipped.
