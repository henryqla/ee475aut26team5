package uw.edu.roadqualitymapper.ui.screens

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.TextAutoSize
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import uw.edu.roadqualitymapper.R
import uw.edu.roadqualitymapper.ble.DeviceConnection
import uw.edu.roadqualitymapper.ble.DeviceState
import uw.edu.roadqualitymapper.ble.DeviceStatus
import uw.edu.roadqualitymapper.ble.FoundDevice
import uw.edu.roadqualitymapper.data.SelectedVehicle
import uw.edu.roadqualitymapper.ui.components.AppCard
import uw.edu.roadqualitymapper.ui.components.AppIcon
import uw.edu.roadqualitymapper.ui.components.NeutralChip
import uw.edu.roadqualitymapper.ui.components.SectionLabel
import uw.edu.roadqualitymapper.ui.components.StatusChip
import uw.edu.roadqualitymapper.ui.components.StatusDot
import uw.edu.roadqualitymapper.ui.theme.StatusAmber
import uw.edu.roadqualitymapper.ui.theme.StatusGray
import uw.edu.roadqualitymapper.ui.theme.StatusGreen

/** A vehicle the phone can be in: its ID in the database, the name shown, and its icon. */
private class VehicleOption(val id: String, val label: String, @DrawableRes val icon: Int)

// Shown as general vehicle types, but uploaded with the IDs of the team's own cars, which are the
// only vehicles in the database (Ben's vehicles table) and carry each car's calibration.
private val vehicleOptions = listOf(
    VehicleOption("camry", "Sedan", R.drawable.ic_car),
    VehicleOption("escape", "SUV", R.drawable.ic_car_suv),
    VehicleOption("truck", "Truck", R.drawable.ic_truck),
)

/** The name shown for a vehicle ID, e.g. "Sedan" for camry. */
internal fun vehicleLabel(vehicleId: String): String =
    vehicleOptions.firstOrNull { it.id == vehicleId }?.label ?: vehicleId

@Composable
fun ConnectScreen(modifier: Modifier = Modifier) {
    Column(
        modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        DeviceCard()
        VehicleCard()
        LiveMessagesCard()
    }
}

// ---------------------------------------------------------------- device

@Composable
private fun DeviceCard() {
    val status by DeviceConnection.status.collectAsState()
    val scanning by DeviceConnection.scanning.collectAsState()
    val found by DeviceConnection.found.collectAsState()
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
            permissionMessage = "Allow \"Nearby devices\" in Settings"
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

    // What a scan found, while not connected. Once connected, the device is shown in the header.
    val showFound = found.isNotEmpty() && status.state != DeviceState.CONNECTED

    AppCard {
        SectionLabel("Device")
        DeviceHeader(status, scanning = scanning, foundCount = if (showFound) found.size else 0)
        if (status.state == DeviceState.CONNECTED) DeviceChips(status)
        (permissionMessage ?: status.error)?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }

        // The buttons split the row's width, so they always fit on one line whatever the phone's width.
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val fill = Modifier.weight(1f)
            val scanLabel = if (scanning) "Scanning…" else "Scan"
            if (status.address == null) {
                // No device yet: scanning is the one thing to do here.
                Button(onClick = scan, enabled = !scanning, modifier = fill, shape = BUTTON_SHAPE) { OneLine(scanLabel) }
            } else {
                OutlinedButton(onClick = scan, enabled = !scanning, modifier = fill, shape = BUTTON_SHAPE, contentPadding = TIGHT_PADDING) {
                    OneLine(scanLabel)
                }
            }
            when {
                status.state != DeviceState.NOT_CONNECTED ->
                    FilledTonalButton(onClick = { DeviceConnection.disconnect() }, modifier = fill, shape = BUTTON_SHAPE, contentPadding = TIGHT_PADDING) {
                        OneLine("Disconnect")
                    }
                status.address != null -> {
                    FilledTonalButton(onClick = { DeviceConnection.connectToRemembered() }, modifier = fill, shape = BUTTON_SHAPE, contentPadding = TIGHT_PADDING) {
                        OneLine("Connect")
                    }
                    TextButton(onClick = { DeviceConnection.forget() }, modifier = fill, shape = BUTTON_SHAPE, contentPadding = TIGHT_PADDING) {
                        OneLine("Forget")
                    }
                }
            }
        }

        var scannedOnce by remember { mutableStateOf(false) }
        LaunchedEffect(scanning) { if (scanning) scannedOnce = true }
        when {
            showFound -> found.forEach { FoundDeviceRow(it) }
            // Only once a scan has finished with nothing, and never next to a found device.
            scannedOnce && !scanning && status.state == DeviceState.NOT_CONNECTED ->
                Text("No device found", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/**
 * Icon, device name, and a colored dot with the connection state. While not connected, it also
 * follows the scan, so it never says nothing was found while a found device is listed below.
 */
@Composable
private fun DeviceHeader(status: DeviceStatus, scanning: Boolean, foundCount: Int) {
    val (stateText, dot) = when (status.state) {
        DeviceState.CONNECTED -> "Connected" to StatusGreen
        DeviceState.CONNECTING -> "Connecting…" to StatusAmber
        DeviceState.RECONNECTING -> "Lost. Waiting for it…" to StatusAmber
        DeviceState.NOT_CONNECTED -> when {
            foundCount > 0 -> "Found. Tap Connect below" to StatusGreen
            scanning -> "Looking for your device…" to StatusAmber
            status.address != null -> "Not connected" to StatusGray
            else -> "Tap Scan to find your device" to StatusGray
        }
    }
    val title = status.name ?: if (foundCount > 0) "Device found" else "No device yet"
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(
            Modifier
                .size(40.dp)
                .background(MaterialTheme.colorScheme.primaryContainer, RoundedCornerShape(12.dp)),
            contentAlignment = Alignment.Center,
        ) {
            AppIcon(R.drawable.ic_bluetooth, contentDescription = null, size = 22.dp, tint = MaterialTheme.colorScheme.primary)
        }
        Column {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                StatusDot(dot)
                Text(stateText, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/** What the device reported about itself in its status message (contract §4.3). */
@Composable
private fun DeviceChips(status: DeviceStatus) {
    @OptIn(ExperimentalLayoutApi::class)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        status.fwVersion?.let { NeutralChip("Firmware $it") }
        when (status.imuOk) {
            true -> OkChip("IMU")
            false -> ProblemChip("IMU not OK")
            null -> {}
        }
        when (status.sdOk) {
            true -> OkChip("SD card")
            false -> NeutralChip("No SD card")
            null -> {}
        }
    }
}

@Composable
private fun OkChip(text: String) = StatusChip(
    text = text,
    container = StatusGreen.copy(alpha = 0.15f),
    content = MaterialTheme.colorScheme.onSurface,
    icon = R.drawable.ic_check,
)

@Composable
private fun ProblemChip(text: String) = StatusChip(
    text = text,
    container = MaterialTheme.colorScheme.error.copy(alpha = 0.15f),
    content = MaterialTheme.colorScheme.error,
    icon = R.drawable.ic_alert_circle,
)

/**
 * A device a scan found: hard to miss, with a thick border, a green "found" dot, and a solid
 * Connect button. The whole row can be tapped too.
 */
@Composable
private fun FoundDeviceRow(device: FoundDevice) {
    val connect = { DeviceConnection.connect(device) }
    Surface(
        onClick = connect,
        shape = RoundedCornerShape(14.dp),
        color = StatusGreen.copy(alpha = 0.12f),
        border = BorderStroke(2.dp, StatusGreen),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            StatusDot(StatusGreen)
            Column(Modifier.weight(1f)) {
                Text(device.name, style = MaterialTheme.typography.titleMedium)
                Text(device.address, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Button(onClick = connect, shape = BUTTON_SHAPE) { Text("Connect") }
        }
    }
}

private val BUTTON_SHAPE = RoundedCornerShape(12.dp)

/** Less side padding than the default, so labels have room on narrow phones. */
private val TIGHT_PADDING = PaddingValues(horizontal = 8.dp, vertical = 8.dp)

/** A button label that stays on one line, shrinking its text if the button is too narrow. */
@Composable
private fun OneLine(text: String) {
    Text(
        text,
        maxLines = 1,
        softWrap = false,
        autoSize = TextAutoSize.StepBased(minFontSize = 10.sp, maxFontSize = 14.sp),
    )
}

// ---------------------------------------------------------------- vehicle

@Composable
private fun VehicleCard() {
    val context = LocalContext.current
    // Remembered between launches; every trip is tagged with it.
    val selectedVehicle by SelectedVehicle.id.collectAsState()
    AppCard {
        SectionLabel("Vehicle")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            vehicleOptions.forEach { option ->
                VehicleTile(
                    option = option,
                    selected = selectedVehicle == option.id,
                    onClick = { SelectedVehicle.set(context, option.id) },
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }
}

@Composable
private fun VehicleTile(option: VehicleOption, selected: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val colors = MaterialTheme.colorScheme
    Surface(
        onClick = onClick,
        modifier = modifier,
        shape = RoundedCornerShape(12.dp),
        color = if (selected) colors.primaryContainer.copy(alpha = 0.5f) else colors.surfaceContainerLowest,
        contentColor = if (selected) colors.primary else colors.onSurfaceVariant,
        border = if (selected) BorderStroke(2.dp, colors.primary) else BorderStroke(1.dp, colors.outlineVariant),
    ) {
        Column(
            Modifier.padding(vertical = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            AppIcon(option.icon, contentDescription = null, size = 26.dp)
            Text(option.label, style = MaterialTheme.typography.labelLarge)
        }
    }
}

// ---------------------------------------------------------------- live messages

/** The device's raw messages, folded away by default: mostly for debugging the firmware. */
@Composable
private fun LiveMessagesCard() {
    val status by DeviceConnection.status.collectAsState()
    val rawLog by DeviceConnection.rawLog.collectAsState()
    var expanded by rememberSaveable { mutableStateOf(false) }
    if (status.address == null && rawLog.isEmpty()) return

    AppCard {
        Row(
            Modifier
                .fillMaxWidth()
                .clickable { expanded = !expanded },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("Live messages", style = MaterialTheme.typography.titleSmall)
                Text(
                    "%,d received · %,d lost".format(status.received, status.dropped),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            AppIcon(
                if (expanded) R.drawable.ic_chevron_up else R.drawable.ic_chevron_down,
                contentDescription = if (expanded) "Hide messages" else "Show messages",
                size = 20.dp,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (expanded) {
            Text(
                "Before time sync ${status.unsynced} · unreadable ${status.unreadable}" +
                    (status.rateHz?.let { " · $it Hz" } ?: ""),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (rawLog.isEmpty()) {
                Text("No messages yet", style = MaterialTheme.typography.bodySmall)
            }
            rawLog.forEach {
                Text(it, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
            }
        }
    }
}
