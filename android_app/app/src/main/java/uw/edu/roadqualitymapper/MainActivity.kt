package uw.edu.roadqualitymapper

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import uw.edu.roadqualitymapper.data.SelectedVehicle
import uw.edu.roadqualitymapper.ui.AppScaffold
import uw.edu.roadqualitymapper.ui.theme.RoadQualityMapperTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SelectedVehicle.load(this)
        enableEdgeToEdge()
        setContent {
            RoadQualityMapperTheme {
                AppScaffold()
            }
        }
    }
}