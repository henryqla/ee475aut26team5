package uw.edu.roadqualitymapper.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import uw.edu.roadqualitymapper.data.SelectedVehicle

// Placeholder until vehicles come from the backend (Phase 2).
private val placeholderVehicles = listOf(
    "truck" to "Truck",
    "camry" to "Camry (sedan)",
    "escape" to "Escape (SUV)",
)

@Composable
fun ConnectScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    // Remembered between launches; every trip is tagged with it.
    val selectedVehicle by SelectedVehicle.id.collectAsState()

    Column(modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Device", style = MaterialTheme.typography.headlineMedium)
        Text("Status: not connected")
        Button(
            onClick = { /* Phase 4: BLE scan */ },
            enabled = false,
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Scan for device") }

        Text("Vehicle", style = MaterialTheme.typography.titleMedium)
        Text("Pick the car this phone is riding in.")
        placeholderVehicles.forEach { (id, name) ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .selectable(
                        selected = selectedVehicle == id,
                        onClick = { SelectedVehicle.set(context, id) },
                    ),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = selectedVehicle == id, onClick = null)
                Text(name, Modifier.padding(start = 8.dp))
            }
        }
    }
}