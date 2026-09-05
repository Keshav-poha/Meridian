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
    for (var index = 0; index < 5; index++) {
      preprocessor.setGnssReference(speedMps: 5, headingDeg: 0);
    }
    final frame = preprocessor.update(
      accelerometer: const Axis3(2, 0, 9.80665),
      gyroscope: const Axis3(0.1, 0.2, 0.3),
      magnetometer: const Axis3(30, 0, 0),
      timestamp: start.add(const Duration(milliseconds: 10)),
    );

    expect(frame.mountCalibrated, isTrue);
    expect(frame.linearAcceleration.x, closeTo(1.92, 0.03));
    expect(frame.linearAcceleration.y, closeTo(0, 0.03));
    expect(frame.linearAcceleration.z, closeTo(0, 0.03));
    expect(frame.gyroscope.x, closeTo(0.1, 0.003));
    expect(frame.magneticDirection.x, closeTo(1, 0.001));
  });
}
