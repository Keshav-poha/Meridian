import 'package:flutter/services.dart';
import 'package:geolocator/geolocator.dart';

/// Android GPS-provider stream used for safety-critical aiding.
///
/// `geolocator` remains responsible for permission dialogs and display-only
/// cache/bootstrap locations. On some devices the fused Flutter callback can
/// omit Android's `Location.hasAccuracy()` flag even while the GPS provider
/// has a valid measured accuracy. This channel subscribes directly to
/// `LocationManager.GPS_PROVIDER`, so a stream observation is satellite-backed
/// and carries the platform's measurement-presence flags.
class NativeGnssStream {
  static const _channel = EventChannel(
    'com.keshavpoha.meridian/gps_location',
  );

  Stream<Position> get positions =>
      _channel.receiveBroadcastStream().map(decodePosition);

  /// Exposed for a platform-contract test without requiring an Android device.
  static Position decodePosition(dynamic message) {
    if (message is! Map) {
      throw const FormatException('Native GNSS event must be a map.');
    }
    return Position.fromMap(Map<dynamic, dynamic>.from(message));
  }
}
