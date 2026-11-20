package uw.edu.roadqualitymapper

import android.graphics.Color
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import uw.edu.roadqualitymapper.ble.DeviceConnection
import uw.edu.roadqualitymapper.data.AppSettings
import uw.edu.roadqualitymapper.data.SelectedVehicle
import uw.edu.roadqualitymapper.data.ThemeChoice
import uw.edu.roadqualitymapper.data.TripUploader
import uw.edu.roadqualitymapper.ui.AppScaffold
import uw.edu.roadqualitymapper.ui.theme.RoadQualityMapperTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SelectedVehicle.load(this)
        AppSettings.load(this)
        DeviceConnection.init(this)
        // Send anything left over from an earlier trip (no signal, or the app was closed).
        lifecycleScope.launch { TripUploader.uploadPending(applicationContext) }
        enableEdgeToEdge()
        setContent {
            val theme by AppSettings.theme.collectAsState()
            val dark = when (theme) {
                ThemeChoice.LIGHT -> false
                ThemeChoice.DARK -> true
                null -> isSystemInDarkTheme()
            }
            // Status bar and navigation bar icons match the app's theme, not the phone's.
            DisposableEffect(dark) {
                enableEdgeToEdge(
                    statusBarStyle = SystemBarStyle.auto(Color.TRANSPARENT, Color.TRANSPARENT) { dark },
                    navigationBarStyle = SystemBarStyle.auto(LIGHT_NAV_SCRIM, DARK_NAV_SCRIM) { dark },
                )
                onDispose {}
            }
            RoadQualityMapperTheme(darkTheme = dark) {
                AppScaffold()
            }
        }
    }
}

// The same see-through navigation bar backgrounds enableEdgeToEdge() uses by default.
private val LIGHT_NAV_SCRIM = Color.argb(0xE6, 0xFF, 0xFF, 0xFF)
private val DARK_NAV_SCRIM = Color.argb(0x80, 0x1B, 0x1B, 0x1B)
