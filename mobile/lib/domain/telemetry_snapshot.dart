/// Shared live/replay data bound to the Developer Mode and navigation UI.
/// Keep its JSON names aligned with shared/schemas/telemetry.schema.json.
enum NavigationMode { gnssAidedIns, deadReckoning, reacquiring }

class Axis3 {
  const Axis3(this.x, this.y, this.z);
  final double x;
  final double y;
  final double z;
}

class TelemetrySnapshot {
  const TelemetrySnapshot({
    required this.timestamp,
    required this.mode,
    required this.speedMps,
    required this.modelSpeedMps,
    required this.velocityModelTrusted,
    required this.mountCalibrated,
    required this.stationary,
    required this.vehicleMotionArmed,
    required this.headingDeg,
    required this.latitudeDeg,
    required this.longitudeDeg,
    required this.accuracyM,
    required this.positionErrorM,
    required this.driftPercent,
    required this.accelerometer,
    required this.gyroscope,
    required this.magnetometer,
    required this.gnssAvailable,
    required this.gnssEnabled,
    required this.lastAccurateLatitudeDeg,
    required this.lastAccurateLongitudeDeg,
    required this.actualLatitudeDeg,
    required this.actualLongitudeDeg,
    required this.deadReckoningDistanceM,
    required this.deadReckoningElapsed,
    this.predictionConfidence = 0,
    this.predictionConfidenceReason = 'Awaiting calibrated prediction',
    this.mountDegraded = false,
    this.motionAnomaly = false,
    this.gnssDegraded = false,
  });

  final DateTime timestamp;
  final NavigationMode mode;
  final double speedMps;
  final double modelSpeedMps;
  final bool velocityModelTrusted;
  final bool mountCalibrated;
  final bool stationary;
  final bool vehicleMotionArmed;
  final double headingDeg;
  final double latitudeDeg;
  final double longitudeDeg;
  final double accuracyM;
  final double positionErrorM;
  final double driftPercent;
  final Axis3 accelerometer;
  final Axis3 gyroscope;
  final Axis3 magnetometer;
  final bool gnssAvailable;
  final bool gnssEnabled;
  final double? lastAccurateLatitudeDeg;
  final double? lastAccurateLongitudeDeg;
  final double? actualLatitudeDeg;
  final double? actualLongitudeDeg;
  final double deadReckoningDistanceM;
  final Duration deadReckoningElapsed;
  final double predictionConfidence;
  final String predictionConfidenceReason;
  final bool mountDegraded;
  final bool motionAnomaly;
  final bool gnssDegraded;
}
