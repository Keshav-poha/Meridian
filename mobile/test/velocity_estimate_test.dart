import 'package:flutter_test/flutter_test.dart';
import 'package:meridian/services/tflite_velocity_estimator.dart';
import 'package:meridian/services/velocity_model_quality.dart';

void main() {
  test('rejects an impossible raw CNN speed before navigation can use it', () {
    const estimate = VelocityEstimate(
      speedMps: 141.9,
      quality: VelocityModelQuality(0.1, 0.2),
      mountCalibrated: true,
      mountConfidence: 1,
      mountDegraded: false,
      motionAnomaly: false,
      sensorsFresh: true,
      vehicleYawRateRps: 0,
    );

    expect(estimate.hasPlausibleSpeed, isFalse);
    expect(estimate.isTrusted, isFalse);
  });
}
