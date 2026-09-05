import 'package:flutter_test/flutter_test.dart';
import 'package:meridian/services/ins_speed_filter.dart';

void main() {
  test('anchors to GNSS then propagates gravity-compensated acceleration', () {
    final filter = InsSpeedFilter();
    filter.anchorGnssSpeed(12);

    final speed = filter.propagate(
      forwardAccelerationMps2: 2,
      dtSeconds: 0.1,
      cnnSpeedMps: null,
      cnnTrusted: false,
    );

    expect(speed, closeTo(12.2, 1e-9));
  });

  test('a constant trusted CNN cannot pull an anchored speed toward itself',
      () {
    final filter = InsSpeedFilter();
    filter.anchorGnssSpeed(12);

    final first = filter.propagate(
      forwardAccelerationMps2: 0,
      dtSeconds: 0.1,
      cnnSpeedMps: 45,
      cnnTrusted: true,
    );
    double? speed;
    for (var index = 0; index < 600; index++) {
      speed = filter.propagate(
        forwardAccelerationMps2: 0,
        dtSeconds: 0.1,
        cnnSpeedMps: 45,
        cnnTrusted: true,
      );
    }

    // A 45 m/s model output may be in range, but a flat model output contains
    // no evidence of acceleration and cannot produce a physical speed jump.
    expect(first, closeTo(12, 1e-9));
    expect(speed, closeTo(12, 1e-9));
  });

  test('uses a bounded CNN velocity change as a small acceleration residual',
      () {
    final filter = InsSpeedFilter();
    filter.anchorGnssSpeed(12);
    filter.propagate(
      forwardAccelerationMps2: 0,
      dtSeconds: 0.1,
      cnnSpeedMps: 12,
      cnnTrusted: true,
    );

    final speed = filter.propagate(
      forwardAccelerationMps2: 0,
      dtSeconds: 0.1,
      cnnSpeedMps: 14,
      cnnTrusted: true,
    );

    // The 2 m/s model jump is capped as a 4 m/s² residual for 100 ms, then
    // admitted at a conservative gain: 12 + 0.4 * 0.12.
    expect(speed, closeTo(12.048, 1e-9));
  });

  test('never exceeds the shared 45 m/s vehicle contract', () {
    final filter = InsSpeedFilter();
    filter.anchorGnssSpeed(44.9);

    final speed = filter.propagate(
      forwardAccelerationMps2: 100,
      dtSeconds: 0.1,
      cnnSpeedMps: 45,
      cnnTrusted: true,
    );

    expect(speed, lessThanOrEqualTo(InsSpeedFilter.maximumSpeedMps));
  });
}
