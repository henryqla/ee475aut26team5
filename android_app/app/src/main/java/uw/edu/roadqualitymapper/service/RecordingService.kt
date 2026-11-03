package uw.edu.roadqualitymapper.service

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
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
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import uw.edu.roadqualitymapper.MainActivity
import uw.edu.roadqualitymapper.R
import uw.edu.roadqualitymapper.ble.DeviceConnection
import uw.edu.roadqualitymapper.ble.DeviceMessage
import uw.edu.roadqualitymapper.ble.DeviceState
import uw.edu.roadqualitymapper.ble.EvtMessage
import uw.edu.roadqualitymapper.ble.FAKE_FW_PREFIX
import uw.edu.roadqualitymapper.ble.SegMessage
import uw.edu.roadqualitymapper.data.DataSource
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.GpsFix
import uw.edu.roadqualitymapper.data.Trip
import uw.edu.roadqualitymapper.data.TripUploader
import uw.edu.roadqualitymapper.data.local.DeviceMessageEntity
import uw.edu.roadqualitymapper.data.local.EventEntity
import uw.edu.roadqualitymapper.data.local.RoadDao
import uw.edu.roadqualitymapper.data.local.RoadDatabase
import uw.edu.roadqualitymapper.data.local.SegmentEntity
import uw.edu.roadqualitymapper.data.local.toEntity
import java.util.UUID

/** GPS once a second (contract §6). */
private const val GPS_INTERVAL_MS = 1_000L

/** Below this speed, GPS heading is unreliable, so it's stored as null (contract §6). */
private const val MIN_SPEED_FOR_HEADING_MPS = 1.5

/** Upload what's been saved this often during a trip. */
private const val UPLOAD_INTERVAL_MS = 60_000L

/** After Stop, give the last upload this long before giving up (it's retried at the next app start). */
private const val FINAL_UPLOAD_TIMEOUT_MS = 30_000L

/** How long a message waits for the GPS fix after its time before it's saved anyway. */
private const val WAIT_FOR_FIX_MS = MAX_FIX_GAP_MS + 500

private const val CHANNEL_ID = "recording"
private const val NOTIFICATION_ID = 1

/**
 * Records a trip with the screen off. A foreground service, so Android keeps it running and
 * shows a "Recording trip" notification the whole time. Screens never talk to it directly: they
 * start/stop it and read [RecordingState].
 *
 * During a trip: GPS fixes and device messages are saved to the phone's database as they arrive;
 * each segment and event gets the GPS position at its own time (contract §6), and everything is
 * uploaded once a minute and when the trip stops. A trip without a device records GPS only.
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

    // Main thread throughout, so the trip's state below is only touched from one thread.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val tripJobs = mutableListOf<Job>()   // cancelled on Stop
    private var finishJob: Job? = null            // the final upload after Stop

    private lateinit var locationClient: FusedLocationProviderClient
    private lateinit var dao: RoadDao
    private var trip: Trip? = null
    private val fixes = FixHistory()

    /** Device messages waiting for the GPS fix after their time, oldest first. */
    private val waiting = mutableListOf<DeviceMessage>()

    private val locationCallback = object : LocationCallback() {
        override fun onLocationResult(result: LocationResult) {
            val currentTrip = trip ?: return
            result.locations.forEach { location ->
                val fix = location.toGpsFix()
                fixes.add(fix)
                RecordingState.update { it.copy(lastFix = fix, fixCount = it.fixCount + 1) }
                scope.launch { dao.insertFix(fix.toEntity(currentTrip.id)) }
            }
            saveReadyMessages()
        }

        override fun onLocationAvailability(availability: LocationAvailability) {
            RecordingState.update { it.copy(gpsAvailable = availability.isLocationAvailable) }
        }
    }

    override fun onCreate() {
        super.onCreate()
        locationClient = LocationServices.getFusedLocationProviderClient(this)
        dao = RoadDatabase.get(this).dao()
        DeviceConnection.init(this)
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
            ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(), foregroundTypes())
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

        val newTrip = Trip(
            id = UUID.randomUUID().toString(),
            vehicleId = vehicleId,
            startTime = System.currentTimeMillis(),
            source = DataSource.REAL,
        )
        trip = newTrip
        RecordingState.update { RecordingStatus(recording = true, trip = newTrip) }

        // Save the trip before anything that belongs to it.
        tripJobs += scope.launch {
            dao.insertTrip(newTrip.toEntity())
            listenToDevice(newTrip)
        }
        tripJobs += scope.launch { uploadEveryMinute() }
        tripJobs += scope.launch { checkWaitingMessagesEverySecond() }
        tripJobs += scope.launch { showDeviceStateInNotification() }
        DeviceConnection.connectToRemembered()

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
        val currentTrip = trip
        if (currentTrip == null) {
            // Not recording (e.g. the notification's Stop after the app was restarted).
            if (finishJob == null) stopSelf()
            return
        }
        locationClient.removeLocationUpdates(locationCallback)
        tripJobs.forEach { it.cancel() }
        tripJobs.clear()
        saveReadyMessages(all = true)   // whatever is still waiting, with the fixes we have

        val endTime = System.currentTimeMillis()
        trip = null
        RecordingState.update { it.copy(recording = false, trip = it.trip?.copy(endTime = endTime)) }
        updateNotification("Uploading the trip…")

        finishJob = scope.launch {
            dao.setEndTime(currentTrip.id, endTime)
            withTimeoutOrNull(FINAL_UPLOAD_TIMEOUT_MS) { TripUploader.uploadPending(applicationContext) }
            finishJob = null
            // A new trip may have started while uploading; only stop if not.
            if (trip == null) {
                ServiceCompat.stopForeground(this@RecordingService, ServiceCompat.STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
    }

    private fun fail(message: String) {
        locationClient.removeLocationUpdates(locationCallback)
        tripJobs.forEach { it.cancel() }
        tripJobs.clear()
        trip = null
        RecordingState.update { it.copy(recording = false, error = message) }
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        // Also covers Android stopping the service on its own.
        locationClient.removeLocationUpdates(locationCallback)
        scope.cancel()
        trip = null
        RecordingState.update { it.copy(recording = false) }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ------------------------------------------------------------ device messages

    private suspend fun listenToDevice(trip: Trip) {
        // Learn the device's firmware version, and whether it's a test device, from its status message.
        tripJobs += scope.launch {
            DeviceConnection.status.map { it.fwVersion }.distinctUntilChanged().collect { fw ->
                if (fw == null) return@collect
                val source = if (fw.startsWith(FAKE_FW_PREFIX)) DataSource.FAKE_DEVICE else DataSource.REAL
                dao.setDeviceInfo(trip.id, fw, source)
                RecordingState.update { it.copy(trip = it.trip?.copy(fwVersion = fw, source = source)) }
            }
        }
        DeviceConnection.messages.collect { received ->
            dao.insertMessage(DeviceMessageEntity(tripId = trip.id, receivedAt = received.receivedAt, text = received.text))
            val message = received.message
            // Unsynced messages carry device time, not phone time, so they can't be placed (contract §2).
            if ((message is SegMessage || message is EvtMessage) && message.synced) {
                waiting += message
                saveReadyMessages()
            }
        }
    }

    /**
     * Saves each waiting message once the GPS fix after its time has arrived, or once it has waited
     * long enough (then with the fixes there are, or no location). [all] saves everything now.
     */
    private fun saveReadyMessages(all: Boolean = false) {
        val currentTrip = trip ?: return
        val newestFix = fixes.newestTime
        val now = System.currentTimeMillis()
        val ready = waiting.filter { message ->
            val t = message.time()
            all || (newestFix != null && newestFix >= t) || now > t + WAIT_FOR_FIX_MS
        }
        if (ready.isEmpty()) return
        waiting -= ready.toSet()
        ready.forEach { message ->
            val fix = fixes.at(message.time())
            when (message) {
                is SegMessage -> saveSegment(currentTrip, message, fix)
                is EvtMessage -> saveEvent(currentTrip, message, fix)
                else -> {}
            }
        }
    }

    private fun saveSegment(trip: Trip, seg: SegMessage, fix: GpsFix?) {
        val entity = SegmentEntity(
            tripId = trip.id,
            tStart = seg.tStart,
            tEnd = seg.tEnd,
            lat = fix?.lat,
            lon = fix?.lon,
            speedMps = fix?.speedMps,
            gpsAccuracyM = fix?.accuracyM,
            headingDeg = fix?.headingDeg,
            rmsZ = seg.rmsZ,
            peakZ = seg.peakZ,
            rollRms = seg.rollRms,
            pitchRms = seg.pitchRms,
        )
        scope.launch { dao.insertSegment(entity) }
        RecordingState.update {
            it.copy(
                segments = it.segments + 1,
                segmentsWithoutLocation = it.segmentsWithoutLocation + if (fix == null) 1 else 0,
                lastRoughness = seg.rmsZ,
            )
        }
    }

    private fun saveEvent(trip: Trip, evt: EvtMessage, fix: GpsFix?) {
        val entity = EventEntity(
            id = UUID.randomUUID().toString(),
            tripId = trip.id,
            vehicleId = trip.vehicleId,
            t = evt.t,
            lat = fix?.lat,
            lon = fix?.lon,
            speedMps = fix?.speedMps,
            gpsAccuracyM = fix?.accuracyM,
            headingDeg = fix?.headingDeg,
            eventClass = EventClass.entries.firstOrNull { it.name.equals(evt.eventClass, ignoreCase = true) }
                ?: EventClass.UNKNOWN,
            peakZ = evt.peakZ,
            rollPeak = evt.rollPeak,
            pitchPeak = evt.pitchPeak,
            conf = evt.conf,
        )
        scope.launch { dao.insertEvent(entity) }
        RecordingState.update { it.copy(events = it.events + 1) }
    }

    /** The phone time a message's location is taken at: a segment's middle, an event's peak. */
    private fun DeviceMessage.time(): Long = when (this) {
        is SegMessage -> (tStart + tEnd) / 2
        is EvtMessage -> t
        else -> 0L
    }

    // ------------------------------------------------------------ background loops

    private suspend fun uploadEveryMinute() {
        while (scope.isActive) {
            delay(UPLOAD_INTERVAL_MS)
            TripUploader.uploadPending(applicationContext)
        }
    }

    /** Without GPS no fixes arrive, so messages would wait forever; this saves them anyway. */
    private suspend fun checkWaitingMessagesEverySecond() {
        while (scope.isActive) {
            delay(1_000)
            saveReadyMessages()
        }
    }

    private suspend fun showDeviceStateInNotification() {
        DeviceConnection.status.map { it.state }.distinctUntilChanged().collect { state ->
            updateNotification(
                when (state) {
                    DeviceState.CONNECTED -> "Recording with the device"
                    DeviceState.CONNECTING, DeviceState.RECONNECTING -> "Recording GPS · waiting for the device"
                    DeviceState.NOT_CONNECTED -> "Recording GPS only · no device connected"
                }
            )
        }
    }

    // ------------------------------------------------------------ notification

    /** Location always; connected device too when allowed (Android 14+ requires the permission). */
    private fun foregroundTypes(): Int {
        val bluetoothAllowed = ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT) ==
            PackageManager.PERMISSION_GRANTED
        return ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION or
            if (bluetoothAllowed) ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE else 0
    }

    private fun createNotificationChannel() {
        // Low importance: shows in the status bar without making a sound.
        val channel = NotificationChannel(CHANNEL_ID, "Trip recording", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun updateNotification(text: String) {
        // Without the notification permission this shows nothing, which is fine.
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification(text))
    }

    private fun buildNotification(text: String = "Road Quality Mapper is recording your route") =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_recording)
            .setContentTitle("Recording trip")
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
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
