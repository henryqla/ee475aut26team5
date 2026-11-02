package uw.edu.roadqualitymapper.ui.screens

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uw.edu.roadqualitymapper.BuildConfig
import uw.edu.roadqualitymapper.data.Drive
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.FakeDriveLoader
import uw.edu.roadqualitymapper.data.RoadEvent
import uw.edu.roadqualitymapper.data.Segment
import uw.edu.roadqualitymapper.data.downloadDrives
import uw.edu.roadqualitymapper.data.supabase.SupabaseConnection
import uw.edu.roadqualitymapper.data.supabase.SupabaseTripRepository
import uw.edu.roadqualitymapper.data.uploadDrive
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

/** Where the map's drives come from. Release builds always use Supabase. */
private enum class MapSource(val label: String) {
    FAKE_FILE("Fake file"),
    SUPABASE("Supabase"),
}

@Composable
fun MapScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val repository = remember { if (SupabaseConnection.isConfigured) SupabaseTripRepository() else null }

    var source by rememberSaveable { mutableStateOf(if (BuildConfig.DEBUG) MapSource.FAKE_FILE else MapSource.SUPABASE) }
    var reloadCount by remember { mutableIntStateOf(0) }   // bump to load again
    var drives by remember { mutableStateOf(emptyList<Drive>()) }
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }

    // Load the drives whenever the source changes or a reload is asked for.
    LaunchedEffect(source, reloadCount) {
        drives = emptyList()
        when (source) {
            // Stays empty in release builds, which don't contain the fake asset.
            MapSource.FAKE_FILE -> drives = withContext(Dispatchers.IO) { FakeDriveLoader.load(context) }
            MapSource.SUPABASE -> {
                if (repository == null) {
                    status = NOT_CONFIGURED
                    return@LaunchedEffect
                }
                status = "Loading from Supabase…"
                status = try {
                    // Debug builds show fake trips too; release builds only real ones.
                    drives = repository.downloadDrives(includeFake = BuildConfig.DEBUG)
                    "${drives.size} trip(s) from Supabase"
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    "Couldn't load: ${e.message}"
                }
            }
        }
    }

    var mapLoaded by remember { mutableStateOf(false) }
    val cameraPositionState = rememberCameraPositionState {
        position = CameraPosition.fromLatLngZoom(UW_CAMPUS, DEFAULT_ZOOM)
    }

    // Zoom to the drives once both the map and the data are ready
    // (bounds-based camera moves crash if the map hasn't been laid out yet).
    LaunchedEffect(mapLoaded, drives) {
        val points = drives.flatMap { drive -> drive.segments.mapNotNull { it.latLng() } }
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

    Box(modifier) {
        GoogleMap(
            modifier = Modifier.fillMaxSize(),
            cameraPositionState = cameraPositionState,
            onMapLoaded = { mapLoaded = true },
        ) {
            drives.forEach { drive -> DriveOnMap(drive, lineWidthPx) }
        }

        if (BuildConfig.DEBUG) {
            DebugBar(
                source = source,
                onSourceChange = { source = it },
                status = status,
                busy = busy,
                onUploadFakeDrives = {
                    if (repository == null) {
                        status = NOT_CONFIGURED
                        return@DebugBar
                    }
                    scope.launch {
                        busy = true
                        status = "Uploading fake drives…"
                        status = try {
                            val fakeDrives = withContext(Dispatchers.IO) { FakeDriveLoader.load(context) }
                            fakeDrives.forEach { repository.uploadDrive(it) }
                            // Show the result: switch to Supabase, or reload if already there.
                            if (source == MapSource.SUPABASE) reloadCount++ else source = MapSource.SUPABASE
                            "Uploaded ${fakeDrives.size} fake trip(s)"
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            "Upload failed: ${e.message}"
                        } finally {
                            busy = false
                        }
                    }
                },
                modifier = Modifier.align(Alignment.TopCenter).padding(8.dp),
            )
        }
    }
}

private const val NOT_CONFIGURED =
    "Supabase isn't set up: add SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY to local.properties"

/** Debug builds only: pick where the map's data comes from, and push the fake drives to Supabase. */
@Composable
private fun DebugBar(
    source: MapSource,
    onSourceChange: (MapSource) -> Unit,
    status: String,
    busy: Boolean,
    onUploadFakeDrives: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(modifier) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                MapSource.entries.forEach { option ->
                    FilterChip(
                        selected = source == option,
                        onClick = { onSourceChange(option) },
                        label = { Text(option.label) },
                        modifier = Modifier.padding(end = 8.dp),
                    )
                }
                TextButton(onClick = onUploadFakeDrives, enabled = !busy) {
                    Text("Upload fake")
                }
            }
            if (status.isNotEmpty()) {
                Text(status, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

/** One trip: its road line and its event markers, shifted to the side of the road it drove on. */
@Composable
@GoogleMapComposable
private fun DriveOnMap(drive: Drive, lineWidthPx: Float) {
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
