import 'package:flutter_test/flutter_test.dart';
import 'package:meridian/services/road_constrained_matcher.dart';

void main() {
  test('snaps a close, heading-consistent DR pose to its road', () {
    final matcher = RoadConstrainedMatcher.forTesting(<RoadSegmentInput>[
      const RoadSegmentInput(
        wayId: 'north-south',
        startLatitude: 0.0,
        startLongitude: 0.0,
        endLatitude: 0.001,
        endLongitude: 0.0,
      ),
    ]);

    final match = matcher.constrain(
      latitude: 0.0005,
      longitude: 0.00008,
      headingDeg: 0,
      speedMps: 8,
    );

    expect(match.matched, isTrue);
    expect(match.longitude, closeTo(0, 1e-9));
    expect(match.distanceToRoadM, lessThan(18));
  });

  test('keeps a distant or heading-inconsistent pose raw', () {
    final matcher = RoadConstrainedMatcher.forTesting(<RoadSegmentInput>[
      const RoadSegmentInput(
        wayId: 'north-south',
        startLatitude: 0.0,
        startLongitude: 0.0,
        endLatitude: 0.001,
        endLongitude: 0.0,
      ),
    ]);

    final distant = matcher.constrain(
      latitude: 0.0005,
      longitude: 0.001,
      headingDeg: 0,
      speedMps: 8,
    );
    final crossStreet = matcher.constrain(
      latitude: 0.0005,
      longitude: 0.00008,
      headingDeg: 90,
      speedMps: 8,
    );

    expect(distant.matched, isFalse);
    expect(crossStreet.matched, isFalse);
  });

  test('keeps an ambiguous parallel-road candidate raw', () {
    final matcher = RoadConstrainedMatcher.forTesting(<RoadSegmentInput>[
      const RoadSegmentInput(
        wayId: 'west',
        startLatitude: 0.0,
        startLongitude: -0.00004,
        endLatitude: 0.001,
        endLongitude: -0.00004,
      ),
      const RoadSegmentInput(
        wayId: 'east',
        startLatitude: 0.0,
        startLongitude: 0.00004,
        endLatitude: 0.001,
        endLongitude: 0.00004,
      ),
    ]);

    final match = matcher.constrain(
      latitude: 0.0005,
      longitude: 0,
      headingDeg: 0,
      speedMps: 8,
    );

    expect(match.matched, isFalse);
  });
}
