import 'dart:math' as math;

import 'package:flutter/material.dart' hide NavigationMode;
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart' show LatLng;
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
      bottomNavigationBar: const MeridianBottomNavigation(),
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
    return Column(
      children: [
        MeridianTopBar(
          title: isLost
              ? 'GPS LOST'
              : controller.routeEditorVisible
                  ? 'NAVIGATION'
                  : 'MERIDIAN',
          onSearch: controller.toggleRouteEditor,
        ),
        if (controller.routeEditorVisible) const _RouteEditor(),
        if (isLost) const _GpsLostBanner(),
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
    final from = snapshot == null
        ? 'Waiting for a live location'
        : '${_coordinate(snapshot.latitudeDeg)}, ${_coordinate(snapshot.longitudeDeg)}';
    final to = controller.destination == null
        ? 'Tap the map to set a destination'
        : '${_coordinate(controller.destination!.latitude)}, ${_coordinate(controller.destination!.longitude)}';
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 10),
      child: Row(
        children: [
          Expanded(
            child: Column(children: [
              _RouteRow(
                  icon: Icons.location_on_rounded,
                  color: _blue,
                  label: 'FROM',
                  value: from),
              const SizedBox(height: 10),
              _RouteRow(
                  icon: Icons.location_on_rounded,
                  color: _red,
                  label: 'TO',
                  value: to),
            ]),
          ),
          const SizedBox(width: 12),
          Material(
            color: _panelLight,
            borderRadius: BorderRadius.circular(20),
            child: IconButton(
              tooltip: 'Swap route endpoints',
              onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(
                    content: Text(
                        'The live current location remains the route origin.')),
              ),
              icon: const Icon(Icons.swap_vert_rounded, size: 34),
            ),
          ),
        ],
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

class _GpsLostBanner extends StatelessWidget {
  const _GpsLostBanner();

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        margin: const EdgeInsets.fromLTRB(20, 8, 20, 10),
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
        decoration: BoxDecoration(
            color: const Color(0xFF681614),
            borderRadius: BorderRadius.circular(22)),
        child:
            const Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          Icon(Icons.location_off_rounded, color: _red),
          SizedBox(width: 10),
          Text('GPS SIGNAL LOST',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
        ]),
      );
}

class _NavigationMap extends StatelessWidget {
  const _NavigationMap({required this.snapshot});
  final TelemetrySnapshot? snapshot;

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<NavigationController>();
    final hasPosition = snapshot != null;
    final current = hasPosition
        ? LatLng(snapshot!.latitudeDeg, snapshot!.longitudeDeg)
        : null;
    final destination = controller.destination;
    final mapCenter = current ?? const LatLng(20.5937, 78.9629);
    final markers = <Marker>[
      if (current != null)
        Marker(
          point: current,
          width: 66,
          height: 66,
          child: const PulsingLocationMarker(),
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
          FlutterMap(
            options: MapOptions(
              initialCenter: mapCenter,
              initialZoom: current == null ? 4.3 : 17,
              onTap: (_, point) => controller.setDestination(point),
            ),
            children: [
              TileLayer(
                urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                userAgentPackageName: 'com.meridian.idr',
              ),
              if (current != null && destination != null)
                PolylineLayer(polylines: [
                  Polyline(
                      points: [current, destination],
                      strokeWidth: 7,
                      color: _blue),
                ]),
              MarkerLayer(markers: markers),
            ],
          ),
          if (!hasPosition) const _WaitingForPosition(),
          Positioned(
              left: 12,
              right: 12,
              bottom: 14,
              child: _StatsBar(snapshot: snapshot)),
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
  const _WaitingForPosition();

  @override
  Widget build(BuildContext context) => ColoredBox(
        color: _nearBlack.withValues(alpha: 0.72),
        child: const Center(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
          CircularProgressIndicator(),
          SizedBox(height: 14),
          Text('Waiting for live sensor and GNSS data'),
        ])),
      );
}

class _StatsBar extends StatelessWidget {
  const _StatsBar({required this.snapshot});
  final TelemetrySnapshot? snapshot;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(vertical: 15),
        decoration: BoxDecoration(
            color: const Color(0xE6222429),
            borderRadius: BorderRadius.circular(22)),
        child: Row(children: [
          _Stat(
              label: 'Speed',
              value: snapshot == null
                  ? '—'
                  : '${snapshot!.speedMps.toStringAsFixed(1)} m/s'),
          const _Divider(),
          _Stat(label: 'Accuracy', value: _meters(snapshot?.accuracyM)),
          const _Divider(),
          _Stat(
              label: 'Heading',
              value:
                  snapshot == null ? '—' : '${snapshot!.headingDeg.round()}°'),
        ]),
      );
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Expanded(
          child: Column(children: [
        Text(label, style: const TextStyle(color: Colors.white70)),
        const SizedBox(height: 3),
        Text(value,
            style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 19)),
      ]));
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
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
            color: const Color(0xE617191D),
            borderRadius: BorderRadius.circular(18)),
        child: Row(children: [
          const Icon(Icons.route_rounded, color: _red),
          const SizedBox(width: 10),
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                const Text('DEAD RECKONING',
                    style: TextStyle(fontWeight: FontWeight.w800)),
                Text(
                    '${_duration(snapshot.deadReckoningElapsed)}  •  ${snapshot.deadReckoningDistanceM.toStringAsFixed(1)} m  •  drift ${_meters(snapshot.positionErrorM)}',
                    style:
                        const TextStyle(color: Colors.white70, fontSize: 12)),
              ])),
        ]),
      );
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
              child: Row(children: [
                const Icon(Icons.location_on_rounded, color: _red, size: 32),
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
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 9),
        child: Row(children: [
          Expanded(
              child: Text(label,
                  style: const TextStyle(
                      fontSize: 17, fontWeight: FontWeight.w700))),
          Text(
              latitude == null || longitude == null
                  ? 'Awaiting fix'
                  : '${_coordinate(latitude!)}, ${_coordinate(longitude!)}',
              style: TextStyle(color: color, fontSize: 16)),
        ]),
      );
}

class _RawSensorData extends StatelessWidget {
  const _RawSensorData({required this.snapshot});
  final TelemetrySnapshot? snapshot;

  @override
  Widget build(BuildContext context) => _PanelCard(
        title: 'RAW SENSOR DATA',
        child: Column(children: [
          Row(children: [
            Expanded(
                child: _AxisTile(
                    'Accelerometer', 'm/s²', snapshot?.accelerometer)),
            const SizedBox(width: 9),
            Expanded(
                child: _AxisTile('Gyroscope', 'rad/s', snapshot?.gyroscope)),
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
                      'Velocity CNN diagnostic: ${snapshot!.modelSpeedMps.toStringAsFixed(2)} m/s  •  navigation: ${snapshot!.speedMps.toStringAsFixed(2)} m/s'),
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
              Text(snapshot?.actualLatitudeDeg == null
                  ? 'Awaiting physical GNSS fix'
                  : 'Latitude: ${_coordinate(snapshot!.actualLatitudeDeg!)}    Longitude: ${_coordinate(snapshot!.actualLongitudeDeg!)}'),
            ]),
          ),
        ]),
      );
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
  Widget build(BuildContext context) => Column(children: [
        MeridianTopBar(title: 'MORE OPTIONS', onSearch: () {}),
        Expanded(
            child: ListView(padding: const EdgeInsets.all(20), children: const [
          _MoreOption(icon: Icons.settings_outlined, label: 'Settings'),
          _MoreOption(
              icon: Icons.ios_share_rounded, label: 'Export Trip Data / Logs'),
          _MoreOption(icon: Icons.help_outline_rounded, label: 'About & Help'),
        ])),
      ]);
}

class _MoreOption extends StatelessWidget {
  const _MoreOption({required this.icon, required this.label});
  final IconData icon;
  final String label;
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
  const PulsingLocationMarker({super.key});
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
            painter: _LocationMarkerPainter(_controller.value),
            child: const SizedBox.expand(),
          ));
}

class _LocationMarkerPainter extends CustomPainter {
  const _LocationMarkerPainter(this.progress);
  final double progress;
  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final pulse = Paint()
      ..color = _blue.withValues(alpha: 0.35 * (1 - progress));
    canvas.drawCircle(center, size.width * (0.2 + progress * 0.28), pulse);
    canvas.drawCircle(center, 10, Paint()..color = _blue);
    canvas.drawCircle(center, 5, Paint()..color = Colors.white);
  }

  @override
  bool shouldRepaint(covariant _LocationMarkerPainter oldDelegate) =>
      oldDelegate.progress != progress;
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

String _duration(Duration value) =>
    '${value.inMinutes.toString().padLeft(2, '0')}:${(value.inSeconds % 60).toString().padLeft(2, '0')}';
