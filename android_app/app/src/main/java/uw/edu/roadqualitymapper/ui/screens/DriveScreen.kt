package uw.edu.roadqualitymapper.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.dp

@Composable
fun DriveScreen(modifier: Modifier = Modifier) {
    // Stub: in Phase 3 this state comes from RecordingService instead.
    var recording by rememberSaveable { mutableStateOf(false) }
    var keepScreenOn by rememberSaveable { mutableStateOf(true) }

    KeepScreenOn(keepScreenOn)

    Column(modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Drive", style = MaterialTheme.typography.headlineMedium)
        Text(if (recording) "Recording (stub)" else "Not recording")

        Button(onClick = { recording = !recording }, modifier = Modifier.fillMaxWidth()) {
            Text(if (recording) "Stop trip" else "Start trip")
        }

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

/** Keeps the display awake while this screen is showing (and the toggle is on). */
@Composable
private fun KeepScreenOn(enabled: Boolean) {
    val view = LocalView.current
    DisposableEffect(enabled) {
        view.keepScreenOn = enabled
        onDispose { view.keepScreenOn = false }
    }
}