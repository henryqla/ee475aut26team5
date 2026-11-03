package uw.edu.roadqualitymapper.ui.screens

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import uw.edu.roadqualitymapper.ble.DeviceConnection
import uw.edu.roadqualitymapper.ble.DeviceState
import uw.edu.roadqualitymapper.ble.DeviceStatus
import uw.edu.roadqualitymapper.ble.FoundDevice
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

    Column(
        modifier
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        DeviceSection()

        HorizontalDivider()

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

@Composable
private fun DeviceSection() {
    val status by DeviceConnection.status.collectAsState()
    val scanning by DeviceConnection.scanning.collectAsState()
    val found by DeviceConnection.found.collectAsState()
    val rawLog by DeviceConnection.rawLog.collectAsState()
    var permissionMessage by remember { mutableStateOf<String?>(null) }

    // Bluetooth must be on, and "Nearby devices" allowed, before scanning.
    val enableBluetooth = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (DeviceConnection.isBluetoothOn()) DeviceConnection.startScan()
    }
    val askPermissions = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (DeviceConnection.hasBluetoothPermissions()) {
            permissionMessage = null
            if (DeviceConnection.isBluetoothOn()) {
                DeviceConnection.startScan()
            } else {
                enableBluetooth.launch(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE))
            }
        } else {
            permissionMessage = "The device can't be found without \"Nearby devices\". Allow it in " +
                "Settings → Apps → Road Quality Mapper → Permissions."
        }
    }
    val scan = {
        when {
            !DeviceConnection.hasBluetoothPermissions() -> askPermissions.launch(
                arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
            )
            !DeviceConnection.isBluetoothOn() -> enableBluetooth.launch(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE))
            else -> DeviceConnection.startScan()
        }
    }

    Text("Device", style = MaterialTheme.typography.headlineMedium)
    Text(stateText(status), style = MaterialTheme.typography.titleMedium)
    (permissionMessage ?: status.error)?.let { Text(it, color = MaterialTheme.colorScheme.error) }

    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = scan, enabled = !scanning) {
            Text(if (scanning) "Scanning…" else "Scan for device")
        }
        when {
            status.state != DeviceState.NOT_CONNECTED ->
                OutlinedButton(onClick = { DeviceConnection.disconnect() }) { Text("Disconnect") }
            status.address != null -> {
                OutlinedButton(onClick = { DeviceConnection.connectToRemembered() }) { Text("Connect") }
                TextButton(onClick = { DeviceConnection.forget() }) { Text("Forget") }
            }
        }
    }

    if (scanning || found.isNotEmpty()) {
        Text("Tap a device to connect. Only Road Quality devices are listed.", style = MaterialTheme.typography.bodySmall)
        found.forEach { FoundDeviceRow(it) }
        if (!scanning && found.isEmpty()) Text("No device found. Is it powered and advertising?")
    }

    if (status.address != null) DeviceDetails(status)

    if (rawLog.isNotEmpty()) {
        Text("Last messages (newest first)", style = MaterialTheme.typography.titleSmall)
        rawLog.forEach {
            Text(it, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
        }
    }
}

@Composable
private fun FoundDeviceRow(device: FoundDevice) {
    Card(
        Modifier
            .fillMaxWidth()
            .clickable { DeviceConnection.connect(device) }
    ) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(device.name, style = MaterialTheme.typography.titleSmall)
                Text(device.address, style = MaterialTheme.typography.bodySmall)
            }
            Text("${device.rssi} dBm", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun DeviceDetails(status: DeviceStatus) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("${status.name ?: "Device"} · ${status.address}", style = MaterialTheme.typography.bodySmall)
        status.fwVersion?.let { fw ->
            Text(
                "Firmware $fw · IMU ${okText(status.imuOk)} · SD card ${okText(status.sdOk)}" +
                    (status.rateHz?.let { " · $it Hz" } ?: "")
            )
        }
        Text(
            "Received ${status.received} · lost ${status.dropped} · " +
                "before time sync ${status.unsynced} · unreadable ${status.unreadable}"
        )
    }
}

private fun stateText(status: DeviceStatus): String {
    val name = status.name ?: "the device"
    return when (status.state) {
        DeviceState.NOT_CONNECTED -> if (status.address != null) "Not connected to $name" else "No device yet: scan to find it"
        DeviceState.CONNECTING -> "Connecting to $name…"
        DeviceState.CONNECTED -> "Connected to $name"
        DeviceState.RECONNECTING -> "Lost $name. Reconnects when it's back"
    }
}

private fun okText(ok: Boolean?) = when (ok) {
    true -> "OK"
    false -> "NOT OK"
    null -> "?"
}
