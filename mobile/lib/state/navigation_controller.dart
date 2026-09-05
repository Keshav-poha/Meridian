import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:latlong2/latlong.dart';

import '../domain/telemetry_snapshot.dart';
import '../services/idr_engine.dart';

enum MeridianTab { navigation, developer, more }

/// Single app state owner. UI widgets consume actual [TelemetrySnapshot] data
/// emitted by the engine rather than maintaining separate mock values.
class NavigationController extends ChangeNotifier {
  NavigationController(this._engine);

  final IdrEngine _engine;
  StreamSubscription<TelemetrySnapshot>? _subscription;
  TelemetrySnapshot? snapshot;
  MeridianTab tab = MeridianTab.navigation;
  bool booting = true;
  bool routeEditorVisible = false;
  bool gnssEnabled = true;
  LatLng? destination;
  bool tripRecording = false;
  String tripLogLabel = 'drive';
  String? lastTripLogPath;

  Future<void> start() async {
    _subscription = _engine.telemetry.listen((next) {
      snapshot = next;
      notifyListeners();
    });
    try {
      await _engine.start();
    } finally {
      await Future<void>.delayed(const Duration(milliseconds: 1250));
      booting = false;
      notifyListeners();
    }
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
    unawaited(_subscription?.cancel());
    unawaited(_engine.stop());
    super.dispose();
  }
}
