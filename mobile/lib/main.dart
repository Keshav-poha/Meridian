import 'package:flutter/material.dart';

void main() => runApp(const MeridianApp());

/// Stage 11 replaces this bootstrap with the specified MERIDIAN navigation UI.
class MeridianApp extends StatelessWidget {
  const MeridianApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'MERIDIAN',
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF070A0F),
        colorScheme: const ColorScheme.dark(primary: Color(0xFF2997FF)),
      ),
      home: const Scaffold(
        body: Center(child: Text('MERIDIAN pipeline scaffold')),
      ),
    );
  }
}
