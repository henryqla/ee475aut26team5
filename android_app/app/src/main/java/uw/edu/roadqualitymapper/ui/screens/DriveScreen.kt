package uw.edu.roadqualitymapper.ui.screens

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.DrawableRes
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import uw.edu.roadqualitymapper.BuildConfig
import uw.edu.roadqualitymapper.R
import uw.edu.roadqualitymapper.ble.DeviceConnection
import uw.edu.roadqualitymapper.ble.DeviceState
import uw.edu.roadqualitymapper.data.AppSettings
import uw.edu.roadqualitymapper.data.ColorMode
import uw.edu.roadqualitymapper.data.DataSource
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.GpsFix
import uw.edu.roadqualitymapper.data.SelectedVehicle
import uw.edu.roadqualitymapper.data.TripUploader
import uw.edu.roadqualitymapper.data.local.RoadDatabase
import uw.edu.roadqualitymapper.service.RecordingService
import uw.edu.roadqualitymapper.service.RecordingState
import uw.edu.roadqualitymapper.service.RecordingStatus
import uw.edu.roadqualitymapper.ui.components.AppCard
import uw.edu.roadqualitymapper.ui.components.AppIcon
import uw.edu.roadqualitymapper.ui.components.SectionLabel
import uw.edu.roadqualitymapper.ui.components.StatTile
import uw.edu.roadqualitymapper.ui.components.StatusChip
import uw.edu.roadqualitymapper.ui.eventMarkerHue
import uw.edu.roadqualitymapper.ui.roughnessColor
import uw.edu.roadqualitymapper.ui.theme.LocalDarkTheme
import uw.edu.roadqualitymapper.ui.theme.StatusAmber
import uw.edu.roadqualitymapper.ui.theme.StatusGray
import uw.edu.roadqualitymapper.ui.theme.StatusGreen
import java.text.DateFormat
import java.util.Date

private const val MPS_TO_MPH = 2.23694

@Composable
fun DriveScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val status by RecordingState.status.collectAsState()
    val vehicleId by SelectedVehicle.id.collectAsState()
    val device by DeviceConnection.status.collectAsState()
    var permissionMessage by remember { mutableStateOf<String?>(null) }
    // Ask for notifications once per screen visit; recording works without them, just hidden.
    var askedForNotifications by rememberSaveable { mutableStateOf(false) }

    // Ticks once a second during a trip, for the trip timer and the upload countdown.
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(status.recording) {
        while (status.recording) {
            now = System.currentTimeMillis()
            delay(1_000)
        }
        now = System.currentTimeMillis()
    }

    // Start a trip once the permissions are sorted out. Each permission answer calls this again,
    // so the steps run in order: precise location (required) → notifications (optional) → start.
    lateinit var startTrip: () -> Unit
    val notificationLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { startTrip() }
    val locationLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        when {
            results[Manifest.permission.ACCESS_FINE_LOCATION] == true -> startTrip()
            results[Manifest.permission.ACCESS_COARSE_LOCATION] == true ->
                permissionMessage = "Turn on \"Precise location\" to record"
            else -> permissionMessage = "Allow location in Settings to record"
        }
    }
    startTrip = start@{
        permissionMessage = null
        val vehicle = vehicleId ?: return@start
        when {
            !context.hasPermission(Manifest.permission.ACCESS_FINE_LOCATION) ->
                locationLauncher.launch(
                    arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)
                )
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                !context.hasPermission(Manifest.permission.POST_NOTIFICATIONS) &&
                !askedForNotifications -> {
                askedForNotifications = true
                notificationLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
            else -> RecordingService.start(context, vehicle)
        }
    }

    Column(
        modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        TripHeader(status, vehicleId, now)

        StartStopButton(
            recording = status.recording,
            // A trip needs the device: without it there's no road data. Losing the device
            // mid-trip doesn't stop the trip; it reconnects.
            enabled = status.recording || (vehicleId != null && device.state == DeviceState.CONNECTED),
            onClick = { if (status.recording) RecordingService.stop(context) else startTrip() },
        )
        if (vehicleId == null && !status.recording) {
            Text("Pick your vehicle on the Connect tab first.", style = MaterialTheme.typography.bodySmall)
        }
        (permissionMessage ?: status.error)?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }
        if (status.trip?.source == DataSource.FAKE_DEVICE) {
            Text("Test device: marked as test data", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        TripStats(status)
        UploadCard(status, now)
        LabelButtons(status)

        if (BuildConfig.DEBUG) GpsDetailsCard(status)
    }
}

// ---------------------------------------------------------------- header and Start/Stop

/** The device's state on the left; the trip's vehicle and running time on the right. */
@Composable
private fun TripHeader(status: RecordingStatus, vehicleId: String?, now: Long) {
    val device by DeviceConnection.status.collectAsState()
    val (text, dot) = when (device.state) {
        DeviceState.CONNECTED -> "Device connected" to StatusGreen
        DeviceState.CONNECTING -> "Connecting to the device…" to StatusAmber
        DeviceState.RECONNECTING -> "Device lost. Waiting for it…" to StatusAmber
        DeviceState.NOT_CONNECTED -> "No device connected" to StatusGray
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        StatusChip(
            text = text,
            container = MaterialTheme.colorScheme.surfaceVariant,
            content = MaterialTheme.colorScheme.onSurface,
            dot = dot,
        )
        Spacer(Modifier.weight(1f))
        val trip = status.trip
        val right = when {
            trip != null -> {
                val duration = formatDuration((trip.endTime ?: now) - trip.startTime)
                (if (status.recording) "" else "Last trip · ") + "${vehicleLabel(trip.vehicleId)} · $duration"
            }
            vehicleId != null -> vehicleLabel(vehicleId)
            else -> null
        }
        right?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    // The device's own status message says its IMU stopped: no road data until it's fixed.
    if (device.state == DeviceState.CONNECTED && device.imuOk == false) {
        StatusChip(
            text = "IMU not OK: no road data",
            container = MaterialTheme.colorScheme.error.copy(alpha = 0.15f),
            content = MaterialTheme.colorScheme.error,
            icon = R.drawable.ic_alert_circle,
        )
    }
}

@Composable
private fun StartStopButton(recording: Boolean, enabled: Boolean, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = Modifier.fillMaxWidth().height(56.dp),
        shape = RoundedCornerShape(18.dp),
        colors = if (recording) {
            ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error, contentColor = MaterialTheme.colorScheme.onError)
        } else {
            ButtonDefaults.buttonColors()
        },
    ) {
        AppIcon(if (recording) R.drawable.ic_player_stop else R.drawable.ic_player_play, contentDescription = null, size = 22.dp)
        Spacer(Modifier.width(8.dp))
        Text(if (recording) "Stop trip" else "Start trip", style = MaterialTheme.typography.titleMedium)
    }
}

// ---------------------------------------------------------------- trip numbers

/** Segments, bumps, current roughness and GPS accuracy: the current trip, or the last one. */
@Composable
private fun TripStats(status: RecordingStatus) {
    val colorMode by AppSettings.colorMode.collectAsState()
    val hasTrip = status.trip != null
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            StatTile(
                label = "Segments",
                value = if (hasTrip) "%,d".format(status.segments) else "—",
                note = if (status.segmentsWithoutLocation > 0) "${status.segmentsWithoutLocation} without GPS" else null,
                modifier = Modifier.weight(1f),
            )
            StatTile(
                label = "Bumps",
                value = if (hasTrip) "%,d".format(status.events) else "—",
                modifier = Modifier.weight(1f),
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val roughness = status.lastRoughness
            StatTile(
                label = "Roughness now",
                value = roughness?.let { "%.3f".format(it) } ?: "—",
                unit = roughness?.let { "g" },
                swatch = roughness?.let { roughnessColor(it, colorMode, LocalDarkTheme.current) },
                modifier = Modifier.weight(1f),
            )
            val accuracy = status.lastFix?.accuracyM
            StatTile(
                label = "GPS",
                value = accuracy?.let { "±%.0f".format(it) } ?: "—",
                unit = accuracy?.let { "m" },
                note = if (status.recording && !status.gpsAvailable) "Waiting for GPS…" else null,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

// ---------------------------------------------------------------- upload

/** What's still on the phone only, when it goes next, and a button to send it now. */
@Composable
private fun UploadCard(status: RecordingStatus, now: Long) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val notUploaded by remember { RoadDatabase.get(context).dao().countNotUploaded() }.collectAsState(initial = 0)
    val upload by TripUploader.status.collectAsState()

    val title = if (notUploaded == 0) "All uploaded" else "%,d waiting to upload".format(notUploaded)
    val nextUploadAt = status.nextUploadAt
    val detail = when {
        upload.uploading -> "Uploading…"
        upload.failed -> upload.lastResult
        status.recording && nextUploadAt != null ->
            "Next upload in ${((nextUploadAt - now) / 1000).coerceAtLeast(0)} s"
        else -> null
    }

    AppCard {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            AppIcon(R.drawable.ic_cloud_upload, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleSmall)
                detail?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = if (upload.failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            TextButton(
                onClick = { scope.launch { TripUploader.uploadPending(context.applicationContext) } },
                enabled = !upload.uploading,
            ) {
                Text("Upload now")
            }
        }
    }
}

// ---------------------------------------------------------------- label buttons

/** Ground-truth labels for tuning the detector and training the classifier. */
@Composable
private fun LabelButtons(status: RecordingStatus) {
    val context = LocalContext.current
    val colorMode by AppSettings.colorMode.collectAsState()
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        LabelButton("Pothole", R.drawable.ic_alert_triangle, EventClass.POTHOLE, colorMode, status.recording, Modifier.weight(1f)) {
            RecordingService.label(context, EventClass.POTHOLE)
        }
        LabelButton("Speed bump", R.drawable.ic_wave_sine, EventClass.SPEED_BUMP, colorMode, status.recording, Modifier.weight(1f)) {
            RecordingService.label(context, EventClass.SPEED_BUMP)
        }
    }
    status.lastLabel?.let {
        Text(
            it,
            modifier = Modifier.fillMaxWidth(),
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** A big tinted button in the same color as that kind of marker on the map. */
@Composable
private fun LabelButton(
    text: String,
    @DrawableRes icon: Int,
    eventClass: EventClass,
    colorMode: ColorMode,
    enabled: Boolean,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val hue = eventMarkerHue(eventClass, colorMode)
    val dark = LocalDarkTheme.current
    val container = if (dark) Color.hsv(hue, 0.45f, 0.30f) else Color.hsv(hue, 0.15f, 0.98f)
    val content = if (dark) Color.hsv(hue, 0.30f, 0.95f) else Color.hsv(hue, 0.70f, 0.50f)
    Surface(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        color = if (enabled) container else MaterialTheme.colorScheme.surfaceVariant,
        contentColor = if (enabled) content else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
    ) {
        Column(
            Modifier.padding(vertical = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            AppIcon(icon, contentDescription = null)
            Text(text, style = MaterialTheme.typography.labelLarge)
        }
    }
}

// ---------------------------------------------------------------- debug details

/** Debug builds only: the raw GPS readings, folded away, for checking a trip in detail. */
@Composable
private fun GpsDetailsCard(status: RecordingStatus) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    AppCard {
        Row(Modifier.fillMaxWidth().clickable { expanded = !expanded }, verticalAlignment = Alignment.CenterVertically) {
            SectionLabel("GPS details", Modifier.weight(1f))
            AppIcon(
                if (expanded) R.drawable.ic_chevron_up else R.drawable.ic_chevron_down,
                contentDescription = if (expanded) "Hide GPS details" else "Show GPS details",
                size = 20.dp,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (expanded) {
            val style = MaterialTheme.typography.bodySmall
            status.trip?.let { trip ->
                Text("Started ${timeOf(trip.startTime)}" + (trip.endTime?.let { ", ended ${timeOf(it)}" } ?: ""), style = style)
            }
            Text("GPS fixes: ${status.fixCount}", style = style)
            status.lastFix?.let { FixDetails(it) }
        }
    }
}

@Composable
private fun FixDetails(fix: GpsFix) {
    val style = MaterialTheme.typography.bodySmall
    Text("Position: %.6f, %.6f".format(fix.lat, fix.lon), style = style)
    Text("Speed: " + (fix.speedMps?.let { "%.1f m/s (%.0f mph)".format(it, it * MPS_TO_MPH) } ?: "—"), style = style)
    Text("Heading: " + (fix.headingDeg?.let { "%.0f°".format(it) } ?: "— (moving too slowly)"), style = style)
    Text("Accuracy: " + (fix.accuracyM?.let { "±%.0f m".format(it) } ?: "—"), style = style)
    Text("Last fix: ${timeOf(fix.t)}", style = style)
}

/** 12:04, or 1:02:04 past an hour. */
private fun formatDuration(millis: Long): String {
    val totalSeconds = (millis / 1000).coerceAtLeast(0)
    val hours = totalSeconds / 3600
    val minutes = totalSeconds % 3600 / 60
    val seconds = totalSeconds % 60
    return if (hours > 0) "%d:%02d:%02d".format(hours, minutes, seconds) else "%d:%02d".format(minutes, seconds)
}

private fun timeOf(epochMillis: Long): String =
    DateFormat.getTimeInstance(DateFormat.MEDIUM).format(Date(epochMillis))

private fun Context.hasPermission(permission: String) =
    ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED
