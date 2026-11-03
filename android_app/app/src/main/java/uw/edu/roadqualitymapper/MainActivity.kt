package uw.edu.roadqualitymapper

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import uw.edu.roadqualitymapper.ble.DeviceConnection
import uw.edu.roadqualitymapper.data.SelectedVehicle
import uw.edu.roadqualitymapper.data.TripUploader
import uw.edu.roadqualitymapper.ui.AppScaffold
import uw.edu.roadqualitymapper.ui.theme.RoadQualityMapperTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SelectedVehicle.load(this)
        DeviceConnection.init(this)
        // Send anything left over from an earlier trip (no signal, or the app was closed).
        lifecycleScope.launch { TripUploader.uploadPending(applicationContext) }
        enableEdgeToEdge()
        setContent {
            RoadQualityMapperTheme {
                AppScaffold()
            }
        }
    }
}