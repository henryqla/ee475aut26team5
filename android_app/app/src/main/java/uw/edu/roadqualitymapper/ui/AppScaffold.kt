package uw.edu.roadqualitymapper.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import uw.edu.roadqualitymapper.ble.DeviceConnection
import uw.edu.roadqualitymapper.data.SelectedVehicle
import uw.edu.roadqualitymapper.ui.screens.ConnectScreen
import uw.edu.roadqualitymapper.ui.screens.DriveScreen
import uw.edu.roadqualitymapper.ui.screens.MapScreen

enum class Tab(val label: String, val emoji: String) {
    CONNECT("Connect", "🔌"),
    DRIVE("Drive", "🚗"),
    MAP("Map", "🗺️"),
}

@Composable
fun AppScaffold() {
    // Start on Connect until a device is remembered and a vehicle is picked; after that, on Drive.
    var tab by rememberSaveable {
        val setUp = DeviceConnection.status.value.address != null && SelectedVehicle.id.value != null
        mutableStateOf(if (setUp) Tab.DRIVE else Tab.CONNECT)
    }

    Scaffold(
        bottomBar = {
            NavigationBar {
                Tab.entries.forEach { t ->
                    NavigationBarItem(
                        selected = tab == t,
                        onClick = { tab = t },
                        icon = { Text(t.emoji) },
                        label = { Text(t.label) },
                    )
                }
            }
        }
    ) { innerPadding ->
        val screenModifier = Modifier.padding(innerPadding).fillMaxSize()
        when (tab) {
            Tab.CONNECT -> ConnectScreen(screenModifier)
            Tab.DRIVE -> DriveScreen(screenModifier)
            Tab.MAP -> MapScreen(screenModifier)
        }
    }
}