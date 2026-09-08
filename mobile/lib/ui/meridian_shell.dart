import 'dart:io';
import 'dart:math' as math;

import 'package:dio_cache_interceptor/dio_cache_interceptor.dart';
import 'package:flutter/material.dart' hide NavigationMode;
import 'package:flutter/services.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_map_cache/flutter_map_cache.dart';
import 'package:geolocator/geolocator.dart' show Geolocator;
import 'package:http_cache_file_store/http_cache_file_store.dart';
import 'package:latlong2/latlong.dart' show LatLng;
import 'package:path_provider/path_provider.dart';
import 'package:provider/provider.dart';

import '../domain/telemetry_snapshot.dart';
import '../state/navigation_controller.dart';

const _nearBlack = Color(0xFF070A0F);
const _panel = Color(0xFF1A1C20);
const _panelLight = Color(0xFF25272C);
const _blue = Color(0xFF2979FF);
const _red = Color(0xFFE53935);

class MeridianShell extends StatelessWidget {
  const MeridianShell({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<NavigationController>();
    if (controller.booting) return const MeridianSplashScreen();
    final Widget page = switch (controller.tab) {
      MeridianTab.navigation => const NavigationScreen(),
      MeridianTab.developer => const DeveloperModeScreen(),
      MeridianTab.more => const MoreOptionsScreen(),
    };
    return Scaffold(
      body: SafeArea(child: page),
      bottomNavigationBar: const SafeArea(
        top: false,
        child: MeridianBottomNavigation(),
      ),
    );
  }
}

class MeridianSplashScreen extends StatefulWidget {
  const MeridianSplashScreen({super.key});

  @override
  State<MeridianSplashScreen> createState() => _MeridianSplashScreenState();
}

class _MeridianSplashScreenState extends State<MeridianSplashScreen>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1600),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        body: AnimatedBuilder(
          animation: _pulse,
          builder: (context, _) => Stack(
            children: [
              Positioned.fill(
                  child:
                      CustomPaint(painter: _RadarRoutePainter(_pulse.value))),
              Center(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 34),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const Text('MERIDIAN',
                          style: TextStyle(
                            color: _blue,
                            fontWeight: FontWeight.w900,
                            fontSize: 45,
                            letterSpacing: 1.6,
                          )),
                      const SizedBox(height: 4),
                      const Text('GPS DENIED NAVIGATION SYSTEM',
                          style: TextStyle(
                            fontWeight: FontWeight.w500,
                            letterSpacing: 1.2,
                          )),
                      const SizedBox(height: 310),
                      const Text('LOADING...',
                          style: TextStyle(
                            color: _blue,
                            fontWeight: FontWeight.w800,
                            fontSize: 24,
                          )),
                      const SizedBox(height: 16),
                      ClipRRect(
                        borderRadius: BorderRadius.circular(10),
                        child: LinearProgressIndicator(
                          value: 0.55 + _pulse.value * 0.2,
                          minHeight: 12,
                          backgroundColor: _panelLight,
                          valueColor:
                              const AlwaysStoppedAnimation<Color>(_blue),
                        ),
                      ),
                      const SizedBox(height: 13),
                      const Text('Initializing sensors and mapping engine',
                          textAlign: TextAlign.center),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      );
}

class NavigationScreen extends StatelessWidget {
  const NavigationScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<NavigationController>();
    final snapshot = controller.snapshot;
    final isLost = snapshot?.mode == NavigationMode.deadReckoning;
    final isAcquiring = snapshot?.mode == NavigationMode.acquiring;
    return Column(
      children: [
        MeridianTopBar(
          title: isLost
              ? 'GPS LOST'
              : isAcquiring
                  ? 'ACQUIRING GPS'
                  : controller.routeEditorVisible
                      ? 'NAVIGATION'
                      : 'MERIDIAN',
          onSearch: controller.toggleRouteEditor,
        ),
        if (controller.routeEditorVisible) const _RouteEditor(),
        Expanded(child: _NavigationMap(snapshot: snapshot)),
      ],
    );
  }
}

class MeridianTopBar extends StatelessWidget {
  const MeridianTopBar(
      {super.key, required this.title, required this.onSearch});
  final String title;
  final VoidCallback onSearch;

  @override
  Widget build(BuildContext context) => Container(
        height: 72,
        decoration: const BoxDecoration(
            border: Border(bottom: BorderSide(color: _panelLight))),
        child: Row(
          children: [
            IconButton(
                onPressed: () {},
                icon: const Icon(Icons.menu_rounded, size: 34)),
            Expanded(
                child: Text(title,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontWeight: FontWeight.w800,
                      fontSize: 24,
                    ))),
            IconButton(
                onPressed: onSearch,
                icon: const Icon(Icons.search_rounded, size: 31)),
            IconButton(
                onPressed: () {},
                icon: const Icon(Icons.more_vert_rounded, size: 30)),
          ],
        ),
      );
}

class _RouteEditor extends StatelessWidget {
  const _RouteEditor();

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<NavigationController>();
    final snapshot = controller.snapshot;
    final from = snapshot == null ||
            !snapshot.latitudeDeg.isFinite ||
            !snapshot.longitudeDeg.isFinite
        ? 'Acquiring current fix...'
        : '${_coordinate(snapshot.latitudeDeg)}, ${_coordinate(snapshot.longitudeDeg)}';
    final to = controller.destination == null
        ? 'Long-press map to set destination'
        : '${_coordinate(controller.destination!.latitude)}, ${_coordinate(controller.destination!.longitude)}';
    final distanceM = controller.distanceToDestinationMeters;
    final distanceText = distanceM == null
        ? null
        : distanceM >= 1000
            ? '${(distanceM / 1000).toStringAsFixed(2)} km'
            : '${distanceM.round()} m';
    final speedMps = snapshot?.speedMps ?? 0;
    final etaText = (distanceM != null && speedMps > 1.2)
        ? '${(distanceM / speedMps / 60).ceil()} min'
        : null;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
      child: Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: _panel,
          borderRadius: BorderRadius.circular(22),
          border: Border.all(color: _panelLight),
        ),
        child: Column(
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(children: [
                    _RouteRow(
                      icon: Icons.my_location_rounded,
                      color: _blue,
                      label: 'ORIGIN (LIVE)',
                      value: from,
                    ),
                    const SizedBox(height: 8),
                    _RouteRow(
                      icon: Icons.location_on_rounded,
                      color: _red,
                      label: 'DESTINATION',
                      value: to,
                    ),
                  ]),
                ),
                const SizedBox(width: 10),
                Column(
                  children: [
                    Material(
                      color: _panelLight,
                      borderRadius: BorderRadius.circular(16),
                      child: IconButton(
                        tooltip: 'Clear destination',
                        icon: const Icon(Icons.close_rounded,
                            color: Colors.white70, size: 26),
                        onPressed: controller.clearDestination,
                      ),
                    ),
                  ],
                ),
              ],
            ),
            if (distanceText != null) ...[
              const SizedBox(height: 10),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text('Distance: $distanceText',
                      style: const TextStyle(
                          color: _blue,
                          fontWeight: FontWeight.w700,
                          fontSize: 13)),
                  if (etaText != null)
                    Text('Est. Time: $etaText',
                        style: const TextStyle(
                            color: Colors.greenAccent,
                            fontWeight: FontWeight.w700,
                            fontSize: 13)),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _RouteRow extends StatelessWidget {
  const _RouteRow(
      {required this.icon,
      required this.color,
      required this.label,
      required this.value});
  final IconData icon;
  final Color color;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 13),
        decoration: BoxDecoration(
            color: _panel,
            borderRadius: BorderRadius.circular(22),
            border: Border.all(color: _panelLight)),
        child: Row(children: [
          Icon(icon, color: color, size: 31),
          const SizedBox(width: 12),
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Text(label,
                    style: const TextStyle(fontWeight: FontWeight.w800)),
                const SizedBox(height: 3),
                Text(value,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Colors.white70)),
              ])),
        ]),
      );
}

class _NavigationMap extends StatefulWidget {
  const _NavigationMap({required this.snapshot});
  final TelemetrySnapshot? snapshot;

  @override
  State<_NavigationMap> createState() => _NavigationMapState();
}

class _NavigationMapState extends State<_NavigationMap> {
  final MapController _mapController = MapController();
  late final Future<CacheStore> _tileCacheStore;
  NavigationMode? _lastMode;

  @override
  void initState() {
    super.initState();
    _tileCacheStore = _createTileCacheStore();
  }

  static Future<CacheStore> _createTileCacheStore() async {
    final directory = await getApplicationSupportDirectory();
    return FileCacheStore(
      '${directory.path}${Platform.pathSeparator}map_tiles',
    );
  }

  @override
  void didUpdateWidget(covariant _NavigationMap oldWidget) {
    super.didUpdateWidget(oldWidget);
    final snapshot = widget.snapshot;
    final controller = context.read<NavigationController>();

    // Tactile haptic feedback on tunnel outage transition
    if (snapshot != null && _lastMode != null && snapshot.mode != _lastMode) {
      if (snapshot.mode == NavigationMode.deadReckoning) {
        HapticFeedback.heavyImpact();
      } else if (snapshot.mode == NavigationMode.gnssAidedIns) {
        HapticFeedback.mediumImpact();
      }
    }
    _lastMode = snapshot?.mode;

    // Auto-follow vehicle position
    if (snapshot != null &&
        snapshot.latitudeDeg.isFinite &&
        snapshot.longitudeDeg.isFinite &&
        controller.autoFollow) {
      final current = LatLng(snapshot.latitudeDeg, snapshot.longitudeDeg);
      final currentZoom = _mapController.camera.zoom;
      final targetZoom = currentZoom < 14 ? 17.0 : currentZoom;
      _mapController.move(current, targetZoom);
      if (controller.headingUp) {
        _mapController.rotate(360 - snapshot.headingDeg);
      } else {
        _mapController.rotate(0);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<NavigationController>();
    final snapshot = widget.snapshot;
    final latitude = snapshot?.latitudeDeg;
    final longitude = snapshot?.longitudeDeg;
    final hasPosition =
        latitude?.isFinite == true && longitude?.isFinite == true;
    final current = hasPosition ? LatLng(latitude!, longitude!) : null;
    final destination = controller.destination;
    final mapCenter = current ?? const LatLng(20.5937, 78.9629);

    final markers = <Marker>[
      if (current != null)
        Marker(
          point: current,
          width: 66,
          height: 66,
          child: PulsingLocationMarker(
            headingDeg: snapshot?.headingDeg ?? 0,
            isDeadReckoning: snapshot?.mode == NavigationMode.deadReckoning,
          ),
        ),
      if (destination != null)
        Marker(
          point: destination,
          width: 52,
          height: 58,
          child: const Icon(Icons.location_on_rounded, color: _red, size: 52),
        ),
    ];

    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 0, 14, 8),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(28),
        child: Stack(children: [
          // Tactical dark grid fallback (underneath map tiles when offline in tunnels)
          Positioned.fill(
            child: const CustomPaint(painter: _TacticalGridPainter()),
          ),
          FutureBuilder<CacheStore>(
            future: _tileCacheStore,
            builder: (context, cacheSnapshot) => FlutterMap(
              mapController: _mapController,
              options: MapOptions(
                initialCenter: mapCenter,
                initialZoom: current == null ? 4.3 : 17,
                onPositionChanged: (camera, hasGesture) {
                  if (hasGesture) controller.setAutoFollow(false);
                },
                onLongPress: (_, point) => controller.setDestination(point),
              ),
              children: [
                TileLayer(
                  urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                  userAgentPackageName: 'com.meridian.idr',
                  tileProvider: cacheSnapshot.hasData
                      ? CachedTileProvider(
                          store: cacheSnapshot.data!,
                          maxStale: const Duration(days: 30),
                          hitCacheOnNetworkFailure: true,
                        )
                      : null,
                ),
                // Breadcrumb trail showing vehicle dead-reckoning trajectory
                if (controller.trajectoryHistory.length >= 2)
                  PolylineLayer(polylines: [
                    Polyline(
                      points: controller.trajectoryHistory,
                      strokeWidth: 4.5,
                      color: (snapshot?.mode == NavigationMode.deadReckoning
                              ? const Color(0xFFFF9100)
                              : _blue)
                          .withValues(alpha: 0.75),
                    ),
                  ]),
                // Destination routing line
                if (current != null && destination != null)
                  PolylineLayer(polylines: [
                    Polyline(
                      points: [current, destination],
                      strokeWidth: 5,
                      color: const Color(0xFF00E5FF),
                    ),
                  ]),
                MarkerLayer(markers: markers),
              ],
            ),
          ),

          // Floating Action Controls: Recenter, Heading-Up, and Quick Outage Simulator
          Positioned(
            right: 14,
            bottom: 84,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Quick Outage Simulator toggle button
                Material(
                  color: controller.gnssEnabled ? _panel : _red,
                  elevation: 4,
                  shape: const CircleBorder(),
                  child: IconButton(
                    tooltip: controller.gnssEnabled
                        ? 'Simulate Tunnel (Disable GNSS)'
                        : 'Restore GNSS Aiding',
                    icon: Icon(
                      controller.gnssEnabled
                          ? Icons.gps_off_rounded
                          : Icons.gps_fixed_rounded,
                      color: Colors.white,
                      size: 22,
                    ),
                    onPressed: () =>
                        controller.setGnssEnabled(!controller.gnssEnabled),
                  ),
                ),
                const SizedBox(height: 10),
                // Heading-Up vs North-Up compass mode
                Material(
                  color: _panel,
                  elevation: 4,
                  shape: const CircleBorder(),
                  child: IconButton(
                    tooltip: controller.headingUp
                        ? 'Heading-Up Navigation (Active)'
                        : 'North-Up Map (Active)',
                    icon: Icon(
                      controller.headingUp
                          ? Icons.navigation_rounded
                          : Icons.explore_rounded,
                      color: controller.headingUp ? _blue : Colors.white70,
                      size: 22,
                    ),
                    onPressed: () {
                      controller.toggleHeadingUp();
                      if (!controller.headingUp) {
                        _mapController.rotate(0);
                      }
                    },
                  ),
                ),
                const SizedBox(height: 10),
                // Recenter button
                Material(
                  color: controller.autoFollow ? _panelLight : _blue,
                  elevation: 4,
                  shape: const CircleBorder(),
                  child: IconButton(
                    tooltip: 'Recenter to current location',
                    icon: Icon(
                      Icons.my_location_rounded,
                      color:
                          controller.autoFollow ? Colors.white60 : Colors.white,
                      size: 24,
                    ),
                    onPressed: () {
                      controller.setAutoFollow(true);
                      if (current != null) {
                        _mapController.move(
                          current,
                          math.max(16.0, _mapController.camera.zoom),
                        );
                        if (controller.headingUp && snapshot != null) {
                          _mapController.rotate(360 - snapshot.headingDeg);
                        }
                      }
                    },
                  ),
                ),
              ],
            ),
          ),

          if (!hasPosition)
            Positioned(
              left: 12,
              right: 12,
              top: 14,
              child: _WaitingForPosition(
                status: snapshot?.predictionConfidenceReason ??
                    controller.startupStatus,
                error: controller.startupError,
              ),
            ),
          Positioned(
              left: 12,
              right: 12,
              bottom: 14,
              child: _StatsBar(snapshot: snapshot)),
          const Positioned(
            right: 14,
            bottom: 60,
            child: IgnorePointer(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: Color(0xB8151A20),
                  borderRadius: BorderRadius.all(Radius.circular(6)),
                ),
                child: Padding(
                  padding: EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                  child: Text(
                    '© OpenStreetMap contributors',
                    style: TextStyle(
                      color: Color(0xFFE7EDF5),
                      fontSize: 9,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ),
            ),
          ),
          if (snapshot?.mode == NavigationMode.deadReckoning)
            Positioned(
                left: 12,
                right: 12,
                top: 14,
                child: _DeadReckoningStatus(snapshot: snapshot!)),
        ]),
      ),
    );
  }
}

class _WaitingForPosition extends StatelessWidget {
  const _WaitingForPosition({required this.status, this.error});

  final String status;
  final String? error;

  @override
  Widget build(BuildContext context) {
    final isPermissionIssue =
        error?.toLowerCase().contains('permission') == true ||
            status.toLowerCase().contains('permission') == true ||
            status.toLowerCase().contains('settings') == true;

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xE617191D),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: _panelLight),
      ),
      child: Row(children: [
        const SizedBox(
          height: 22,
          width: 22,
          child: CircularProgressIndicator(strokeWidth: 2.5),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('Acquiring location',
                  style: TextStyle(fontWeight: FontWeight.w800)),
              Text(error ?? status,
                  style: TextStyle(
                    color: error == null ? Colors.white70 : _red,
                    fontSize: 12,
                  )),
            ],
          ),
        ),
        if (isPermissionIssue)
          TextButton(
            onPressed: Geolocator.openAppSettings,
            child: const Text('SETTINGS',
                style: TextStyle(color: _blue, fontWeight: FontWeight.w700)),
          ),
      ]),
    );
  }
}

class _StatsBar extends StatelessWidget {
  const _StatsBar({required this.snapshot});
  final TelemetrySnapshot? snapshot;

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<NavigationController>();
    final speed = snapshot?.speedMps;
    final speedDisplay = speed == null
        ? '—'
        : controller.speedInKmh
            ? '${(speed * 3.6).round()} km/h'
            : '${speed.toStringAsFixed(1)} m/s';

    return Container(
      padding: const EdgeInsets.symmetric(vertical: 12),
      decoration: BoxDecoration(
          color: const Color(0xE61E2127),
          borderRadius: BorderRadius.circular(22),
          border: Border.all(color: _panelLight)),
      child: Row(children: [
        _Stat(
          label: 'Speed (${controller.speedInKmh ? 'km/h' : 'm/s'})',
          value: speedDisplay,
          onTap: controller.toggleSpeedUnit,
        ),
        const _Divider(),
        _Stat(label: 'Accuracy', value: _meters(snapshot?.accuracyM)),
        const _Divider(),
        _Stat(
          label: 'Heading',
          value: snapshot == null ? '—' : '${snapshot!.headingDeg.round()}°',
          onTap: controller.toggleHeadingUp,
        ),
      ]),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value, this.onTap});
  final String label;
  final String value;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => Expanded(
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(12),
          child: Column(children: [
            Text(label,
                style: const TextStyle(color: Colors.white70, fontSize: 11)),
            const SizedBox(height: 3),
            Text(value,
                style:
                    const TextStyle(fontWeight: FontWeight.w800, fontSize: 18)),
          ]),
        ),
      );
}

class _Divider extends StatelessWidget {
  const _Divider();
  @override
  Widget build(BuildContext context) =>
      const SizedBox(height: 35, child: VerticalDivider(color: Colors.white70));
}

class _DeadReckoningStatus extends StatelessWidget {
  const _DeadReckoningStatus({required this.snapshot});
  final TelemetrySnapshot snapshot;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: const Color(0xF2161A22),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: const Color(0xFFFF9100), width: 1.5),
          boxShadow: [
            BoxShadow(
              color: const Color(0xFFFF9100).withValues(alpha: 0.2),
              blurRadius: 16,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Row(children: [
          Container(
            padding: const EdgeInsets.all(8),
            decoration: const BoxDecoration(
              color: Color(0x33FF9100),
              shape: BoxShape.circle,
            ),
            child: const Icon(Icons.sync_problem_rounded,
                color: Color(0xFFFF9100), size: 22),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                const Row(
                  children: [
                    Text('DEAD RECKONING ACTIVE',
                        style: TextStyle(
                          fontWeight: FontWeight.w900,
                          fontSize: 13,
                          letterSpacing: 1.1,
                          color: Color(0xFFFF9100),
                        )),
                    SizedBox(width: 6),
                    Text('• TUNNEL',
                        style: TextStyle(
                            fontWeight: FontWeight.w700,
                            color: Colors.white70,
                            fontSize: 11)),
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  '${_duration(snapshot.deadReckoningElapsed)} elapsed  •  ${snapshot.deadReckoningDistanceM.toStringAsFixed(1)} m  •  drift ${_meters(snapshot.positionErrorM)}',
                  style: const TextStyle(color: Colors.white70, fontSize: 11),
                ),
              ],
            ),
          ),
        ]),
      );
}

class _TacticalGridPainter extends CustomPainter {
  const _TacticalGridPainter();

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(
        Offset.zero & size, Paint()..color = const Color(0xFF0D1017));
    final gridPaint = Paint()
      ..color = const Color(0xFF1B2230)
      ..strokeWidth = 1.0;
    const spacing = 36.0;
    for (double x = 0; x < size.width; x += spacing) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), gridPaint);
    }
    for (double y = 0; y < size.height; y += spacing) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), gridPaint);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class DeveloperModeScreen extends StatelessWidget {
  const DeveloperModeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<NavigationController>();
    final snapshot = controller.snapshot;
    final enabled = controller.gnssEnabled;
    return Column(children: [
      MeridianTopBar(title: 'DEV MODE', onSearch: () {}),
      Expanded(
          child: ListView(
              padding: const EdgeInsets.fromLTRB(20, 20, 20, 12),
              children: [
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                  color: _panel, borderRadius: BorderRadius.circular(22)),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      const Icon(Icons.location_on_rounded,
                          color: _red, size: 32),
                      const SizedBox(width: 12),
                      const Expanded(
                          child: Text('GPS/GNSS',
                              style: TextStyle(
                                  fontWeight: FontWeight.w800, fontSize: 23))),
                      Semantics(
                        label: enabled
                            ? 'Disable GNSS outage simulator'
                            : 'Enable GNSS outage simulator',
                        button: true,
                        child: FilledButton(
                          key: const ValueKey('gnss-outage-toggle'),
                          onPressed: () => controller.setGnssEnabled(!enabled),
                          style: FilledButton.styleFrom(
                              backgroundColor: enabled ? _panelLight : _blue),
                          child: Text(enabled ? 'Disable' : 'Enable'),
                        ),
                      ),
                    ]),
                    const Divider(height: 24, color: _panelLight),
                    Row(children: [
                      Expanded(
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                            const Text('Physical drive log',
                                style: TextStyle(fontWeight: FontWeight.w700)),
                            Text(
                                'Label: ${controller.tripLogLabel.replaceAll('_', ' ')}',
                                style: const TextStyle(
                                    color: Colors.white60, fontSize: 12)),
                          ])),
                      PopupMenuButton<String>(
                        tooltip: 'Select drive log label',
                        onSelected: controller.setTripLogLabel,
                        itemBuilder: (context) => const [
                          PopupMenuItem(value: 'drive', child: Text('Driving')),
                          PopupMenuItem(
                              value: 'parked_idle',
                              child: Text('Parked / idle')),
                          PopupMenuItem(
                              value: 'pothole_bump',
                              child: Text('Pothole / bump')),
                          PopupMenuItem(
                              value: 'handheld_shake',
                              child: Text('Handheld shake')),
                          PopupMenuItem(
                              value: 'mount_shift', child: Text('Mount shift')),
                        ],
                        icon: const Icon(Icons.label_outline_rounded,
                            color: Colors.white70),
                      ),
                      const SizedBox(width: 4),
                      FilledButton(
                        onPressed: () => controller
                            .setTripRecording(!controller.tripRecording),
                        style: FilledButton.styleFrom(
                            backgroundColor:
                                controller.tripRecording ? _red : _panelLight),
                        child: Text(controller.tripRecording
                            ? 'Stop & save'
                            : 'Record'),
                      ),
                    ]),
                    if (controller.lastTripLogPath != null)
                      Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: Text('Saved: ${controller.lastTripLogPath}',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                  color: Colors.white60, fontSize: 11))),
                  ]),
            ),
            const SizedBox(height: 16),
            _PositionComparison(snapshot: snapshot),
            const SizedBox(height: 16),
            _RawSensorData(snapshot: snapshot),
          ])),
    ]);
  }
}

class _PositionComparison extends StatelessWidget {
  const _PositionComparison({required this.snapshot});
  final TelemetrySnapshot? snapshot;

  @override
  Widget build(BuildContext context) => _PanelCard(
        title: 'POSITION COMPARISON',
        child: Column(children: [
          _CoordinateLine(
              'Last accurate (GPS)',
              snapshot?.lastAccurateLatitudeDeg,
              snapshot?.lastAccurateLongitudeDeg),
          _CoordinateLine('Predicted (current)', snapshot?.latitudeDeg,
              snapshot?.longitudeDeg,
              color: _blue),
          _CoordinateLine('Actual', snapshot?.actualLatitudeDeg,
              snapshot?.actualLongitudeDeg,
              color: Colors.greenAccent),
          const Divider(height: 28, color: _panelLight),
          Row(children: [
            const Expanded(
                child: Text('Position Error',
                    style:
                        TextStyle(fontWeight: FontWeight.w800, fontSize: 19))),
            Text(_error(snapshot),
                style: const TextStyle(
                    color: _red, fontWeight: FontWeight.w800, fontSize: 18)),
          ]),
          const SizedBox(height: 10),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Expanded(
                child: Text('Prediction confidence',
                    style: TextStyle(fontWeight: FontWeight.w700))),
            Flexible(
                child: Text(_confidence(snapshot),
                    textAlign: TextAlign.right,
                    style: TextStyle(
                        color:
                            snapshot?.predictionConfidence == 0 ? _red : _blue,
                        fontWeight: FontWeight.w800))),
          ]),
          if (snapshot != null)
            Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(snapshot!.predictionConfidenceReason,
                    style:
                        const TextStyle(color: Colors.white60, fontSize: 12))),
        ]),
      );
}

class _CoordinateLine extends StatelessWidget {
  const _CoordinateLine(this.label, this.latitude, this.longitude,
      {this.color = Colors.white});
  final String label;
  final double? latitude;
  final double? longitude;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final latitude = this.latitude;
    final longitude = this.longitude;
    final hasCoordinate =
        latitude?.isFinite == true && longitude?.isFinite == true;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 9),
      child: Row(children: [
        Expanded(
            child: Text(label,
                style: const TextStyle(
                    fontSize: 17, fontWeight: FontWeight.w700))),
        Text(
            hasCoordinate
                ? '${_coordinate(latitude!)}, ${_coordinate(longitude!)}'
                : 'Awaiting fix',
            style: TextStyle(color: color, fontSize: 16)),
      ]),
    );
  }
}

class _RawSensorData extends StatelessWidget {
  const _RawSensorData({required this.snapshot});
  final TelemetrySnapshot? snapshot;

  @override
  Widget build(BuildContext context) {
    final actualLatitude = snapshot?.actualLatitudeDeg;
    final actualLongitude = snapshot?.actualLongitudeDeg;
    final hasActualCoordinate =
        actualLatitude?.isFinite == true && actualLongitude?.isFinite == true;
    final modelSpeed = snapshot?.modelSpeedMps;
    final modelSpeedText = modelSpeed?.isFinite == true
        ? '${modelSpeed!.toStringAsFixed(2)} m/s'
        : 'rejected outside 0–45 m/s';
    return _PanelCard(
      title: 'RAW SENSOR DATA',
      child: Column(children: [
        Row(children: [
          Expanded(
              child:
                  _AxisTile('Accelerometer', 'm/s²', snapshot?.accelerometer)),
          const SizedBox(width: 9),
          Expanded(child: _AxisTile('Gyroscope', 'rad/s', snapshot?.gyroscope)),
          const SizedBox(width: 9),
          Expanded(
              child: _AxisTile('Magnetometer', 'µT', snapshot?.magnetometer)),
        ]),
        const SizedBox(height: 14),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
              color: _panelLight, borderRadius: BorderRadius.circular(18)),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('Motion gate',
                style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 7),
            Text(snapshot == null
                ? 'Awaiting IMU window'
                : '${snapshot!.stationary ? 'Stationary — velocity held at 0' : 'Moving — velocity accepted'}\n'
                    '${snapshot!.vehicleMotionArmed ? 'GNSS-confirmed vehicle motion armed' : 'Awaiting GNSS-confirmed vehicle motion'}\n'
                    '${!snapshot!.mountCalibrated ? 'Mount calibration: awaiting fixed mount + GNSS course' : snapshot!.velocityModelTrusted ? 'Velocity CNN input: calibrated and in distribution' : 'Velocity CNN input: rejected — device/model mismatch'}\n'
                    'CNN prior (diagnostic): $modelSpeedText  •  GNSS-anchored INS: ${snapshot!.speedMps.toStringAsFixed(2)} m/s'),
          ]),
        ),
        const SizedBox(height: 14),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
              color: _panelLight, borderRadius: BorderRadius.circular(18)),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('Raw GPS/GNSS',
                style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 7),
            Text(hasActualCoordinate
                ? 'Latitude: ${_coordinate(actualLatitude!)}    Longitude: ${_coordinate(actualLongitude!)}'
                : 'Awaiting physical GNSS fix'),
          ]),
        ),
      ]),
    );
  }
}

class _AxisTile extends StatelessWidget {
  const _AxisTile(this.title, this.unit, this.axis);
  final String title;
  final String unit;
  final Axis3? axis;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
            color: _panelLight, borderRadius: BorderRadius.circular(16)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title,
              style:
                  const TextStyle(fontWeight: FontWeight.w700, fontSize: 12)),
          Text(unit,
              style: const TextStyle(color: Colors.white70, fontSize: 11)),
          const SizedBox(height: 8),
          Text('X: ${_axis(axis?.x)}'),
          Text('Y: ${_axis(axis?.y)}'),
          Text('Z: ${_axis(axis?.z)}'),
        ]),
      );
}

class MoreOptionsScreen extends StatelessWidget {
  const MoreOptionsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<NavigationController>();
    return Column(children: [
      MeridianTopBar(title: 'MORE OPTIONS', onSearch: () {}),
      Expanded(
          child: ListView(padding: const EdgeInsets.all(20), children: [
        _MoreOption(
          icon: Icons.settings_outlined,
          label: 'Settings & Display',
          onTap: () => _showSettingsSheet(context, controller),
        ),
        _MoreOption(
            icon: Icons.ios_share_rounded,
            label: 'Export Trip Data / Logs',
            onTap: () async {
              final path = controller.lastTripLogPath;
              if (path == null) {
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                    content:
                        Text('Record and save a Developer Mode drive first.')));
                return;
              }
              await Clipboard.setData(ClipboardData(text: path));
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Saved log path copied.')));
              }
            }),
        _MoreOption(
          icon: Icons.help_outline_rounded,
          label: 'About & System Overview',
          onTap: () => _showAboutDialog(context),
        ),
      ])),
    ]);
  }

  void _showSettingsSheet(
      BuildContext context, NavigationController controller) {
    showModalBottomSheet(
      context: context,
      backgroundColor: _panel,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
      ),
      builder: (context) => StatefulBuilder(
        builder: (context, setModalState) => Padding(
          padding: const EdgeInsets.fromLTRB(24, 20, 24, 30),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 44,
                  height: 4,
                  decoration: BoxDecoration(
                    color: _panelLight,
                    borderRadius: BorderRadius.circular(10),
                  ),
                ),
              ),
              const SizedBox(height: 18),
              const Text('NAVIGATION SETTINGS',
                  style: TextStyle(
                      fontWeight: FontWeight.w900,
                      fontSize: 18,
                      color: _blue,
                      letterSpacing: 1.1)),
              const SizedBox(height: 16),
              SwitchListTile(
                title: const Text('Speedometer in km/h'),
                subtitle: Text(
                  controller.speedInKmh
                      ? 'Displaying km/h (Standard)'
                      : 'Displaying m/s (Raw telemetry)',
                  style: const TextStyle(fontSize: 12, color: Colors.white60),
                ),
                value: controller.speedInKmh,
                activeThumbColor: _blue,
                onChanged: (_) {
                  controller.toggleSpeedUnit();
                  setModalState(() {});
                },
              ),
              SwitchListTile(
                title: const Text('Camera Auto-Follow'),
                subtitle: const Text(
                  'Keep vehicle centered on the navigation map',
                  style: TextStyle(fontSize: 12, color: Colors.white60),
                ),
                value: controller.autoFollow,
                activeThumbColor: _blue,
                onChanged: (val) {
                  controller.setAutoFollow(val);
                  setModalState(() {});
                },
              ),
              SwitchListTile(
                title: const Text('Heading-Up Mode'),
                subtitle: const Text(
                  'Rotate map to match vehicle driving direction',
                  style: TextStyle(fontSize: 12, color: Colors.white60),
                ),
                value: controller.headingUp,
                activeThumbColor: _blue,
                onChanged: (_) {
                  controller.toggleHeadingUp();
                  setModalState(() {});
                },
              ),
              if (controller.destination != null) ...[
                const Divider(color: _panelLight),
                ListTile(
                  leading: const Icon(Icons.close_rounded, color: _red),
                  title: const Text('Clear Active Route',
                      style: TextStyle(color: _red)),
                  onTap: () {
                    controller.clearDestination();
                    Navigator.pop(context);
                  },
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  void _showAboutDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: _panel,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
        title: const Row(
          children: [
            Icon(Icons.navigation_rounded, color: _blue, size: 28),
            SizedBox(width: 10),
            Text('MERIDIAN IDR',
                style: TextStyle(fontWeight: FontWeight.w900, fontSize: 20)),
          ],
        ),
        content: const Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Intelligent Dead Reckoning System',
              style: TextStyle(fontWeight: FontWeight.w700, color: _blue),
            ),
            SizedBox(height: 8),
            Text(
              'Designed for continuous vehicle navigation during complete GNSS blackouts (underground tunnels, underpasses, multi-level basements, dense urban canyons).\n\n'
              '• Mount-Independent Kinematics\n'
              '• 1D CNN Neural Velocity Prior\n'
              '• Zero-Velocity Updates (ZUPT) & Gyro Bias Estimation\n'
              '• Smooth GNSS Reacquisition Blending\n\n'
              'SIH Problem Statement 26168 (ISRO)',
              style:
                  TextStyle(fontSize: 13, color: Colors.white70, height: 1.4),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('CLOSE', style: TextStyle(color: _blue)),
          ),
        ],
      ),
    );
  }
}

class _MoreOption extends StatelessWidget {
  const _MoreOption({required this.icon, required this.label, this.onTap});
  final IconData icon;
  final String label;
  final VoidCallback? onTap;
  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.only(bottom: 14),
        decoration: BoxDecoration(
            color: _panel, borderRadius: BorderRadius.circular(20)),
        child: ListTile(
          leading: Icon(icon, color: _blue),
          title:
              Text(label, style: const TextStyle(fontWeight: FontWeight.w700)),
          trailing: const Icon(Icons.chevron_right_rounded),
          onTap: onTap,
        ),
      );
}

class MeridianBottomNavigation extends StatelessWidget {
  const MeridianBottomNavigation({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<NavigationController>();
    return Container(
      color: Colors.black,
      padding: const EdgeInsets.fromLTRB(14, 8, 14, 10),
      child: Row(children: [
        _TabButton(
            icon: Icons.map_outlined,
            label: 'Navigation',
            selected: controller.tab == MeridianTab.navigation,
            onTap: () => controller.selectTab(MeridianTab.navigation)),
        _TabButton(
            icon: Icons.code_rounded,
            label: 'Developer Mode',
            selected: controller.tab == MeridianTab.developer,
            onTap: () => controller.selectTab(MeridianTab.developer)),
        _TabButton(
            icon: Icons.more_horiz_rounded,
            label: 'More',
            selected: controller.tab == MeridianTab.more,
            onTap: () => controller.selectTab(MeridianTab.more)),
      ]),
    );
  }
}

class _TabButton extends StatelessWidget {
  const _TabButton(
      {required this.icon,
      required this.label,
      required this.selected,
      required this.onTap});
  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => Expanded(
          child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Icon(icon, color: selected ? _blue : Colors.white, size: 29),
              const SizedBox(height: 4),
              Text(label,
                  style: TextStyle(
                      color: selected ? _blue : Colors.white, fontSize: 12)),
            ])),
      ));
}

class _PanelCard extends StatelessWidget {
  const _PanelCard({required this.title, required this.child});
  final String title;
  final Widget child;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
            color: _panel,
            borderRadius: BorderRadius.circular(24),
            border: Border.all(color: _panelLight)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title,
              style: const TextStyle(
                  color: _blue, fontWeight: FontWeight.w900, fontSize: 22)),
          const SizedBox(height: 20),
          child,
        ]),
      );
}

class PulsingLocationMarker extends StatefulWidget {
  const PulsingLocationMarker({
    super.key,
    this.headingDeg = 0.0,
    this.isDeadReckoning = false,
  });

  final double headingDeg;
  final bool isDeadReckoning;

  @override
  State<PulsingLocationMarker> createState() => _PulsingLocationMarkerState();
}

class _PulsingLocationMarkerState extends State<PulsingLocationMarker>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1300),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: _controller,
        builder: (context, child) => CustomPaint(
          painter: _LocationMarkerPainter(
            progress: _controller.value,
            headingDeg: widget.headingDeg,
            isDeadReckoning: widget.isDeadReckoning,
          ),
          child: const SizedBox.expand(),
        ),
      );
}

class _LocationMarkerPainter extends CustomPainter {
  const _LocationMarkerPainter({
    required this.progress,
    required this.headingDeg,
    required this.isDeadReckoning,
  });

  final double progress;
  final double headingDeg;
  final bool isDeadReckoning;

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final markerColor = isDeadReckoning ? const Color(0xFFFF9100) : _blue;

    final pulse = Paint()
      ..color = markerColor.withValues(alpha: 0.35 * (1 - progress));
    canvas.drawCircle(center, size.width * (0.2 + progress * 0.28), pulse);

    canvas.save();
    canvas.translate(center.dx, center.dy);
    canvas.rotate(headingDeg * math.pi / 180.0);

    final arrow = Path()
      ..moveTo(0, -20)
      ..lineTo(-11, 10)
      ..lineTo(0, 3)
      ..lineTo(11, 10)
      ..close();

    canvas.drawPath(
      arrow,
      Paint()
        ..color = markerColor
        ..style = PaintingStyle.fill,
    );
    canvas.drawPath(
      arrow,
      Paint()
        ..color = Colors.white
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.0,
    );
    canvas.restore();

    canvas.drawCircle(center, 4, Paint()..color = Colors.white);
  }

  @override
  bool shouldRepaint(covariant _LocationMarkerPainter oldDelegate) =>
      oldDelegate.progress != progress ||
      oldDelegate.headingDeg != headingDeg ||
      oldDelegate.isDeadReckoning != isDeadReckoning;
}

class _RadarRoutePainter extends CustomPainter {
  const _RadarRoutePainter(this.progress);
  final double progress;
  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(Offset.zero & size, Paint()..color = _nearBlack);
    final center = Offset(size.width / 2, size.height * 0.53);
    final grid = Paint()
      ..color = _blue.withValues(alpha: 0.16)
      ..strokeWidth = 1;
    for (var radius = 58.0; radius < size.width * 0.48; radius += 38) {
      canvas.drawCircle(center, radius, grid..style = PaintingStyle.stroke);
    }
    for (var angle = 0.0; angle < math.pi * 2; angle += math.pi / 8) {
      canvas.drawLine(
          center,
          center + Offset(math.cos(angle), math.sin(angle)) * size.width * .46,
          grid);
    }
    final route = Path()
      ..moveTo(size.width * .12, size.height * .66)
      ..cubicTo(size.width * .36, size.height * .61, size.width * .67,
          size.height * .68, size.width * .69, size.height * .49)
      ..quadraticBezierTo(size.width * .72, size.height * .41, size.width * .86,
          size.height * .37);
    final routePaint = Paint()
      ..color = _blue
      ..style = PaintingStyle.stroke
      ..strokeWidth = 5;
    for (final metric in route.computeMetrics()) {
      final span = metric.length * (.35 + progress * .55);
      canvas.drawPath(metric.extractPath(0, span), routePaint);
    }
    final arrow = Path()
      ..moveTo(center.dx, center.dy - 62)
      ..lineTo(center.dx - 42, center.dy + 45)
      ..lineTo(center.dx, center.dy + 23)
      ..lineTo(center.dx + 42, center.dy + 45)
      ..close();
    canvas.drawPath(arrow, Paint()..color = const Color(0xFF4DB7FF));
    canvas.drawCircle(
        Offset(size.width * .86, size.height * .37), 14, Paint()..color = _red);
  }

  @override
  bool shouldRepaint(covariant _RadarRoutePainter oldDelegate) =>
      oldDelegate.progress != progress;
}

String _coordinate(double value) => value.toStringAsFixed(5);
String _axis(double? value) => value == null ? '—' : value.toStringAsFixed(3);
String _meters(double? value) =>
    value == null || !value.isFinite ? '—' : '${value.toStringAsFixed(1)} m';
String _error(TelemetrySnapshot? snapshot) {
  if (snapshot == null || !snapshot.positionErrorM.isFinite) {
    return 'Awaiting actual GNSS';
  }
  final percent = snapshot.driftPercent.isFinite
      ? ' (${snapshot.driftPercent.toStringAsFixed(2)}%)'
      : '';
  return '${snapshot.positionErrorM.toStringAsFixed(1)} m$percent';
}

String _confidence(TelemetrySnapshot? snapshot) => snapshot == null
    ? 'Awaiting data'
    : '${(snapshot.predictionConfidence * 100).toStringAsFixed(0)}%';

String _duration(Duration value) =>
    '${value.inMinutes.toString().padLeft(2, '0')}:${(value.inSeconds % 60).toString().padLeft(2, '0')}';
