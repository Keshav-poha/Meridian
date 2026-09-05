import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:latlong2/latlong.dart';
import 'package:meridian/domain/telemetry_snapshot.dart';
import 'package:meridian/services/idr_engine.dart';
import 'package:meridian/state/navigation_controller.dart';

void main() {
  test('controller publishes engine telemetry and controls the GNSS simulator',
      () async {
    final engine = _FakeEngine();
    final controller = NavigationController(engine);
    await controller.start();
    engine.emit(_snapshot());
    await Future<void>.delayed(Duration.zero);

    expect(controller.snapshot?.speedMps, 4.2);
    expect(controller.snapshot?.actualLatitudeDeg, 28.61390);
    controller.setDestination(const LatLng(28.614, 77.21));
    expect(controller.routeEditorVisible, isTrue);
    await controller.setGnssEnabled(false);
    expect(engine.gnssEnabled, isFalse);
    expect(controller.gnssEnabled, isFalse);
    controller.selectTab(MeridianTab.developer);
    expect(controller.tab, MeridianTab.developer);
    controller.dispose();
  });
}

class _FakeEngine implements IdrEngine {
  final StreamController<TelemetrySnapshot> _stream =
      StreamController<TelemetrySnapshot>.broadcast();
  bool gnssEnabled = true;
  @override
  Stream<TelemetrySnapshot> get telemetry => _stream.stream;
  @override
  Future<void> setGnssEnabled(bool enabled) async => gnssEnabled = enabled;
  @override
  Future<void> start() async {}
  @override
  Future<void> stop() async => _stream.close();
  void emit(TelemetrySnapshot value) => _stream.add(value);
}

TelemetrySnapshot _snapshot() => TelemetrySnapshot(
      timestamp: DateTime(2026, 9, 4),
      mode: NavigationMode.gnssAidedIns,
      speedMps: 4.2,
      modelSpeedMps: 4.2,
      stationary: false,
      vehicleMotionArmed: true,
      headingDeg: 135,
      latitudeDeg: 28.61390,
      longitudeDeg: 77.2090,
      accuracyM: 2.4,
      positionErrorM: 1.2,
      driftPercent: 1.72,
      accelerometer: Axis3(0.1, 0.2, 9.8),
      gyroscope: Axis3(0.01, 0.02, 0.03),
      magnetometer: Axis3(22, -4, 41),
      gnssAvailable: true,
      gnssEnabled: true,
      lastAccurateLatitudeDeg: 28.61390,
      lastAccurateLongitudeDeg: 77.2090,
      actualLatitudeDeg: 28.61390,
      actualLongitudeDeg: 77.2090,
      deadReckoningDistanceM: 50,
      deadReckoningElapsed: Duration(seconds: 12),
    );
