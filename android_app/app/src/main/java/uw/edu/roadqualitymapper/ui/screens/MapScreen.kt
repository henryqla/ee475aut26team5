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
import androidx.compose.runtime.snapshotFlow
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
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uw.edu.roadqualitymapper.BuildConfig
import uw.edu.roadqualitymapper.data.BoundingBox
import uw.edu.roadqualitymapper.data.Drive
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.EventSpot
import uw.edu.roadqualitymapper.data.FakeDriveLoader
import uw.edu.roadqualitymapper.data.RoadEvent
import uw.edu.roadqualitymapper.data.RoadPiece
import uw.edu.roadqualitymapper.data.Segment
import uw.edu.roadqualitymapper.data.downloadDrives
import uw.edu.roadqualitymapper.data.supabase.SupabaseConnection
import uw.edu.roadqualitymapper.data.supabase.SupabaseTripRepository
import uw.edu.roadqualitymapper.data.uploadDrive
import uw.edu.roadqualitymapper.ui.headingDirection
import uw.edu.roadqualitymapper.ui.metersToPixels
import uw.edu.roadqualitymapper.ui.moveAlong
import uw.edu.roadqualitymapper.ui.roughnessColor
import uw.edu.roadqualitymapper.ui.shiftPathRight
import uw.edu.roadqualitymapper.ui.shiftRight
import uw.edu.roadqualitymapper.ui.travelDirectionAt
import kotlin.math.abs
import kotlin.math.max

private val UW_CAMPUS = LatLng(47.6553, -122.3035)
private const val DEFAULT_ZOOM = 14f
private const val DRIVE_BOUNDS_PADDING_PX = 120

/**
 * Zoomed out further than this, the screen covers too much road to load. At zoom 13 a phone
 * screen shows about 5 × 10 km.
 */
private const val MIN_ROAD_QUALITY_ZOOM = 13f

/** Line width on the ground, about one direction's lanes on a city street. */
private const val ROAD_LINE_WIDTH_M = 5.0

/** So the line stays visible when zoomed out, where 5 m would be less than a pixel. */
private const val MIN_ROAD_LINE_WIDTH_DP = 4f

/**
 * How far each direction is drawn to the right of the road's centerline. With 5 m lines this
 * leaves a ~1 m gap between the two directions when zoomed in; zoomed out they overlap.
 */
private const val DIRECTION_OFFSET_M = 3.0

/** What the map shows. Release builds always show Road quality. */
private enum class MapView(val label: String) {
    ROAD_QUALITY("Road quality"),   // every drive combined, from Ben's map functions
    TRIPS("Trips"),                 // each trip drawn on its own, from Supabase
    FAKE_FILE("Fake file"),         // the trips in the debug-only fake_drive.json
}

@Composable
fun MapScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val repository = remember { if (SupabaseConnection.isConfigured) SupabaseTripRepository() else null }

    var view by rememberSaveable { mutableStateOf(MapView.ROAD_QUALITY) }
    var reloadCount by remember { mutableIntStateOf(0) }   // bump to load again
    var drives by remember { mutableStateOf(emptyList<Drive>()) }           // Trips and Fake file
    var pieces by remember { mutableStateOf(emptyList<RoadPiece>()) }       // Road quality
    var spots by remember { mutableStateOf(emptyList<EventSpot>()) }        // Road quality
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }

    var mapLoaded by remember { mutableStateOf(false) }
    val cameraPositionState = rememberCameraPositionState {
        position = CameraPosition.fromLatLngZoom(UW_CAMPUS, DEFAULT_ZOOM)
    }

    // Trips and Fake file: load whole trips when the view changes or a reload is asked for.
    LaunchedEffect(view, reloadCount) {
        drives = emptyList()
        when (view) {
            MapView.ROAD_QUALITY -> {}   // loaded for the visible area, below
            // Stays empty in release builds, which don't contain the fake asset.
            MapView.FAKE_FILE -> {
                drives = withContext(Dispatchers.IO) { FakeDriveLoader.load(context) }
                status = "${drives.size} trip(s) from the fake file"
            }
            MapView.TRIPS -> {
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

    // Road quality: load what's on screen each time the map stops moving. collectLatest drops a
    // load that's still running when the map moves again, so only the newest area is shown.
    LaunchedEffect(view, reloadCount, mapLoaded) {
        pieces = emptyList()
        spots = emptyList()
        if (view != MapView.ROAD_QUALITY || !mapLoaded) return@LaunchedEffect
        if (repository == null) {
            status = NOT_CONFIGURED
            return@LaunchedEffect
        }
        snapshotFlow { cameraPositionState.isMoving }.collectLatest { moving ->
            if (moving) return@collectLatest
            if (cameraPositionState.position.zoom < MIN_ROAD_QUALITY_ZOOM) {
                pieces = emptyList()
                spots = emptyList()
                status = "Zoom in to see road quality"
                return@collectLatest
            }
            val area = cameraPositionState.projection?.visibleRegion?.latLngBounds?.toBoundingBox()
                ?: return@collectLatest
            status = try {
                // Debug builds count fake trips too; release builds only real ones.
                val newPieces = repository.roadQualityInArea(area, includeFake = BuildConfig.DEBUG)
                val newSpots = repository.eventsInArea(area, includeFake = BuildConfig.DEBUG)
                pieces = newPieces
                spots = newSpots
                when {
                    newPieces.isEmpty() && newSpots.isEmpty() -> "No drives recorded here yet"
                    BuildConfig.DEBUG -> "${newPieces.size} road pieces, ${newSpots.size} bump spots"
                    else -> ""
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                "Couldn't load: ${e.message}"
            }
        }
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
            pieces.forEach { piece ->
                RoadPieceOnMap(piece, lineWidthPx, onClick = {
                    status = "Roughness %.3f g, from %d drive(s)".format(piece.roughness, piece.drives)
                })
            }
            spots.forEach { EventSpotMarker(it) }
            drives.forEach { drive -> DriveOnMap(drive, lineWidthPx) }
        }

        val topCenter = Modifier.align(Alignment.TopCenter).padding(8.dp)
        if (BuildConfig.DEBUG) {
            DebugBar(
                view = view,
                onViewChange = { view = it },
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
                            // Show the result: reload a Supabase view, or switch to one.
                            if (view == MapView.FAKE_FILE) view = MapView.ROAD_QUALITY else reloadCount++
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
                modifier = topCenter,
            )
        } else if (status.isNotEmpty()) {
            Card(topCenter) {
                Text(status, Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
            }
        }
    }
}

private const val NOT_CONFIGURED =
    "Supabase isn't set up: add SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY to local.properties"

private fun LatLngBounds.toBoundingBox() = BoundingBox(
    south = southwest.latitude,
    west = southwest.longitude,
    north = northeast.latitude,
    east = northeast.longitude,
)

/** Debug builds only: pick what the map shows, and push the fake drives to Supabase. */
@Composable
private fun DebugBar(
    view: MapView,
    onViewChange: (MapView) -> Unit,
    status: String,
    busy: Boolean,
    onUploadFakeDrives: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(modifier) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) {
            Row {
                MapView.entries.forEach { option ->
                    FilterChip(
                        selected = view == option,
                        onClick = { onViewChange(option) },
                        label = { Text(option.label) },
                        modifier = Modifier.padding(end = 8.dp),
                    )
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(status, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                TextButton(onClick = onUploadFakeDrives, enabled = !busy) {
                    Text("Upload fake")
                }
            }
        }
    }
}

/**
 * Road quality view: one piece of road as a short line along its direction of travel, on that
 * direction's side of the road. Solid colors, so it also works on the older map renderer.
 */
@Composable
@GoogleMapComposable
private fun RoadPieceOnMap(piece: RoadPiece, lineWidthPx: Float, onClick: () -> Unit) {
    val points = remember(piece) {
        val direction = headingDirection(piece.headingDeg)
        val center = shiftRight(LatLng(piece.lat, piece.lon), direction, DIRECTION_OFFSET_M)
        listOf(
            moveAlong(center, direction, -piece.lengthM / 2),
            moveAlong(center, direction, piece.lengthM / 2),
        )
    }
    Polyline(
        points = points,
        color = roughnessColor(piece.roughness),
        width = lineWidthPx,
        // Round ends overlap the next piece a little, so the road looks continuous.
        startCap = RoundCap(),
        endCap = RoundCap(),
        clickable = true,
        onClick = { onClick() },
    )
}

/** Road quality view: a pothole or speed bump spot, merged over every drive that hit it. */
@Composable
@GoogleMapComposable
private fun EventSpotMarker(spot: EventSpot) {
    val position = remember(spot) {
        val point = LatLng(spot.lat, spot.lon)
        // On its direction's side of the road, like the road pieces. No heading: leave it centered.
        spot.headingDeg?.let { shiftRight(point, headingDirection(it), DIRECTION_OFFSET_M) } ?: point
    }
    EventMarker(
        eventClass = spot.eventClass,
        position = position,
        snippet = "Hit by %d of %d drives · peak %.1f g".format(spot.hits, spot.passes, spot.maxPeakZ),
    )
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
        EventMarker(
            // A user label wins, except UNKNOWN, which means "not sure" (contract §7).
            eventClass = event.userLabel?.takeIf { it != EventClass.UNKNOWN } ?: event.eventClass,
            position = position,
            snippet = "Peak %.1f g, confidence %d%%".format(event.peakZ, (event.conf * 100).toInt()),
        )
    }
}

private fun Segment.latLng(): LatLng? = if (lat != null && lon != null) LatLng(lat, lon) else null

private fun RoadEvent.latLng(): LatLng? = if (lat != null && lon != null) LatLng(lat, lon) else null

/** Violet = pothole, blue = speed bump, so markers don't blend into the green/red roads. */
@Composable
@GoogleMapComposable
private fun EventMarker(eventClass: EventClass, position: LatLng, snippet: String) {
    val (title, hue) = when (eventClass) {
        EventClass.POTHOLE -> "Pothole" to BitmapDescriptorFactory.HUE_VIOLET
        EventClass.SPEED_BUMP -> "Speed bump" to BitmapDescriptorFactory.HUE_AZURE
        EventClass.UNKNOWN -> "Bump" to BitmapDescriptorFactory.HUE_YELLOW
    }
    Marker(
        state = rememberUpdatedMarkerState(position = position),
        title = title,
        snippet = snippet,
        icon = BitmapDescriptorFactory.defaultMarker(hue),
    )
}
