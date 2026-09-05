import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:meridian/domain/telemetry_snapshot.dart';
import 'package:meridian/services/trip_recorder.dart';

void main() {
  test('encodes a portable sample without invalid NaN JSON values', () {
    final record = TripLogEncoder.sample(
      TelemetrySnapshot(
        timestamp: DateTime.utc(2026, 9, 5, 12),
        mode: NavigationMode.deadReckoning,
        speedMps: 4.2,
        modelSpeedMps: 4.0,
        velocityModelTrusted: true,
        mountCalibrated: true,
        stationary: false,
        vehicleMotionArmed: true,
        headingDeg: 90,
        latitudeDeg: 28.6139,
        longitudeDeg: 77.209,
        accuracyM: double.nan,
        positionErrorM: double.nan,
        driftPercent: double.nan,
        accelerometer: const Axis3(0.1, 0.2, 9.8),
        gyroscope: const Axis3(0.01, 0.02, 0.03),
        magnetometer: const Axis3(22, -4, 41),
        gnssAvailable: false,
        gnssEnabled: false,
        lastAccurateLatitudeDeg: 28.6138,
        lastAccurateLongitudeDeg: 77.2089,
        actualLatitudeDeg: 28.6140,
        actualLongitudeDeg: 77.2091,
        deadReckoningDistanceM: 50,
        deadReckoningElapsed: const Duration(seconds: 12),
        predictionConfidence: .72,
        predictionConfidenceReason: 'Calibrated vehicle-frame IMU prediction',
      ),
      label: 'drive',
    );

    final encoded = jsonEncode(record);
    expect(encoded, isNot(contains('NaN')));
    expect(record['mode'], 'dead_reckoning');
    expect(record['schema_version'], TripRecorder.schemaVersion);
    final metrics = record['metrics'] as Map<String, Object?>;
    expect(metrics['position_error_m'], isNull);
    expect(metrics['prediction_confidence'], .72);
    final position = record['position'] as Map<String, Object?>;
    expect(position['east_m'], isNull);
  });
}
