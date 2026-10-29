package uw.edu.roadqualitymapper.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.location.Location
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.google.android.gms.location.FusedLocationProviderClient
import com.google.android.gms.location.LocationAvailability
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import uw.edu.roadqualitymapper.MainActivity
import uw.edu.roadqualitymapper.R
import uw.edu.roadqualitymapper.data.DataSource
import uw.edu.roadqualitymapper.data.GpsFix
import uw.edu.roadqualitymapper.data.Trip
import java.util.UUID

/** GPS once a second (contract §6). */
private const val GPS_INTERVAL_MS = 1_000L

/** Below this speed, GPS heading is unreliable, so it's stored as null (contract §6). */
private const val MIN_SPEED_FOR_HEADING_MPS = 1.5

private const val CHANNEL_ID = "recording"
private const val NOTIFICATION_ID = 1

/**
 * Records a trip with the screen off. A foreground service, so Android keeps it running and
 * shows a "Recording trip" notification the whole time. Screens never talk to it directly: they
 * start/stop it and read [RecordingState].
 *
 * Phase 3a: GPS only. Later: saving to Room, uploading (3b/3c), and the BLE device (Phase 4).
 */
class RecordingService : Service() {

    companion object {
        private const val ACTION_START = "uw.edu.roadqualitymapper.action.START_RECORDING"
        private const val ACTION_STOP = "uw.edu.roadqualitymapper.action.STOP_RECORDING"
        private const val EXTRA_VEHICLE_ID = "vehicle_id"

        /** Call from the screen (the app must be in the foreground, e.g. after a button tap). */
        fun start(context: Context, vehicleId: String) {
            val intent = Intent(context, RecordingService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_VEHICLE_ID, vehicleId)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.startService(Intent(context, RecordingService::class.java).setAction(ACTION_STOP))
        }
    }

    private lateinit var locationClient: FusedLocationProviderClient

    private val locationCallback = object : LocationCallback() {
        override fun onLocationResult(result: LocationResult) {
            result.locations.forEach { location ->
                val fix = location.toGpsFix()
                RecordingState.update { it.copy(lastFix = fix, fixCount = it.fixCount + 1) }
            }
        }

        override fun onLocationAvailability(availability: LocationAvailability) {
            RecordingState.update { it.copy(gpsAvailable = availability.isLocationAvailable) }
        }
    }

    override fun onCreate() {
        super.onCreate()
        locationClient = LocationServices.getFusedLocationProviderClient(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startRecording(intent.getStringExtra(EXTRA_VEHICLE_ID))
            ACTION_STOP -> stopRecording()
            else -> stopSelf()   // restarted by the system without our intent; nothing to resume yet
        }
        // If Android kills the service, don't restart it on its own: a new trip needs a button tap.
        return START_NOT_STICKY
    }

    private fun startRecording(vehicleId: String?) {
        // Android requires startForeground() within a few seconds of start(), even if we stop
        // again right away, or it crashes the app. So it comes first, before any checks.
        try {
            createNotificationChannel()
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, buildNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION,
            )
        } catch (e: Exception) {
            // Missing location permission, or started while the app was in the background.
            fail("Couldn't start recording: ${e.message}")
            return
        }

        if (RecordingState.status.value.recording) return   // already recording; keep going
        if (vehicleId == null) {
            fail("No vehicle selected")
            return
        }

        val trip = Trip(
            id = UUID.randomUUID().toString(),
            vehicleId = vehicleId,
            startTime = System.currentTimeMillis(),
            source = DataSource.REAL,
        )
        RecordingState.update { RecordingStatus(recording = true, trip = trip) }

        val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, GPS_INTERVAL_MS)
            .setMinUpdateIntervalMillis(GPS_INTERVAL_MS)
            .build()
        try {
            locationClient.requestLocationUpdates(request, locationCallback, Looper.getMainLooper())
        } catch (e: SecurityException) {
            fail("Location permission was removed")
        }
    }

    private fun stopRecording() {
        locationClient.removeLocationUpdates(locationCallback)
        RecordingState.update { status ->
            status.copy(
                recording = false,
                trip = status.trip?.copy(endTime = System.currentTimeMillis()),
            )
        }
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun fail(message: String) {
        locationClient.removeLocationUpdates(locationCallback)
        RecordingState.update { it.copy(recording = false, error = message) }
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        // Also covers Android stopping the service on its own.
        locationClient.removeLocationUpdates(locationCallback)
        RecordingState.update { it.copy(recording = false) }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createNotificationChannel() {
        // Low importance: shows in the status bar without making a sound.
        val channel = NotificationChannel(CHANNEL_ID, "Trip recording", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification() = NotificationCompat.Builder(this, CHANNEL_ID)
        .setSmallIcon(R.drawable.ic_recording)
        .setContentTitle("Recording trip")
        .setContentText("Road Quality Mapper is recording your route")
        .setOngoing(true)
        .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
        // Tapping the notification opens the app.
        .setContentIntent(
            PendingIntent.getActivity(
                this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
            )
        )
        .addAction(
            0, "Stop",
            PendingIntent.getService(
                this, 0,
                Intent(this, RecordingService::class.java).setAction(ACTION_STOP),
                PendingIntent.FLAG_IMMUTABLE,
            ),
        )
        .build()
}

private fun Location.toGpsFix(): GpsFix {
    val speed = if (hasSpeed()) speed.toDouble() else null
    return GpsFix(
        t = time,
        lat = latitude,
        lon = longitude,
        speedMps = speed,
        headingDeg = if (hasBearing() && speed != null && speed >= MIN_SPEED_FOR_HEADING_MPS) {
            bearing.toDouble()
        } else {
            null
        },
        accuracyM = if (hasAccuracy()) accuracy.toDouble() else null,
    )
}
