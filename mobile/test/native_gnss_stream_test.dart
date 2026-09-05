import 'package:flutter_test/flutter_test.dart';
import 'package:meridian/services/native_gnss_stream.dart';

void main() {
  test('preserves measured GPS accuracy from the native channel', () {
    final position = NativeGnssStream.decodePosition(<String, Object>{
      'latitude': 28.715,
      'longitude': 77.17621,
      'timestamp': DateTime.utc(2026, 9, 5, 14).millisecondsSinceEpoch,
      'accuracy': 2.7,
      'has_accuracy': true,
      'speed': 0.0,
      'has_speed': true,
      'heading': 0.0,
      'has_heading': false,
      'is_mocked': false,
    });

    expect(position.hasAccuracy, isTrue);
    expect(position.accuracy, 2.7);
    expect(position.isMocked, isFalse);
  });

  test('rejects a malformed native GPS event', () {
    expect(
      () => NativeGnssStream.decodePosition('not a position'),
      throwsFormatException,
    );
  });
}
