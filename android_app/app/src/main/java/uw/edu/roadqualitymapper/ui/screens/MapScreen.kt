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
import androidx.compose.ui.platform.LocalDensity
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
import uw.edu.roadqualitymapper.ui.metersToPixels
import uw.edu.roadqualitymapper.ui.roughnessColor
import uw.edu.roadqualitymapper.ui.shiftPathRight
import uw.edu.roadqualitymapper.ui.shiftRight
import uw.edu.roadqualitymapper.ui.travelDirectionAt
import kotlin.math.abs
import kotlin.math.max

private val UW_CAMPUS = LatLng(47.6553, -122.3035)
private const val DEFAULT_ZOOM = 14f
private const val DRIVE_BOUNDS_PADDING_PX = 120

/** Line width on the ground, about one direction's lanes on a city street. */
private const val ROAD_LINE_WIDTH_M = 5.0

/** So the line stays visible when zoomed out, where 5 m would be less than a pixel. */
private const val MIN_ROAD_LINE_WIDTH_DP = 4f

/**
 * How far each direction is drawn to the right of the road's centerline. With 5 m lines this
 * leaves a ~1 m gap between the two directions when zoomed in; zoomed out they overlap.
 */
private const val DIRECTION_OFFSET_M = 3.0

@Composable
fun MapScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    // Read off the main thread. Stays empty in release builds, which don't contain the fake asset.
    val fakeDrives by produceState(initialValue = emptyList<FakeDrive>()) {
        value = withContext(Dispatchers.IO) { FakeDriveLoader.load(context) }
    }
    var mapLoaded by remember { mutableStateOf(false) }
    val cameraPositionState = rememberCameraPositionState {
        position = CameraPosition.fromLatLngZoom(UW_CAMPUS, DEFAULT_ZOOM)
    }

    // Zoom to the drives once both the map and the data are ready
    // (bounds-based camera moves crash if the map hasn't been laid out yet).
    LaunchedEffect(mapLoaded, fakeDrives) {
        val points = fakeDrives.flatMap { drive -> drive.segments.mapNotNull { it.latLng() } }
        if (!mapLoaded || points.isEmpty()) return@LaunchedEffect
        val bounds = LatLngBounds.builder()
            .apply { points.forEach { include(it) } }
            .build()
        cameraPositionState.animate(CameraUpdateFactory.newLatLngBounds(bounds, DRIVE_BOUNDS_PADDING_PX))
    }

    // Reading the camera here redraws the lines as you zoom, so they keep a fixed width on the ground.
    val density = LocalDensity.current.density
    val camera = cameraPositionState.position
    val lineWidthPx = max(
        metersToPixels(ROAD_LINE_WIDTH_M, camera.zoom, camera.target.latitude, density),
        MIN_ROAD_LINE_WIDTH_DP * density,
    )

    GoogleMap(
        modifier = modifier,
        cameraPositionState = cameraPositionState,
        onMapLoaded = { mapLoaded = true },
    ) {
        fakeDrives.forEach { drive -> DriveOnMap(drive, lineWidthPx) }
    }
}

/** One trip: its road line and its event markers, shifted to the side of the road it drove on. */
@Composable
@GoogleMapComposable
private fun DriveOnMap(drive: FakeDrive, lineWidthPx: Float) {
    // Segments without a GPS fix can't be drawn; skip them.
    val located = remember(drive) { drive.segments.filter { it.latLng() != null } }
    val path = remember(located) { located.mapNotNull { it.latLng() } }
    if (path.size < 2) return
    val shiftedPath = remember(path) { shiftPathRight(path, DIRECTION_OFFSET_M) }
    // Each piece between two points fades from the first point's color to the second's.
    // A span covers one piece by default, so there's one span per piece.
    val spans = remember(located) {
        val colors = located.map { roughnessColor(it.rmsZ).toArgb() }
        (1 until colors.size).map { i ->
            StyleSpan(StrokeStyle.gradientBuilder(colors[i - 1], colors[i]).build())
        }
    }
    Polyline(
        points = shiftedPath,
        spans = spans,
        width = lineWidthPx,
        startCap = RoundCap(),
        endCap = RoundCap(),
        jointType = JointType.ROUND,
    )

    // Events without a GPS fix can't be placed; skip them.
    drive.events.filter { it.latLng() != null }.forEach { event ->
        // Shift the marker the same way as the road under it, using the direction of travel
        // at the segment closest in time to the event.
        val position = remember(event, path) {
            val nearest = located.indices.minBy { abs(located[it].tMid - event.t) }
            shiftRight(event.latLng()!!, travelDirectionAt(path, nearest), DIRECTION_OFFSET_M)
        }
        EventMarker(event, position)
    }
}

private fun Segment.latLng(): LatLng? = if (lat != null && lon != null) LatLng(lat, lon) else null

private fun RoadEvent.latLng(): LatLng? = if (lat != null && lon != null) LatLng(lat, lon) else null

/** Violet = pothole, blue = speed bump, so markers don't blend into the green/red roads. */
@Composable
@GoogleMapComposable
private fun EventMarker(event: RoadEvent, position: LatLng) {
    val (title, hue) = when (event.userLabel ?: event.eventClass) {
        EventClass.POTHOLE -> "Pothole" to BitmapDescriptorFactory.HUE_VIOLET
        EventClass.SPEED_BUMP -> "Speed bump" to BitmapDescriptorFactory.HUE_AZURE
        EventClass.UNKNOWN -> "Bump" to BitmapDescriptorFactory.HUE_YELLOW
    }
    Marker(
        state = rememberUpdatedMarkerState(position = position),
        title = title,
        snippet = "Peak %.1f g, confidence %d%%".format(event.peakZ, (event.conf * 100).toInt()),
        icon = BitmapDescriptorFactory.defaultMarker(hue),
    )
}
