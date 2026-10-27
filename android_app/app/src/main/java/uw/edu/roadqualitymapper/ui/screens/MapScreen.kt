package uw.edu.roadqualitymapper.ui.screens

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import com.google.android.gms.maps.CameraUpdateFactory
import com.google.android.gms.maps.model.BitmapDescriptorFactory
import com.google.android.gms.maps.model.CameraPosition
import com.google.android.gms.maps.model.JointType
import com.google.android.gms.maps.model.LatLng
import com.google.android.gms.maps.model.LatLngBounds
import com.google.android.gms.maps.model.RoundCap
import com.google.android.gms.maps.model.StrokeStyle
import com.google.android.gms.maps.model.StyleSpan
import com.google.maps.android.compose.GoogleMap
import com.google.maps.android.compose.GoogleMapComposable
import com.google.maps.android.compose.Marker
import com.google.maps.android.compose.Polyline
import com.google.maps.android.compose.rememberCameraPositionState
import com.google.maps.android.compose.rememberUpdatedMarkerState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.FakeDrive
import uw.edu.roadqualitymapper.data.FakeDriveLoader
import uw.edu.roadqualitymapper.data.RoadEvent
import uw.edu.roadqualitymapper.data.Segment
import uw.edu.roadqualitymapper.ui.roughnessColor

private val UW_CAMPUS = LatLng(47.6553, -122.3035)
private const val DEFAULT_ZOOM = 14f
private const val ROAD_LINE_WIDTH_PX = 16f
private const val DRIVE_BOUNDS_PADDING_PX = 120

@Composable
fun MapScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    // Read off the main thread. Stays null in release builds, which don't contain the fake asset.
    val fakeDrive by produceState<FakeDrive?>(initialValue = null) {
        value = withContext(Dispatchers.IO) { FakeDriveLoader.load(context) }
    }
    var mapLoaded by remember { mutableStateOf(false) }
    val cameraPositionState = rememberCameraPositionState {
        position = CameraPosition.fromLatLngZoom(UW_CAMPUS, DEFAULT_ZOOM)
    }

    // Zoom to the drive once both the map and the data are ready
    // (bounds-based camera moves crash if the map hasn't been laid out yet).
    LaunchedEffect(mapLoaded, fakeDrive) {
        val segments = fakeDrive?.segments.orEmpty()
        if (!mapLoaded || segments.isEmpty()) return@LaunchedEffect
        val bounds = LatLngBounds.builder()
            .apply { segments.forEach { include(LatLng(it.lat, it.lon)) } }
            .build()
        cameraPositionState.animate(CameraUpdateFactory.newLatLngBounds(bounds, DRIVE_BOUNDS_PADDING_PX))
    }

    GoogleMap(
        modifier = modifier,
        cameraPositionState = cameraPositionState,
        onMapLoaded = { mapLoaded = true },
    ) {
        fakeDrive?.let { drive ->
            RoadSegments(drive.segments)
            drive.events.forEach { EventMarker(it) }
        }
    }
}

/**
 * The whole drive as one line. Each piece between two points fades from the first point's
 * color to the second's, so the color changes smoothly along the road.
 */
@Composable
@GoogleMapComposable
private fun RoadSegments(segments: List<Segment>) {
    if (segments.size < 2) return
    val colors = segments.map { roughnessColor(it.roughness).toArgb() }
    // One span per piece; a span covers one piece by default.
    val spans = (1 until segments.size).map { i ->
        StyleSpan(StrokeStyle.gradientBuilder(colors[i - 1], colors[i]).build())
    }
    Polyline(
        points = segments.map { LatLng(it.lat, it.lon) },
        spans = spans,
        width = ROAD_LINE_WIDTH_PX,
        startCap = RoundCap(),
        endCap = RoundCap(),
        jointType = JointType.ROUND,
    )
}

/** Violet = pothole, blue = speed bump, so markers don't blend into the green/red roads. */
@Composable
@GoogleMapComposable
private fun EventMarker(event: RoadEvent) {
    val (title, hue) = when (event.userLabel ?: event.eventClass) {
        EventClass.POTHOLE -> "Pothole" to BitmapDescriptorFactory.HUE_VIOLET
        EventClass.SPEED_BUMP -> "Speed bump" to BitmapDescriptorFactory.HUE_AZURE
        EventClass.UNKNOWN -> "Bump" to BitmapDescriptorFactory.HUE_YELLOW
    }
    Marker(
        state = rememberUpdatedMarkerState(position = LatLng(event.lat, event.lon)),
        title = title,
        snippet = "Peak %.1f g, confidence %d%%".format(event.peakG, (event.confidence * 100).toInt()),
        icon = BitmapDescriptorFactory.defaultMarker(hue),
    )
}
