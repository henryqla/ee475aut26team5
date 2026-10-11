package uw.edu.roadqualitymapper.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

// Placeholder until vehicles come from the backend (Phase 2).
private val placeholderVehicles = listOf(
    "truck" to "Truck",
    "camry" to "Camry (sedan)",
    "escape" to "Escape (SUV)",
)

@Composable
fun ConnectScreen(modifier: Modifier = Modifier) {
    var selectedVehicle by rememberSaveable { mutableStateOf(placeholderVehicles.first().first) }

    Column(modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Device", style = MaterialTheme.typography.headlineMedium)
        Text("Status: not connected")
        Button(
            onClick = { /* Phase 4: BLE scan */ },
            enabled = false,
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Scan for device") }

        Text("Vehicle", style = MaterialTheme.typography.titleMedium)
        placeholderVehicles.forEach { (id, name) ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .selectable(selected = selectedVehicle == id, onClick = { selectedVehicle = id }),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = selectedVehicle == id, onClick = null)
                Text(name, Modifier.padding(start = 8.dp))
            }
        }
    }
}