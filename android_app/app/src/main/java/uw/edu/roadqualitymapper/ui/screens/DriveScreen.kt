package uw.edu.roadqualitymapper.ui.screens

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import uw.edu.roadqualitymapper.data.GpsFix
import uw.edu.roadqualitymapper.data.SelectedVehicle
import uw.edu.roadqualitymapper.service.RecordingService
import uw.edu.roadqualitymapper.service.RecordingState
import uw.edu.roadqualitymapper.service.RecordingStatus
import java.text.DateFormat
import java.util.Date

private const val MPS_TO_MPH = 2.23694

@Composable
fun DriveScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val status by RecordingState.status.collectAsState()
    val vehicleId by SelectedVehicle.id.collectAsState()
    var keepScreenOn by rememberSaveable { mutableStateOf(true) }
    var permissionMessage by remember { mutableStateOf<String?>(null) }
    // Ask for notifications once per screen visit; recording works without them, just hidden.
    var askedForNotifications by rememberSaveable { mutableStateOf(false) }

    KeepScreenOn(keepScreenOn)

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
                permissionMessage = "Trips need your precise location. Allow location again and " +
                    "turn on \"Use precise location\"."
            else -> permissionMessage = "Trips need location access. Allow it in Settings → Apps → " +
                "Road Quality Mapper → Permissions → Location."
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

    Column(modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Drive", style = MaterialTheme.typography.headlineMedium)

        Button(
            onClick = { if (status.recording) RecordingService.stop(context) else startTrip() },
            enabled = status.recording || vehicleId != null,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (status.recording) "Stop trip" else "Start trip")
        }
        if (vehicleId == null && !status.recording) {
            Text("Pick your vehicle on the Connect tab first.")
        }
        (permissionMessage ?: status.error)?.let {
            Text(it, color = MaterialTheme.colorScheme.error)
        }

        TripStatus(status)

        // Ground-truth labels. Enabled in Phase 4 once there's a trip to attach them to.
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = {}, enabled = false, modifier = Modifier.weight(1f)) {
                Text("Pothole!")
            }
            OutlinedButton(onClick = {}, enabled = false, modifier = Modifier.weight(1f)) {
                Text("Speed bump!")
            }
        }

        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Keep screen on", Modifier.weight(1f))
            Switch(checked = keepScreenOn, onCheckedChange = { keepScreenOn = it })
        }
    }
}

/** What's being recorded right now, or a summary of the last trip after Stop. */
@Composable
private fun TripStatus(status: RecordingStatus) {
    val trip = status.trip ?: run {
        Text("Not recording")
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            if (status.recording) "Recording · ${trip.vehicleId}" else "Last trip · ${trip.vehicleId}",
            style = MaterialTheme.typography.titleMedium,
        )
        Text("Started ${timeOf(trip.startTime)}" + (trip.endTime?.let { ", ended ${timeOf(it)}" } ?: ""))
        Text("GPS fixes: ${status.fixCount}")
        if (status.recording && !status.gpsAvailable) {
            Text("Waiting for GPS…", color = MaterialTheme.colorScheme.error)
        }
        status.lastFix?.let { FixDetails(it) }
    }
}

@Composable
private fun FixDetails(fix: GpsFix) {
    Text("Position: %.6f, %.6f".format(fix.lat, fix.lon))
    Text(
        "Speed: " + (fix.speedMps?.let { "%.1f m/s (%.0f mph)".format(it, it * MPS_TO_MPH) } ?: "—")
    )
    Text("Heading: " + (fix.headingDeg?.let { "%.0f°".format(it) } ?: "— (moving too slowly)"))
    Text("Accuracy: " + (fix.accuracyM?.let { "±%.0f m".format(it) } ?: "—"))
    Text("Last fix: ${timeOf(fix.t)}")
}

private fun timeOf(epochMillis: Long): String =
    DateFormat.getTimeInstance(DateFormat.MEDIUM).format(Date(epochMillis))

private fun Context.hasPermission(permission: String) =
    ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

/** Keeps the display awake while this screen is showing (and the toggle is on). */
@Composable
private fun KeepScreenOn(enabled: Boolean) {
    val view = LocalView.current
    DisposableEffect(enabled) {
        view.keepScreenOn = enabled
        onDispose { view.keepScreenOn = false }
    }
}
