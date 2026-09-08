import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:path_provider/path_provider.dart';

/// On-device, fail-closed road constraint for dead reckoning.
///
/// Road geometry is obtained only while GNSS is available, retained locally,
/// and never required for an inertial estimate to be published. During a
/// blackout, a position is snapped only when a nearby road is unambiguous and
/// consistent with the current travel heading; otherwise the raw INS pose is
/// retained unchanged.
class RoadConstrainedMatcher {
  static const _queryRadiusM = 1200.0;
  static const _refreshDistanceM = 750.0;
  static const _maximumSnapDistanceM = 18.0;
  static const _ambiguityMarginM = 8.0;
  static const _maximumHeadingDifferenceDeg = 50.0;
  static const _maximumSegments = 24000;

  final List<_RoadSegment> _segments = <_RoadSegment>[];
  _RoadAnchor? _coverageAnchor;
  String? _stickyWayId;
  bool _initialised = false;
  bool _fetching = false;
  File? _cacheFile;

  RoadConstrainedMatcher();

  /// Deterministic constructor used by unit tests and replay harnesses.
  RoadConstrainedMatcher.forTesting(Iterable<RoadSegmentInput> segments) {
    _initialised = true;
    _segments.addAll(segments.map((segment) => _RoadSegment(
          wayId: segment.wayId,
          startLatitude: segment.startLatitude,
          startLongitude: segment.startLongitude,
          endLatitude: segment.endLatitude,
          endLongitude: segment.endLongitude,
        )));
  }

  Future<void> initialise() async {
    if (_initialised) return;
    _initialised = true;
    try {
      final directory = await getApplicationSupportDirectory();
      _cacheFile =
          File('${directory.path}${Platform.pathSeparator}road_geometry.json');
      if (!await _cacheFile!.exists()) return;
      final decoded =
          jsonDecode(await _cacheFile!.readAsString()) as Map<String, dynamic>;
      final stored = decoded['segments'];
      if (stored is! List<dynamic>) return;
      for (final value in stored) {
        if (value is Map<String, dynamic>) {
          final segment = _RoadSegment.fromJson(value);
          if (segment != null) _segments.add(segment);
        }
      }
    } catch (_) {
      // Road constraints are optional. A corrupt/unavailable cache must never
      // block navigation or force an unsafe snap.
      _segments.clear();
    }
  }

  /// Opportunistically caches a small road corridor around an aided position.
  /// Calls are rate-limited by travelled distance and have no effect offline.
  Future<void> observeGnss({
    required double latitude,
    required double longitude,
  }) async {
    if (!_isCoordinate(latitude, longitude) || _fetching) return;
    final anchor = _coverageAnchor;
    if (anchor != null &&
        _distanceM(anchor.latitude, anchor.longitude, latitude, longitude) <
            _refreshDistanceM) {
      return;
    }
    _fetching = true;
    try {
      final uri =
          Uri.https('overpass-api.de', '/api/interpreter', <String, String>{
        'data': '[out:json][timeout:12];way(around:${_queryRadiusM.round()},$latitude,$longitude)'
            '[highway~"motorway|trunk|primary|secondary|tertiary|unclassified|residential|service|living_street"];out geom;',
      });
      final client = HttpClient()
        ..connectionTimeout = const Duration(seconds: 8);
      try {
        final request =
            await client.getUrl(uri).timeout(const Duration(seconds: 10));
        request.headers.set(HttpHeaders.userAgentHeader, 'MERIDIAN-IDR/0.1');
        final response =
            await request.close().timeout(const Duration(seconds: 15));
        if (response.statusCode != HttpStatus.ok) return;
        final body = await utf8.decoder.bind(response).join();
        _mergeOverpassResponse(body);
        _coverageAnchor = _RoadAnchor(latitude, longitude);
        await _persist();
      } finally {
        client.close(force: true);
      }
    } catch (_) {
      // The last successfully retained corridor remains available offline.
    } finally {
      _fetching = false;
    }
  }

  /// Returns a safe road-constrained coordinate, or the unchanged raw pose
  /// when no road candidate is suitable.
  RoadConstrainedPosition constrain({
    required double latitude,
    required double longitude,
    required double headingDeg,
    required double speedMps,
  }) {
    if (!_isCoordinate(latitude, longitude) || _segments.isEmpty) {
      return RoadConstrainedPosition.raw(latitude, longitude);
    }
    _Projection? best;
    _Projection? alternate;
    _Projection? sticky;
    for (final segment in _segments) {
      final candidate = _project(latitude, longitude, segment);
      if (candidate.distanceM > _maximumSnapDistanceM) continue;
      if (speedMps >= 1.5 &&
          _headingDifferenceDeg(headingDeg, candidate.headingDeg) >
              _maximumHeadingDifferenceDeg) {
        continue;
      }
      if (segment.wayId == _stickyWayId) sticky = candidate;
      if (best == null || candidate.distanceM < best.distanceM) {
        if (best != null && best.segment.wayId != candidate.segment.wayId) {
          alternate = best;
        }
        best = candidate;
      } else if (candidate.segment.wayId != best.segment.wayId &&
          (alternate == null || candidate.distanceM < alternate.distanceM)) {
        alternate = candidate;
      }
    }
    if (best == null) return RoadConstrainedPosition.raw(latitude, longitude);
    // Prefer continuity when the previously accepted way is nearly as close.
    if (sticky != null && sticky.distanceM <= best.distanceM + 4.0) {
      best = sticky;
    }
    final second = alternate;
    if (second != null &&
        second.segment.wayId != best.segment.wayId &&
        second.distanceM - best.distanceM < _ambiguityMarginM) {
      return RoadConstrainedPosition.raw(latitude, longitude);
    }
    _stickyWayId = best.segment.wayId;
    return RoadConstrainedPosition(
      latitude: best.latitude,
      longitude: best.longitude,
      matched: true,
      distanceToRoadM: best.distanceM,
      wayId: best.segment.wayId,
    );
  }

  void _mergeOverpassResponse(String body) {
    final decoded = jsonDecode(body) as Map<String, dynamic>;
    final elements = decoded['elements'];
    if (elements is! List<dynamic>) return;
    final seen = <String>{for (final segment in _segments) segment.key};
    for (final element in elements) {
      if (element is! Map<String, dynamic>) continue;
      final geometry = element['geometry'];
      final id = element['id'];
      if (geometry is! List<dynamic> || id == null) continue;
      for (var index = 1; index < geometry.length; index++) {
        final start = geometry[index - 1];
        final end = geometry[index];
        if (start is! Map<String, dynamic> || end is! Map<String, dynamic>) {
          continue;
        }
        final segment = _RoadSegment(
          wayId: id.toString(),
          startLatitude: (start['lat'] as num?)?.toDouble() ?? double.nan,
          startLongitude: (start['lon'] as num?)?.toDouble() ?? double.nan,
          endLatitude: (end['lat'] as num?)?.toDouble() ?? double.nan,
          endLongitude: (end['lon'] as num?)?.toDouble() ?? double.nan,
        );
        if (!segment.isValid || !seen.add(segment.key)) continue;
        _segments.add(segment);
      }
    }
    if (_segments.length > _maximumSegments) {
      _segments.removeRange(0, _segments.length - _maximumSegments);
    }
  }

  Future<void> _persist() async {
    final file = _cacheFile;
    if (file == null) return;
    try {
      await file.writeAsString(
          jsonEncode(<String, Object>{
            'version': 1,
            'segments': _segments.map((segment) => segment.toJson()).toList(),
          }),
          flush: true);
    } catch (_) {
      // Retaining in-memory geometry is still useful for this app session.
    }
  }

  static _Projection _project(
      double latitude, double longitude, _RoadSegment segment) {
    const metersPerDegreeLatitude = 111320.0;
    final cosLatitude =
        math.cos(latitude * math.pi / 180).abs().clamp(0.01, 1.0);
    final ax = (segment.startLongitude - longitude) *
        metersPerDegreeLatitude *
        cosLatitude;
    final ay = (segment.startLatitude - latitude) * metersPerDegreeLatitude;
    final bx = (segment.endLongitude - longitude) *
        metersPerDegreeLatitude *
        cosLatitude;
    final by = (segment.endLatitude - latitude) * metersPerDegreeLatitude;
    final dx = bx - ax;
    final dy = by - ay;
    final lengthSquared = dx * dx + dy * dy;
    final fraction = lengthSquared <= 1e-9
        ? 0.0
        : (-(ax * dx + ay * dy) / lengthSquared).clamp(0.0, 1.0);
    final px = ax + dx * fraction;
    final py = ay + dy * fraction;
    final roadHeading = math.atan2(dx, dy) * 180 / math.pi;
    final normalizedHeading = (roadHeading + 360) % 360;
    return _Projection(
      segment: segment,
      latitude: latitude + py / metersPerDegreeLatitude,
      longitude: longitude + px / (metersPerDegreeLatitude * cosLatitude),
      distanceM: math.sqrt(px * px + py * py),
      headingDeg: normalizedHeading,
    );
  }

  static bool _isCoordinate(double latitude, double longitude) =>
      latitude.isFinite &&
      longitude.isFinite &&
      latitude.abs() <= 90 &&
      longitude.abs() <= 180;

  static double _distanceM(double lat1, double lon1, double lat2, double lon2) {
    const radiusM = 6371000.0;
    final dLat = (lat2 - lat1) * math.pi / 180;
    final dLon = (lon2 - lon1) * math.pi / 180;
    final a = math.sin(dLat / 2) * math.sin(dLat / 2) +
        math.cos(lat1 * math.pi / 180) *
            math.cos(lat2 * math.pi / 180) *
            math.sin(dLon / 2) *
            math.sin(dLon / 2);
    return 2 * radiusM * math.atan2(math.sqrt(a), math.sqrt(1 - a));
  }

  static double _headingDifferenceDeg(double first, double second) {
    final forward = ((first - second + 540) % 360 - 180).abs();
    final reverse = ((first - (second + 180) + 540) % 360 - 180).abs();
    return math.min(forward, reverse);
  }
}

class RoadConstrainedPosition {
  const RoadConstrainedPosition({
    required this.latitude,
    required this.longitude,
    required this.matched,
    required this.distanceToRoadM,
    this.wayId,
  });

  factory RoadConstrainedPosition.raw(double latitude, double longitude) =>
      RoadConstrainedPosition(
        latitude: latitude,
        longitude: longitude,
        matched: false,
        distanceToRoadM: double.nan,
      );

  final double latitude;
  final double longitude;
  final bool matched;
  final double distanceToRoadM;
  final String? wayId;
}

class RoadSegmentInput {
  const RoadSegmentInput({
    required this.wayId,
    required this.startLatitude,
    required this.startLongitude,
    required this.endLatitude,
    required this.endLongitude,
  });

  final String wayId;
  final double startLatitude;
  final double startLongitude;
  final double endLatitude;
  final double endLongitude;
}

class _RoadAnchor {
  const _RoadAnchor(this.latitude, this.longitude);
  final double latitude;
  final double longitude;
}

class _RoadSegment {
  const _RoadSegment({
    required this.wayId,
    required this.startLatitude,
    required this.startLongitude,
    required this.endLatitude,
    required this.endLongitude,
  });

  final String wayId;
  final double startLatitude;
  final double startLongitude;
  final double endLatitude;
  final double endLongitude;

  bool get isValid =>
      RoadConstrainedMatcher._isCoordinate(startLatitude, startLongitude) &&
      RoadConstrainedMatcher._isCoordinate(endLatitude, endLongitude) &&
      (startLatitude != endLatitude || startLongitude != endLongitude);
  String get key =>
      '$wayId:$startLatitude:$startLongitude:$endLatitude:$endLongitude';

  Map<String, Object> toJson() => <String, Object>{
        'way_id': wayId,
        'start_latitude': startLatitude,
        'start_longitude': startLongitude,
        'end_latitude': endLatitude,
        'end_longitude': endLongitude,
      };

  static _RoadSegment? fromJson(Map<String, dynamic> value) {
    final segment = _RoadSegment(
      wayId: value['way_id']?.toString() ?? '',
      startLatitude:
          (value['start_latitude'] as num?)?.toDouble() ?? double.nan,
      startLongitude:
          (value['start_longitude'] as num?)?.toDouble() ?? double.nan,
      endLatitude: (value['end_latitude'] as num?)?.toDouble() ?? double.nan,
      endLongitude: (value['end_longitude'] as num?)?.toDouble() ?? double.nan,
    );
    return segment.wayId.isEmpty || !segment.isValid ? null : segment;
  }
}

class _Projection {
  const _Projection({
    required this.segment,
    required this.latitude,
    required this.longitude,
    required this.distanceM,
    required this.headingDeg,
  });

  final _RoadSegment segment;
  final double latitude;
  final double longitude;
  final double distanceM;
  final double headingDeg;
}
