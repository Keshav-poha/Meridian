import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'services/live_idr_engine.dart';
import 'state/navigation_controller.dart';
import 'ui/meridian_shell.dart';

void main() => runApp(
      ChangeNotifierProvider(
        create: (_) => NavigationController(LiveIdrEngine())..start(),
        child: const MeridianApp(),
      ),
    );

class MeridianApp extends StatelessWidget {
  const MeridianApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'MERIDIAN',
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF070A0F),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFF2979FF),
          surface: Color(0xFF17191E),
          error: Color(0xFFE53935),
        ),
        textTheme: ThemeData.dark().textTheme.apply(fontFamily: 'Roboto'),
      ),
      home: const MeridianShell(),
    );
  }
}
