import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:latlong2/latlong.dart';

import '../domain/telemetry_snapshot.dart';
import '../services/idr_engine.dart';

enum MeridianTab { navigation, developer, more }

/// Single app state owner. UI widgets consume actual [TelemetrySnapshot] data
/// emitted by the engine rather than maintaining separate mock values.
class NavigationController extends ChangeNotifier {
  NavigationController(
    this._engine, {
    Duration splashDuration = const Duration(milliseconds: 1250),
    Duration engineStartTimeout = const Duration(seconds: 8),
  })  : _splashDuration = splashDuration,
        _engineStartTimeout = engineStartTimeout;

  final IdrEngine _engine;
  final Duration _splashDuration;
  final Duration _engineStartTimeout;
  StreamSubscription<TelemetrySnapshot>? _subscription;
  TelemetrySnapshot? snapshot;
  MeridianTab tab = MeridianTab.navigation;
  bool booting = true;
  bool engineReady = false;
  String startupStatus = 'Initializing live sensors';
  String? startupError;
  bool routeEditorVisible = false;
  bool gnssEnabled = true;
  LatLng? destination;
  bool tripRecording = false;
  String tripLogLabel = 'drive';
  String? lastTripLogPath;
  bool _startRequested = false;
  bool _disposed = false;

  Future<void> start() async {
    if (_startRequested) return;
    _startRequested = true;
    _subscription = _engine.telemetry.listen((next) {
      snapshot = next;
      notifyListeners();
    });

    // TFLite and Android's location service are optional startup work.  They
    // must never turn the splash screen into a permanent modal state: the
    // map already has an honest no-fix view and can remain useful while they
    // become available.
    unawaited(_observeEngineStart());
    await Future<void>.delayed(_splashDuration);
    if (_disposed) return;
    booting = false;
    if (!engineReady && startupError == null) {
      startupStatus = 'Starting sensors in the background';
    }
    notifyListeners();
  }

  Future<void> _observeEngineStart() async {
    final startFuture = _engine.start();
    try {
      await startFuture.timeout(_engineStartTimeout);
      if (_disposed) return;
      engineReady = true;
      startupError = null;
      startupStatus = 'Sensors active — waiting for a usable GNSS fix';
    } on TimeoutException {
      if (_disposed) return;
      startupError = 'Sensor setup is taking longer than expected.';
      startupStatus = 'The navigation screen is still available.';
      // Keep observing a late platform response so a temporary native stall
      // recovers without an app restart.
      unawaited(startFuture.then<void>(
        (_) {
          if (_disposed) return;
          engineReady = true;
          startupError = null;
          startupStatus = 'Sensors active — waiting for a usable GNSS fix';
          notifyListeners();
        },
        onError: (Object _, StackTrace __) {},
      ));
    } catch (_) {
      if (_disposed) return;
      startupError =
          'Sensor initialization failed. Navigation is waiting for a fix.';
      startupStatus = 'Check the device sensors and location service.';
    }
    if (!_disposed) notifyListeners();
  }

  void selectTab(MeridianTab next) {
    tab = next;
    notifyListeners();
  }

  void toggleRouteEditor() {
    routeEditorVisible = !routeEditorVisible;
    notifyListeners();
  }

  void setDestination(LatLng point) {
    destination = point;
    routeEditorVisible = true;
    notifyListeners();
  }

  Future<void> setGnssEnabled(bool enabled) async {
    if (gnssEnabled == enabled) return;
    gnssEnabled = enabled;
    notifyListeners();
    await _engine.setGnssEnabled(enabled);
    notifyListeners();
  }

  void setTripLogLabel(String value) {
    tripLogLabel = value;
    notifyListeners();
  }

  Future<void> setTripRecording(bool enabled) async {
    if (tripRecording == enabled) return;
    if (enabled) {
      await _engine.startTripRecording(label: tripLogLabel);
      tripRecording = true;
      lastTripLogPath = null;
    } else {
      lastTripLogPath = await _engine.stopTripRecording();
      tripRecording = false;
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(_subscription?.cancel());
    unawaited(_engine.stop());
    super.dispose();
  }
}
