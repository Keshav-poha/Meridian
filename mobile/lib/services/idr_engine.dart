import '../domain/telemetry_snapshot.dart';

/// Interface implemented by the Stage 4 replay bridge and later by on-device
/// TFLite inference. Widgets depend only on this stream, never fake metrics.
abstract interface class IdrEngine {
  Stream<TelemetrySnapshot> get telemetry;
  Future<void> setGnssEnabled(bool enabled);
  Future<void> start();
  Future<void> stop();
}
