# MERIDIAN reliability audit — 5 September 2026

This audit was completed before the reliability changes described below.  It
separates a demonstrated offline replay result from a claim about a live car:
the previous 7.90% value is a useful baseline, but it is not yet a
leakage-safe, multi-drive, on-road result.

## Findings and actions

1. **Phone and vehicle clocks are assumed to start together.**
   `synchronize_streams` independently zeroed both elapsed-time columns and
   interpolated them at the same index.  On the local Driver B subset, a
   speed-correlation sweep improves from 0.951 at the assumed zero lag to
   0.980 at approximately four seconds of phone delay.  That can corrupt both
   speed labels and calibration.  Detect it by fitting a bounded GNSS
   trajectory/speed lag and recording its residual.  Change: estimate and
   apply a timestamp offset before interpolation; reject a pair whose fit is
   weak instead of silently proceeding.

2. **Windows can bridge missing data, and adjacent temporal splits overlap.**
   Dropping rows and resetting the index can make a model window cross a sensor
   gap.  A two-second window with a 0.2-second stride also shares 1.8 seconds
   of samples across a naive train/validation/test boundary.  Detect timestamp
   discontinuities and intersecting source sample ranges.  Change: build
   windows only inside continuous runs and purge a full-window guard band at
   each split boundary.

3. **Calibration provenance can leak the held-out blackout.**
   The Stage 3 script calibrates from the full source subset, while the final
   blackout is later selected from that same subset.  The reported calibration
   also has only 0.467 turn-kinematics correlation, although the acceptance
   threshold is 0.2.  Detect by storing the calibration time range and
   requiring it to end before the evaluation/training boundary; reject weak
   turn fits.  Change: make calibration time-bounded and provenance-bearing,
   then retrain and remeasure with only pre-test information.

4. **Live mount calibration is too easy to obtain and trusts a disturbed
   magnetometer.**  Five moving GNSS callbacks can mark a phone calibrated,
   with no GNSS accuracy/heading quality, duration, circular-residual, or
   heading-coverage check.  A short lucky segment, cabin magnetic disturbance,
   or a mocked/poor fix can therefore create a wrong transform.  Detect by
   tracking sample count, elapsed calibration time, candidate-yaw dispersion,
   course coverage, and GNSS quality.  Change: require a sustained,
   consistent calibration evidence set; expose its continuous confidence; keep
   the speed model unavailable while it is incomplete or degraded.

5. **A mount shift is not detected after calibration.**  A suction cup
   loosening, a phone sliding, or prolonged thermal attitude drift leaves the
   old transform active indefinitely.  Detect persistent disagreement between
   new good GNSS-course/magnetic yaw candidates and the accepted transform, as
   well as large abrupt attitude/shock events.  Change: add a mount-integrity
   monitor that degrades confidence and stops DR velocity until recalibration
   succeeds; it must never silently replace a transform on one event.

6. **The live heading path uses the raw phone Z gyroscope.**  The velocity
   model receives a vehicle-frame gyroscope, but `LiveIdrEngine` integrated
   the raw phone Z axis for heading.  Tilted/rotated mounts therefore steer the
   trajectory incorrectly even after model calibration.  Detect by comparing
   vehicle-frame and raw-axis yaw during a known turn.  Change: propagate the
   calibrated vehicle-frame yaw rate from the common preprocessor and use
   quality-gated GNSS course only while actually moving.

7. **Low-quality GNSS can become an authoritative state.**  A merely fresh
   location callback was used as position, speed, heading, and calibration
   reference without accuracy, speed/heading accuracy, or mock-fix checks.
   Detect stale, coarse, invalid, or inconsistent fixes and compare their
   innovation with the predicted position.  Change: gate navigation updates
   and calibration references by explicit GNSS quality; retain poor fixes only
   as raw diagnostic data.

8. **The motion gate is vulnerable after a real drive has armed it.**  Five
   instantaneous quiet samples identify stationary motion, while a single
   noisy GNSS speed can arm the latch.  During an outage after prior travel,
   door slams, engine vibration, potholes, or handheld movement may still
   yield model speed.  Detect rolling acceleration/gyro energy, shock events,
   repeated zero-speed GNSS evidence, and sensor saturation.  Change: use
   hysteretic evidence windows, a shock/handling hold, and a mount-integrity
   disarm policy rather than a one-sample latch.

9. **The model quality signal is binary and incomplete.**  The p95 normalized
   feature magnitude is useful but hides isolated shocks and does not account
   for calibration quality, GNSS quality, or motion integrity.  Detect maximum
   feature excursions plus the component scores.  Change: emit a 0–1
   prediction confidence with explicit components; show it beside Position
   Comparison in Developer Mode and suppress navigation below a conservative
   threshold.

10. **Natural GNSS loss/recovery differs from the simulator.**  DR distance is
    accumulated only for the manual-disable path, and a natural recovery does
    not reliably begin the same blend state.  Detect transition tests for both
    sources of loss and assert monotonic DR distance plus bounded display
    steps.  Change: use a single loss/reacquisition state machine driven by
    usable GNSS quality, not by the UI toggle alone.

11. **Map matching can confidently select a nearby wrong road.**  The HMM
    considers nearest segments without a maximum snap distance, road
    direction/topology, or ambiguity margin; Euclidean transition distance is
    not a route constraint.  Detect candidate distance, heading disagreement,
    score margin, and impossible road-to-road jumps.  Change: never snap when
    candidates exceed a maximum distance or the winner is ambiguous; preserve
    the inertial path and publish a low map-match confidence.  A full directed
    road-graph router is still needed before mobile map matching can be called
    production ready.

12. **The replay “fusion” is an adaptive residual propagator, not a live
    quality-aware UKF.**  It fits pre-outage residuals from dataset ground
    truth fields, carries a covariance variable that is not returned or used
    for a measurement update, and has no GNSS innovation gate.  Detect
    correlation/conditioning of residual fits and GNSS innovation outliers.
    Change: retain only well-conditioned pre-loss adaptation, make uncertainty
    observable, and keep the offline result labelled as replay-only until a
    live EKF/UKF measurement-update path is implemented. The current replay
    now withholds both speed and yaw residuals when their pre-loss correlation
    is weak, instead of applying a fixed scale that can worsen DR.

13. **The edge target is velocity inference rather than a full IDR engine.**
    It accepts already calibrated values, lacks mount/quality/fusion logic,
    interpolates across long input gaps, and the C++ target is an interface
    placeholder.  Detect non-finite axes, magnetic norm errors, and timestamp
    gaps.  Change: reset the edge window on gaps and validate its input
    contract; full FOG-grade calibration/fusion remains a separate required
    implementation.

14. **The dataset does not contain labelled real handheld, pothole, bump, or
    mount-shift negatives.**  IO-VNBD supplies genuine low-speed/idle examples
    but does not establish that the model can reject the other conditions.
    Detect it with class-balanced held-out labels collected on real phones.
    Change: up-weight verified stationary windows and add data-collection plus
    training-ingestion support.  Do not claim the model has learned the
    missing classes until those labelled recordings exist.

15. **There is no end-to-end real-drive validation artifact.**  The previous
    Android check was a stationary smoke test, not a mounted-car blackout
    measurement.  Detect it by recording raw sensors, GNSS, prediction,
    confidence, and mode at 10 Hz or better, then replaying 10/30/60-second
    masked intervals against retained good GNSS.  Change: add an in-app trip
    recorder/export and a desktop evaluator.  The next validation must be a
    fixed-mount road run, not another synthetic claim.

16. **The documented evaluation is not reproducible from a clean checkout.**
    Its PyTorch artifact is ignored while Stage 12 defaults to that artifact.
    Detect with a fresh clone reproduction test.  Change: track the compact
    evaluation artifact (or regenerate it in a documented command) and bind
    it to feature-spec/calibration provenance.

17. **GNSS recovery could reuse a cached pre-outage fix.**  The former
    freshness watchdog treated a recent last-known fix as valid immediately
    after the user re-enabled GNSS, even though it may have been collected
    before the blackout.  That can start a blend toward a stale position and
    create a visible backward jump.  Detect it by simulating an enable within
    the freshness timeout and checking the fix timestamp against the
    loss/enable boundary.  Change: require a valid GNSS fix timestamped after
    that boundary before changing from DR to reacquiring; retain old physical
    fixes only for Developer Mode comparison.

18. **Offline blackout propagation could inspect future ground-truth fields.**
    Earlier DR helpers used `gt_speed_mps`/`gt_heading_rad` across a masked
    interval to infer initial state or gyro bias. That is not a deployable
    input contract, even when the data are only an offline proxy for GNSS.
    Detect it by stripping every `gt_*` column before propagation and requiring
    the replay to run. Change: freeze speed, heading, and gyro bias from the
    last GNSS-proxy fix at the loss boundary, pass a sensor-only blackout frame
    to every integrator, and reserve ground truth for metrics and plotting.

## Priority order

1. Correct timestamp/split/calibration leakage first, because any model metric
   derived from misaligned or leaked labels is unsafe evidence.
2. Add live quality, mount integrity, GNSS quality, calibrated heading, and a
   unified loss state machine next; these prevent a real phone from displaying
   an unsafe confident trajectory.
3. Add map-match rejection and edge gap validation, which are deterministic
   safeguards with low regression risk.
4. Add recording/evaluation and negative-example ingestion.  Actual labelled
   multi-phone/multi-mount/road recordings are required before retraining a
   trustworthy motion-rejection model.

## Implemented in this pass

- Timestamp alignment is now fitted from true timestamps and written into
  calibration, training, export, and evaluation provenance. The source phone
  GPS speed is explicitly treated as m/s after a cross-check against vehicle
  speed.
- Interpolation rejects source gaps longer than 0.25 seconds; fixed windows
  reject missing samples; Stage 5 purges a full 1.8-second overlap band on
  both temporal split boundaries.
- Calibration is bounded before the held-out blackout, and Stage 5/10/12
  reject stale or mismatched provenance. The tracked ONNX/TFLite exports are
  hash-bound to the retrained checkpoint and normalization data.
- Stage 12 now structurally drops every `gt_*` column before classical,
  learned, or guarded-fusion propagation. The one initial speed/course state
  is recorded as a loss-boundary GNSS proxy in the provenance report.
- Mobile now exposes continuous confidence, quality-gates GNSS/calibration,
  stops model DR during shock/mount-motion conditions, detects persistent
  mount disagreement, uses vehicle-frame yaw, and waits for a post-loss GNSS
  timestamp before blending back.
- The offline map matcher fails closed under distance, ambiguity, and heading
  rules; the edge velocity runtime validates sensor contracts and resets on
  >15 ms gaps at 200 Hz.
- Developer Mode records labelled physical-drive JSONL logs, and desktop
  tooling scores 10/30/60-second hidden-GNSS intervals. Collection tooling
  refuses to pretend missing negative-motion classes are training data.

## Still not evidenced

- No fixed-mount moving-car run has yet measured real phone drift, transition
  latency, or 10 Hz device output. The Android check is only a sensor/build
  smoke test.
- The present dataset has no labelled real handheld, bump, loose-mount, or
  multi-phone/multi-car negatives. The recorder makes collection possible;
  it does not make the current CNN robust to them.
- Live directed-road map matching and a real GNSS innovation-gated EKF/UKF
  remain future work. The current masked-GNSS result is guarded replay, not a
  claim of production fusion or runtime road snapping.
