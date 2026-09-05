import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import '../domain/telemetry_snapshot.dart';

/// Versioned, line-oriented recorder for a physical MERIDIAN drive.
///
/// During a deliberate GNSS outage, `reference_gnss` stays in the log for
/// later scoring but is never an estimator input. The JSONL format lets the
/// evaluation script process an interrupted drive without loading it all into
/// memory.
class TripRecorder {
  static const schemaVersion = 1;

  IOSink? _sink;
  String? _path;
  String _label = 'drive';
  int _sampleCount = 0;

  bool get isRecording => _sink != null;
  String? get path => _path;

  Future<void> start({String label = 'drive'}) async {
    if (_sink != null) return;
    _label = label.trim().isEmpty ? 'drive' : label.trim();
    _sampleCount = 0;
    final documents = await getApplicationDocumentsDirectory();
    final directory = Directory(
      '${documents.path}${Platform.pathSeparator}meridian_logs',
    );
    await directory.create(recursive: true);
    final timestamp = DateTime.now()
        .toUtc()
        .toIso8601String()
        .replaceAll(':', '-')
        .replaceAll('.', '-');
    final safeLabel = _label.replaceAll(RegExp(r'[^a-zA-Z0-9_-]+'), '_');
    final file = File('${directory.path}${Platform.pathSeparator}'
        'meridian_${timestamp}_$safeLabel.jsonl');
    _path = file.path;
    _sink = file.openWrite(mode: FileMode.writeOnly);
    _sink!.writeln(jsonEncode(<String, Object?>{
      'record_type': 'session_start',
      'schema_version': schemaVersion,
      'created_at_utc': DateTime.now().toUtc().toIso8601String(),
      'label': _label,
      'reference_gnss_note':
          'Reference GNSS may be logged during a simulated outage but is not fed to DR.',
    }));
  }

  void recordEvent(String event, {Map<String, Object?> fields = const {}}) {
    final sink = _sink;
    if (sink == null) return;
    sink.writeln(jsonEncode(<String, Object?>{
      'record_type': 'event',
      'schema_version': schemaVersion,
      'timestamp_s': DateTime.now().microsecondsSinceEpoch / 1e6,
      'timestamp_utc': DateTime.now().toUtc().toIso8601String(),
      'event': event,
      ...fields,
    }));
  }

  void record(TelemetrySnapshot snapshot) {
    final sink = _sink;
    if (sink == null) return;
    sink.writeln(jsonEncode(TripLogEncoder.sample(snapshot, label: _label)));
    _sampleCount++;
  }

  Future<String?> stop() async {
    final sink = _sink;
    if (sink == null) return _path;
    sink.writeln(jsonEncode(<String, Object?>{
      'record_type': 'session_end',
      'schema_version': schemaVersion,
      'ended_at_utc': DateTime.now().toUtc().toIso8601String(),
      'samples': _sampleCount,
    }));
    await sink.flush();
    await sink.close();
    _sink = null;
    return _path;
  }
}

/// Converts a snapshot to portable JSON values without emitting invalid NaN.
class TripLogEncoder {
  const TripLogEncoder._();

  static Map<String, Object?> sample(
    TelemetrySnapshot snapshot, {
    required String label,
  }) =>
      <String, Object?>{
        'record_type': 'sample',
        'schema_version': TripRecorder.schemaVersion,
        'label': label,
        'timestamp_s': snapshot.timestamp.microsecondsSinceEpoch / 1e6,
        'timestamp_utc': snapshot.timestamp.toUtc().toIso8601String(),
        'mode': _modeName(snapshot.mode),
        'position': <String, Object?>{
          'latitude_deg': _finite(snapshot.latitudeDeg),
          'longitude_deg': _finite(snapshot.longitudeDeg),
          // Mobile does not expose one globally stable ENU origin. Leave these
          // explicit nulls rather than inventing coordinates in the log.
          'east_m': null,
          'north_m': null,
          'heading_deg': _finite(snapshot.headingDeg),
          'reference_gnss': _coordinate(
            snapshot.actualLatitudeDeg,
            snapshot.actualLongitudeDeg,
          ),
          'last_accepted_gnss': _coordinate(
            snapshot.lastAccurateLatitudeDeg,
            snapshot.lastAccurateLongitudeDeg,
          ),
        },
        'motion': <String, Object?>{
          'speed_mps': _finite(snapshot.speedMps),
          'model_speed_mps': _finite(snapshot.modelSpeedMps),
          'velocity_model_trusted': snapshot.velocityModelTrusted,
          'mount_calibrated': snapshot.mountCalibrated,
          'mount_degraded': snapshot.mountDegraded,
          'stationary': snapshot.stationary,
          'motion_anomaly': snapshot.motionAnomaly,
          'vehicle_motion_armed': snapshot.vehicleMotionArmed,
          'accelerometer_mps2': _axis(snapshot.accelerometer),
          'gyroscope_rps': _axis(snapshot.gyroscope),
          'magnetometer_ut': _axis(snapshot.magnetometer),
        },
        'gnss': <String, Object?>{
          'available': snapshot.gnssAvailable,
          'aiding_enabled': snapshot.gnssEnabled,
          'degraded': snapshot.gnssDegraded,
          'latitude_deg': _finite(snapshot.actualLatitudeDeg),
          'longitude_deg': _finite(snapshot.actualLongitudeDeg),
          'accuracy_m': _finite(snapshot.accuracyM),
        },
        'metrics': <String, Object?>{
          'position_error_m': _finite(snapshot.positionErrorM),
          'drift_percent': _finite(snapshot.driftPercent),
          'dr_distance_m': _finite(snapshot.deadReckoningDistanceM),
          'seconds_since_gnss': snapshot.deadReckoningElapsed.inMilliseconds /
              Duration.millisecondsPerSecond,
          'prediction_confidence': _finite(snapshot.predictionConfidence),
          'prediction_confidence_reason': snapshot.predictionConfidenceReason,
        },
      };

  static String _modeName(NavigationMode mode) => switch (mode) {
        NavigationMode.gnssAidedIns => 'gnss_aided_ins',
        NavigationMode.deadReckoning => 'dead_reckoning',
        NavigationMode.reacquiring => 'reacquiring',
      };

  static Map<String, Object?> _axis(Axis3 value) => <String, Object?>{
        'x': _finite(value.x),
        'y': _finite(value.y),
        'z': _finite(value.z),
      };

  static Map<String, Object?> _coordinate(
          double? latitude, double? longitude) =>
      <String, Object?>{
        'latitude_deg': _finite(latitude),
        'longitude_deg': _finite(longitude),
      };

  static double? _finite(double? value) =>
      value != null && value.isFinite ? value : null;
}
