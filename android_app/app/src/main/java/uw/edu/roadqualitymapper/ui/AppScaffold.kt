package uw.edu.roadqualitymapper.ui

import androidx.annotation.DrawableRes
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import kotlinx.coroutines.launch
import uw.edu.roadqualitymapper.R
import uw.edu.roadqualitymapper.ble.DeviceConnection
import uw.edu.roadqualitymapper.data.AppSettings
import uw.edu.roadqualitymapper.data.SelectedVehicle
import uw.edu.roadqualitymapper.service.RecordingState
import uw.edu.roadqualitymapper.ui.components.AppIcon
import uw.edu.roadqualitymapper.ui.screens.ConnectScreen
import uw.edu.roadqualitymapper.ui.screens.DriveScreen
import uw.edu.roadqualitymapper.ui.screens.MapScreen
import uw.edu.roadqualitymapper.ui.screens.SettingsScreen

enum class Tab(val label: String, @DrawableRes val icon: Int) {
    CONNECT("Connect", R.drawable.ic_bluetooth),
    DRIVE("Drive", R.drawable.ic_car),
    MAP("Map", R.drawable.ic_map),
}

/**
 * The app's frame: a top bar (title, and ☰ for Settings), the three tabs side by side so they can
 * be swiped between, and the bottom bar. Settings opens over all of it.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppScaffold() {
    // Start on Connect until a device is remembered and a vehicle is picked; after that, on Drive.
    val setUp = DeviceConnection.status.value.address != null && SelectedVehicle.id.value != null
    val pagerState = rememberPagerState(initialPage = if (setUp) Tab.DRIVE.ordinal else Tab.CONNECT.ordinal) {
        Tab.entries.size
    }
    val scope = rememberCoroutineScope()
    var showSettings by rememberSaveable { mutableStateOf(false) }

    val recording by RecordingState.status.collectAsState()
    val keepScreenOn by AppSettings.keepScreenOn.collectAsState()
    KeepScreenOn(keepScreenOn && recording.recording)

    val currentTab = Tab.entries[pagerState.currentPage]
    // Settings opens over the tabs instead of replacing them, so the screens (the map above all,
    // which is slow to start) stay alive underneath and don't start over when Settings closes.
    Box(Modifier.fillMaxSize()) {
        Scaffold(
            topBar = {
                // The screen's name for now; the app's name or logo goes here once there is one.
                TopAppBar(
                    title = { Text(currentTab.label) },
                    actions = {
                        IconButton(onClick = { showSettings = true }) {
                            AppIcon(R.drawable.ic_menu_2, contentDescription = "Settings")
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
                )
            },
            bottomBar = {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surfaceContainerLowest) {
                    Tab.entries.forEach { tab ->
                        NavigationBarItem(
                            selected = currentTab == tab,
                            onClick = { scope.launch { pagerState.animateScrollToPage(tab.ordinal) } },
                            icon = { AppIcon(tab.icon, contentDescription = null) },
                            label = { Text(tab.label) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = MaterialTheme.colorScheme.primary,
                                selectedTextColor = MaterialTheme.colorScheme.primary,
                                indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                            ),
                        )
                    }
                }
            },
        ) { innerPadding ->
            HorizontalPager(
                state = pagerState,
                modifier = Modifier.padding(innerPadding).fillMaxSize(),
                // On the map, sideways swipes move the map; leave it with the bottom bar.
                userScrollEnabled = currentTab != Tab.MAP,
                // Keep all three screens alive, not only the one showing. Otherwise the map is torn
                // down when it's swiped away and rebuilt (slowly) every time it comes back.
                beyondViewportPageCount = Tab.entries.size - 1,
            ) { page ->
                val screenModifier = Modifier.fillMaxSize()
                when (Tab.entries[page]) {
                    Tab.CONNECT -> ConnectScreen(screenModifier)
                    Tab.DRIVE -> DriveScreen(screenModifier)
                    Tab.MAP -> MapScreen(screenModifier)
                }
            }
        }
        if (showSettings) SettingsScreen(onBack = { showSettings = false })
    }
}

/** Keeps the display awake while [enabled] (Settings → Keep screen on, during a trip). */
@Composable
private fun KeepScreenOn(enabled: Boolean) {
    val view = LocalView.current
    DisposableEffect(enabled) {
        view.keepScreenOn = enabled
        onDispose { view.keepScreenOn = false }
    }
}
