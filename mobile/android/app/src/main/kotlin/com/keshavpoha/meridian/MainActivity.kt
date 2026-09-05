package com.keshavpoha.meridian

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel

class MainActivity : FlutterActivity() {
    private var locationManager: LocationManager? = null
    private var locationListener: LocationListener? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        EventChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            "com.keshavpoha.meridian/gps_location",
        ).setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink) {
                stopGpsUpdates()
                if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) !=
                    PackageManager.PERMISSION_GRANTED
                ) {
                    events.error(
                        "permission_denied",
                        "Fine location permission is required for the GPS provider.",
                        null,
                    )
                    return
                }

                val manager =
                    getSystemService(Context.LOCATION_SERVICE) as LocationManager
                if (!manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                    events.error(
                        "gps_disabled",
                        "Android GPS provider is disabled.",
                        null,
                    )
                    return
                }

                val listener = LocationListener { location ->
                    events.success(location.toGnssMap())
                }
                locationManager = manager
                locationListener = listener
                try {
                    manager.requestLocationUpdates(
                        LocationManager.GPS_PROVIDER,
                        1000L,
                        0f,
                        listener,
                    )
                    // Some Android builds expose their current position only
                    // through the platform fused provider while a fresh raw
                    // GPS epoch is pending. It is still required to carry
                    // Android's measured accuracy; Dart never invents one.
                    if (manager.getProvider(LocationManager.FUSED_PROVIDER) != null) {
                        manager.requestLocationUpdates(
                            LocationManager.FUSED_PROVIDER,
                            1000L,
                            0f,
                            listener,
                        )
                    }
                    emitRecentCachedLocations(manager, events)
                } catch (error: SecurityException) {
                    stopGpsUpdates()
                    events.error(
                        "permission_denied",
                        "Android rejected the GPS-provider request.",
                        null,
                    )
                }
            }

            override fun onCancel(arguments: Any?) {
                stopGpsUpdates()
            }
        })
    }

    override fun onDestroy() {
        stopGpsUpdates()
        super.onDestroy()
    }

    private fun stopGpsUpdates() {
        val manager = locationManager
        val listener = locationListener
        if (manager != null && listener != null) {
            manager.removeUpdates(listener)
        }
        locationManager = null
        locationListener = null
    }

    private fun emitRecentCachedLocations(
        manager: LocationManager,
        events: EventChannel.EventSink,
    ) {
        val newest = listOfNotNull(
            manager.getLastKnownLocation(LocationManager.GPS_PROVIDER),
            manager.getLastKnownLocation(LocationManager.FUSED_PROVIDER),
        ).maxByOrNull { it.time } ?: return
        events.success(newest.toGnssMap())
    }

    @Suppress("DEPRECATION")
    private fun Location.toGnssMap(): Map<String, Any> {
        val values = mutableMapOf<String, Any>(
            "latitude" to latitude,
            "longitude" to longitude,
            "timestamp" to time,
            "provider" to (provider ?: "unknown"),
            "is_mocked" to if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                isMock
            } else {
                isFromMockProvider
            },
        )
        values["has_accuracy"] = hasAccuracy()
        if (hasAccuracy()) values["accuracy"] = accuracy.toDouble()
        values["has_speed"] = hasSpeed()
        if (hasSpeed()) values["speed"] = speed.toDouble()
        values["has_heading"] = hasBearing()
        if (hasBearing()) values["heading"] = bearing.toDouble()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            values["has_speed_accuracy"] = hasSpeedAccuracy()
            if (hasSpeedAccuracy()) values["speed_accuracy"] =
                speedAccuracyMetersPerSecond.toDouble()
            values["has_heading_accuracy"] = hasBearingAccuracy()
            if (hasBearingAccuracy()) values["heading_accuracy"] =
                bearingAccuracyDegrees.toDouble()
        }
        return values
    }
}
