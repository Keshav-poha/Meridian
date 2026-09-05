import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:meridian/domain/telemetry_snapshot.dart';
import 'package:meridian/services/imu_preprocessor.dart';

void main() {
  test('removes gravity and resolves yaw from moving GNSS course', () {
    final preprocessor = VehicleFramePreprocessor();
    final start = DateTime(2026, 9, 5);
    // Seed a stationary, level gravity estimate and phone magnetic north.
    preprocessor.update(
      accelerometer: const Axis3(0, 0, 9.80665),
      gyroscope: const Axis3(0, 0, 0),
      magnetometer: const Axis3(30, 0, 0),
      timestamp: start,
    );
    // A valid calibration needs duration and course coverage, not five lucky
    // same-heading location callbacks. Keep the synthetic phone and vehicle
    // yaw aligned so every accepted candidate has the same mount yaw.
    for (var index = 0; index < 20; index++) {
      final heading = index * 2.0;
      final radians = heading * math.pi / 180;
      final timestamp = start.add(Duration(seconds: index));
      preprocessor.update(
        accelerometer: const Axis3(0, 0, 9.80665),
        gyroscope: const Axis3(0, 0, 0),
        magnetometer: Axis3(30 * math.cos(radians), 30 * math.sin(radians), 0),
        timestamp: timestamp,
      );
      preprocessor.setGnssReference(
        speedMps: 5,
        headingDeg: heading,
        accuracyM: 5,
        timestamp: timestamp,
      );
    }
    final frame = preprocessor.update(
      accelerometer: const Axis3(2, 0, 9.80665),
      gyroscope: const Axis3(0.01, 0.02, 0.03),
      magnetometer: const Axis3(30, 0, 0),
      timestamp: start.add(const Duration(seconds: 20)),
    );

    expect(frame.mountCalibrated, isTrue);
    expect(frame.linearAcceleration.x, closeTo(1.92, 0.03));
    expect(frame.linearAcceleration.y, closeTo(0, 0.03));
    expect(frame.linearAcceleration.z, closeTo(0, 0.03));
    expect(frame.gyroscope.x, closeTo(0.01, 0.003));
    expect(frame.magneticDirection.x, closeTo(1, 0.001));
  });

  test('does not calibrate from a short straight GNSS segment', () {
    final preprocessor = VehicleFramePreprocessor();
    final start = DateTime(2026, 9, 5);
    preprocessor.update(
      accelerometer: const Axis3(0, 0, 9.80665),
      gyroscope: const Axis3(0, 0, 0),
      magnetometer: const Axis3(30, 0, 0),
      timestamp: start,
    );
    for (var index = 0; index < 5; index++) {
      final timestamp = start.add(Duration(seconds: index));
      preprocessor.setGnssReference(
        speedMps: 5,
        headingDeg: 0,
        accuracyM: 5,
        timestamp: timestamp,
      );
    }

    expect(preprocessor.isMountCalibrated, isFalse);
    expect(preprocessor.mountState, MountCalibrationState.collecting);
  });
}
