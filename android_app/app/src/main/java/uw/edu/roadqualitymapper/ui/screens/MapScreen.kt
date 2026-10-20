package uw.edu.roadqualitymapper.ui.screens

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.google.android.gms.maps.model.CameraPosition
import com.google.android.gms.maps.model.LatLng
import com.google.maps.android.compose.GoogleMap
import com.google.maps.android.compose.rememberCameraPositionState

private val UW_CAMPUS = LatLng(47.6553, -122.3035)
private const val DEFAULT_ZOOM = 14f

@Composable
fun MapScreen(modifier: Modifier = Modifier) {
    val cameraPositionState = rememberCameraPositionState {
        position = CameraPosition.fromLatLngZoom(UW_CAMPUS, DEFAULT_ZOOM)
    }
    GoogleMap(
        modifier = modifier,
        cameraPositionState = cameraPositionState,
    )
}
