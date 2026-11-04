package uw.edu.roadqualitymapper.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.IntSize
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
import com.google.maps.android.compose.MapUiSettings
import com.google.maps.android.compose.Marker
import com.google.maps.android.compose.Polyline
import com.google.maps.android.compose.rememberCameraPositionState
import com.google.maps.android.compose.rememberUpdatedMarkerState
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import uw.edu.roadqualitymapper.BuildConfig
import uw.edu.roadqualitymapper.data.BoundingBox
import uw.edu.roadqualitymapper.data.Drive
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.EventSpot
import uw.edu.roadqualitymapper.data.RoadEvent
import uw.edu.roadqualitymapper.data.RoadPiece
import uw.edu.roadqualitymapper.data.Segment
import uw.edu.roadqualitymapper.data.downloadDrives
import uw.edu.roadqualitymapper.data.shortMessage
import uw.edu.roadqualitymapper.data.supabase.SupabaseConnection
import uw.edu.roadqualitymapper.data.supabase.SupabaseTripRepository
import uw.edu.roadqualitymapper.ui.DrawnPiece
import uw.edu.roadqualitymapper.ui.boxAround
import uw.edu.roadqualitymapper.ui.layOutPieces
import uw.edu.roadqualitymapper.ui.headingDirection
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

/**
 * Zoomed out further than this, the screen covers too much road to load. At zoom 13 a phone
 * screen shows about 5 × 10 km.
 */
private const val MIN_ROAD_QUALITY_ZOOM = 13f

/**
 * Road quality is loaded for more than the screen shows: twice as far from the center as the
 * screen's edge, and at least this far. Panning and zooming within that need no new load.
 */
private const val MIN_LOAD_RADIUS_M = 1_000.0
private const val LOAD_RADIUS_PER_SCREEN = 2.0

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
}

/**
 * The last road quality load, kept while the app is open so switching tabs shows it again at once
 * instead of loading from scratch.
 */
private object RoadQualityCache {
    var area: BoundingBox? = null
    var pieces = emptyList<RoadPiece>()
    var spots = emptyList<EventSpot>()
}

@Composable
fun MapScreen(modifier: Modifier = Modifier) {
    val scope = rememberCoroutineScope()
    val repository = remember { if (SupabaseConnection.isConfigured) SupabaseTripRepository() else null }

    var view by rememberSaveable { mutableStateOf(MapView.ROAD_QUALITY) }
    var drives by remember { mutableStateOf(emptyList<Drive>()) }                     // Trips
    var pieces by remember { mutableStateOf(RoadQualityCache.pieces) }                // Road quality
    var spots by remember { mutableStateOf(RoadQualityCache.spots) }                  // Road quality
    var status by remember { mutableStateOf("") }

    var mapLoaded by remember { mutableStateOf(false) }
    var mapSize by remember { mutableStateOf(IntSize.Zero) }
    val cameraPositionState = rememberCameraPositionState {
        position = CameraPosition.fromLatLngZoom(UW_CAMPUS, DEFAULT_ZOOM)
    }
    val density = LocalDensity.current.density

    // Trips: load whole trips when the view is picked.
    LaunchedEffect(view) {
        drives = emptyList()
        if (view != MapView.TRIPS) return@LaunchedEffect
        if (repository == null) {
            status = NOT_CONFIGURED
            return@LaunchedEffect
        }
        status = "Loading…"
        status = try {
            // Debug builds show fake trips too; release builds only real ones.
            drives = repository.downloadDrives(includeFake = BuildConfig.DEBUG)
            "${drives.size} trip(s)"
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            "Couldn't load: ${e.shortMessage()}"
        }
    }

    // Road quality: each time the map stops moving, load the area around it, unless the last load
    // already covers the screen. Starts before the map has finished drawing, so both happen at once.
    // collectLatest drops a load that's still running when the map moves again.
    LaunchedEffect(view, mapSize) {
        if (view != MapView.ROAD_QUALITY || mapSize == IntSize.Zero) return@LaunchedEffect
        if (repository == null) {
            status = NOT_CONFIGURED
            return@LaunchedEffect
        }
        snapshotFlow { cameraPositionState.isMoving }.collectLatest { moving ->
            if (moving) return@collectLatest
            val camera = cameraPositionState.position
            if (camera.zoom < MIN_ROAD_QUALITY_ZOOM) {
                status = "Zoom in to see roads"
                return@collectLatest
            }
            val screenRadiusM = max(mapSize.width, mapSize.height) / 2.0 /
                metersToPixels(1.0, camera.zoom, camera.target.latitude, density)
            val loaded = RoadQualityCache.area
            if (loaded != null && loaded.contains(boxAround(camera.target, screenRadiusM))) {
                status = roadQualitySummary(pieces, spots)
                return@collectLatest
            }

            val area = boxAround(camera.target, max(screenRadiusM * LOAD_RADIUS_PER_SCREEN, MIN_LOAD_RADIUS_M))
            if (pieces.isEmpty() && spots.isEmpty()) status = "Loading…"
            status = try {
                // Both requests at once. Debug builds count fake trips too; release builds only real ones.
                val (newPieces, newSpots) = coroutineScope {
                    val p = async { repository.roadQualityInArea(area, includeFake = BuildConfig.DEBUG) }
                    val s = async { repository.eventsInArea(area, includeFake = BuildConfig.DEBUG) }
                    p.await() to s.await()
                }
                pieces = newPieces
                spots = newSpots
                RoadQualityCache.area = area
                RoadQualityCache.pieces = newPieces
                RoadQualityCache.spots = newSpots
                roadQualitySummary(newPieces, newSpots)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                "Couldn't load: ${e.shortMessage()}"
            }
        }
    }

    // Trips: zoom to the drives once both the map and the data are ready
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
    val camera = cameraPositionState.position
    val lineWidthPx = max(
        metersToPixels(ROAD_LINE_WIDTH_M, camera.zoom, camera.target.latitude, density),
        MIN_ROAD_LINE_WIDTH_DP * density,
    )
    // Which pieces meet along a road, worked out once per load, so colors can fade between them.
    val drawnPieces = remember(pieces) { layOutPieces(pieces, DIRECTION_OFFSET_M) }
    Box(modifier.onSizeChanged { mapSize = it }) {
        GoogleMap(
            modifier = Modifier.fillMaxSize(),
            cameraPositionState = cameraPositionState,
            // Google's compass can only sit in the top-left; ours (below) sits above the zoom buttons.
            uiSettings = MapUiSettings(compassEnabled = false),
            onMapLoaded = { mapLoaded = true },
        ) {
            if (view == MapView.ROAD_QUALITY && camera.zoom >= MIN_ROAD_QUALITY_ZOOM) {
                drawnPieces.forEach { drawn ->
                    RoadPieceOnMap(drawn, lineWidthPx, onClick = {
                        status = "%.3f g · %d drive(s)".format(drawn.piece.roughness, drawn.piece.drives)
                    })
                }
                spots.forEach { EventSpotMarker(it) }
            }
            if (view == MapView.TRIPS) drives.forEach { drive -> DriveOnMap(drive, lineWidthPx) }
        }

        NorthButton(
            bearing = camera.bearing,
            onClick = {
                scope.launch {
                    val northUp = CameraPosition.Builder(cameraPositionState.position).bearing(0f).build()
                    cameraPositionState.animate(CameraUpdateFactory.newCameraPosition(northUp))
                }
            },
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(end = NORTH_BUTTON_END_PADDING, bottom = NORTH_BUTTON_BOTTOM_PADDING),
        )

        val topCenter = Modifier.align(Alignment.TopCenter).padding(8.dp)
        if (BuildConfig.DEBUG) {
            DebugBar(view = view, onViewChange = { view = it }, status = status, modifier = topCenter)
        } else if (status.isNotEmpty()) {
            Card(topCenter) {
                Text(status, Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
            }
        }
    }
}

// Lines the north button up above Google's +/− zoom buttons in the bottom-right corner.
private val NORTH_BUTTON_END_PADDING = 12.dp
private val NORTH_BUTTON_BOTTOM_PADDING = 112.dp
private val NORTH_BUTTON_SIZE = 40.dp

/**
 * Turns the map back to north up. The needle turns with the map so it always points north, like
 * the compass in Google Maps; red is the north end.
 */
@Composable
private fun NorthButton(bearing: Float, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Surface(
        onClick = onClick,
        shape = CircleShape,
        color = Color.White,
        shadowElevation = 3.dp,
        modifier = modifier.size(NORTH_BUTTON_SIZE),
    ) {
        Canvas(
            Modifier
                .padding(8.dp)
                .rotate(-bearing)   // the map turned clockwise by `bearing`, so north is that far counterclockwise
                .semantics { contentDescription = "Turn map to north" }
        ) {
            val midX = size.width / 2
            val midY = size.height / 2
            val halfWidth = size.width * 0.22f
            drawPath(
                Path().apply {
                    moveTo(midX, 0f)
                    lineTo(midX + halfWidth, midY)
                    lineTo(midX - halfWidth, midY)
                    close()
                },
                color = Color(0xFFE53935),
            )
            drawPath(
                Path().apply {
                    moveTo(midX, size.height)
                    lineTo(midX + halfWidth, midY)
                    lineTo(midX - halfWidth, midY)
                    close()
                },
                color = Color(0xFF9E9E9E),
            )
        }
    }
}

private const val NOT_CONFIGURED = "Server not set up"

private fun roadQualitySummary(pieces: List<RoadPiece>, spots: List<EventSpot>): String = when {
    pieces.isEmpty() && spots.isEmpty() -> "No drives here yet"
    BuildConfig.DEBUG -> "${pieces.size} road pieces, ${spots.size} bumps"
    else -> ""
}

private fun BoundingBox.contains(other: BoundingBox) =
    other.south >= south && other.north <= north && other.west >= west && other.east <= east

/** Debug builds only: pick what the map shows. */
@Composable
private fun DebugBar(
    view: MapView,
    onViewChange: (MapView) -> Unit,
    status: String,
    modifier: Modifier = Modifier,
) {
    Card(modifier) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                MapView.entries.forEach { option ->
                    FilterChip(
                        selected = view == option,
                        onClick = { onViewChange(option) },
                        label = { Text(option.label) },
                        modifier = Modifier.padding(end = 8.dp),
                    )
                }
            }
            if (status.isNotEmpty()) {
                Text(status, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

/**
 * Road quality view: one piece of road as a short line along its direction of travel, on that
 * direction's side of the road. Its color fades from the in-between color where it meets the
 * previous piece, to its own color in the middle, to the in-between color with the next piece,
 * like the Trips lines. (Gradients need Google's newer map renderer, as Trips does.)
 */
@Composable
@GoogleMapComposable
private fun RoadPieceOnMap(drawn: DrawnPiece, lineWidthPx: Float, onClick: () -> Unit) {
    val spans = remember(drawn) {
        val startColor = roughnessColor(drawn.startRoughness).toArgb()
        val middleColor = roughnessColor(drawn.piece.roughness).toArgb()
        val endColor = roughnessColor(drawn.endRoughness).toArgb()
        listOf(
            StyleSpan(StrokeStyle.gradientBuilder(startColor, middleColor).build()),
            StyleSpan(StrokeStyle.gradientBuilder(middleColor, endColor).build()),
        )
    }
    Polyline(
        points = listOf(drawn.start, drawn.middle, drawn.end),
        spans = spans,
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
        snippet = "Hit by %d of %d drives · %.1f g".format(spot.hits, spot.passes, spot.maxPeakZ),
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
            snippet = "%.1f g · %d%% sure".format(event.peakZ, (event.conf * 100).toInt()),
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
