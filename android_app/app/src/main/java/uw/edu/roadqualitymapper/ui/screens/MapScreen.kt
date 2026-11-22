package uw.edu.roadqualitymapper.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.graphicsLayer
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
import com.google.maps.android.compose.ComposeMapColorScheme
import com.google.maps.android.compose.GoogleMap
import com.google.maps.android.compose.GoogleMapComposable
import com.google.maps.android.compose.MapUiSettings
import com.google.maps.android.compose.Marker
import com.google.maps.android.compose.Polyline
import com.google.maps.android.compose.rememberCameraPositionState
import com.google.maps.android.compose.rememberUpdatedMarkerState
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import uw.edu.roadqualitymapper.BuildConfig
import uw.edu.roadqualitymapper.data.AppSettings
import uw.edu.roadqualitymapper.data.ColorMode
import uw.edu.roadqualitymapper.data.Drive
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.EventSpot
import uw.edu.roadqualitymapper.data.RoadEvent
import uw.edu.roadqualitymapper.data.RoadPiece
import uw.edu.roadqualitymapper.data.RoadPieceKind
import uw.edu.roadqualitymapper.data.Segment
import uw.edu.roadqualitymapper.data.downloadDrives
import uw.edu.roadqualitymapper.data.shortMessage
import uw.edu.roadqualitymapper.data.supabase.SupabaseConnection
import uw.edu.roadqualitymapper.data.supabase.SupabaseTripRepository
import uw.edu.roadqualitymapper.ui.RoadQualityTiles
import uw.edu.roadqualitymapper.ui.RoadTile
import uw.edu.roadqualitymapper.ui.RoughnessScale
import uw.edu.roadqualitymapper.ui.TileData
import uw.edu.roadqualitymapper.ui.boxAround
import uw.edu.roadqualitymapper.ui.headingDirection
import uw.edu.roadqualitymapper.ui.metersToPixels
import uw.edu.roadqualitymapper.ui.eventMarkerHue
import uw.edu.roadqualitymapper.ui.roughnessColor
import uw.edu.roadqualitymapper.ui.shiftPathRight
import uw.edu.roadqualitymapper.ui.shiftRight
import uw.edu.roadqualitymapper.ui.tilesCovering
import uw.edu.roadqualitymapper.ui.zoomBand
import uw.edu.roadqualitymapper.ui.theme.LocalDarkTheme
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.roundToInt

private val UW_CAMPUS = LatLng(47.6553, -122.3035)
private const val DEFAULT_ZOOM = 14f
private const val DRIVE_BOUNDS_PADDING_PX = 120

/**
 * Road lines are shown at every zoom, so you can always see where there's data. Pothole and
 * speed bump pins only from this zoom in: further out they'd pile on top of each other. At zoom
 * 13 a phone screen shows about 5 × 10 km.
 */
private const val MIN_EVENT_MARKER_ZOOM = 13f

/**
 * The map only shows trips that started on or after this (epoch ms): 19 Nov 2026, Seattle time,
 * the first drive with the board firmly mounted. Older drives stay in the database.
 */
private const val MAP_DRIVES_SINCE_MS = 1_795_075_200_000L

/** Tiles loading at once (see RoadQualityTiles.kt). The rest wait their turn, the screen's first. */
private const val MAX_PARALLEL_TILE_LOADS = 4

/** Trips line width on the ground, about one direction's lanes on a city street. */
private const val ROAD_LINE_WIDTH_M = 5.0

/**
 * Road quality lines snapped to a road (ROAD pieces) are as wide as the lanes in their direction:
 * lanes × LANE_WIDTH_M × LANE_FILL, a little narrower than the lanes so the road's edges show.
 * One width covers every lane in that direction, since GPS can't tell lanes apart.
 */
private const val LANE_WIDTH_M = 3.3
private const val LANE_FILL = 0.9

/** CELL pieces (not on a matched road) have no lane count; about one lane. */
private const val CELL_LINE_WIDTH_M = 3.5

/**
 * On a two-way road, ROAD pieces lie on the road's center line, so each direction is moved to its
 * right by half its width plus this, leaving twice this between the two directions.
 * One-way roads (including each side of a divided highway) and CELL pieces are drawn where they are.
 */
private const val ROAD_SIDE_GAP_M = 0.25

/** So lines stay visible when zoomed out, where a few meters would be less than a pixel. */
private const val MIN_ROAD_LINE_WIDTH_DP = 4f

/**
 * How far event markers and Trips lines are drawn to the right of where the car was, so the two
 * directions of a road don't sit on top of each other. 0: raw GPS already puts the car in its own
 * lane, and 3 m pushed lines off the road.
 */
private const val DIRECTION_OFFSET_M = 0.0

/** What the map shows. Release builds always show Road quality. */
private enum class MapView(val label: String) {
    ROAD_QUALITY("Road quality"),   // every drive combined, from Ben's map functions
    TRIPS("Trips"),                 // each trip drawn on its own, from Supabase
}

@Composable
fun MapScreen(modifier: Modifier = Modifier) {
    val scope = rememberCoroutineScope()
    val repository = remember { if (SupabaseConnection.isConfigured) SupabaseTripRepository() else null }

    var view by rememberSaveable { mutableStateOf(MapView.ROAD_QUALITY) }
    var drives by remember { mutableStateOf(emptyList<Drive>()) }                     // Trips
    var pieces by remember { mutableStateOf(emptyList<RoadPiece>()) }                  // Road quality
    var spots by remember { mutableStateOf(emptyList<EventSpot>()) }                   // Road quality
    var status by remember { mutableStateOf("") }

    var mapLoaded by remember { mutableStateOf(false) }
    var mapSize by remember { mutableStateOf(IntSize.Zero) }
    val cameraPositionState = rememberCameraPositionState {
        position = CameraPosition.fromLatLngZoom(UW_CAMPUS, DEFAULT_ZOOM)
    }
    val density = LocalDensity.current.density
    val colorMode by AppSettings.colorMode.collectAsState()

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

    // Road quality, in tiles (RoadQualityTiles.kt). Each time the map stops moving: show the tiles
    // already loaded around the screen, then load the missing ones, the screen's first, then a
    // ring around it. A tile that has started loading always finishes (it's kept for later);
    // tiles still waiting their turn are dropped if the map has moved away from them.
    // Starts before the map has finished drawing, so both happen at once.
    LaunchedEffect(view, mapSize) {
        if (view != MapView.ROAD_QUALITY || mapSize == IntSize.Zero) return@LaunchedEffect
        if (repository == null) {
            status = NOT_CONFIGURED
            return@LaunchedEffect
        }
        val loads = this
        val requestSlots = Semaphore(MAX_PARALLEL_TILE_LOADS)
        val queued = HashMap<RoadTile, Job>()    // waiting for a request slot
        val loading = HashSet<RoadTile>()        // request sent
        var lastError: String? = null

        /** The screen's tiles, and the ring of tiles around them. */
        fun wantedTiles(): Pair<List<RoadTile>, List<RoadTile>> {
            val camera = cameraPositionState.position
            val band = zoomBand(camera.zoom)
            val screenRadiusM = max(mapSize.width, mapSize.height) / 2.0 /
                metersToPixels(1.0, camera.zoom, camera.target.latitude, density)
            val screen = boxAround(camera.target, screenRadiusM)
            val visible = tilesCovering(screen, band)
            return visible to (tilesCovering(screen, band, ring = 1) - visible.toSet())
        }

        /** Draws what's loaded for the screen and the ring around it (a piece in two tiles once). */
        fun show() {
            val (visible, ring) = wantedTiles()
            val tiles = (visible + ring).mapNotNull { RoadQualityTiles.loaded[it] }
            pieces = tiles.flatMap { it.pieces }.distinctBy { it.pieceId }
            spots = tiles.flatMap { it.spots }.distinctBy { Triple(it.lat, it.lon, it.eventClass) }
            status = when {
                lastError != null -> "Couldn't load: $lastError"
                visible.any { it !in RoadQualityTiles.loaded } -> "Loading…"
                pieces.isEmpty() && spots.isEmpty() -> "No drives here yet"
                else -> ""
            }
        }

        fun load(tile: RoadTile) {
            if (tile in RoadQualityTiles.loaded || tile in queued || tile in loading) return
            queued[tile] = loads.launch {
                try {
                    requestSlots.withPermit {
                        queued.remove(tile)
                        loading += tile
                        try {
                            RoadQualityTiles.loaded[tile] = loadTile(repository, tile)
                            lastError = null
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            // Not kept, so the next time the map stops it's tried again.
                            lastError = e.shortMessage()
                        } finally {
                            loading -= tile
                        }
                    }
                    show()
                } finally {
                    queued.remove(tile)
                }
            }
        }

        snapshotFlow { cameraPositionState.isMoving }.collect { moving ->
            if (moving) return@collect
            val (visible, ring) = wantedTiles()
            val wanted = (visible + ring).toSet()
            // Tiles still waiting their turn that the map has moved away from: drop them, so the
            // ones needed now go first.
            queued.filterKeys { it !in wanted }.values.forEach { it.cancel() }
            visible.forEach(::load)
            ring.forEach(::load)
            show()
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

    // Line widths depend on the zoom and, a little, on the latitude. Read through derivedStateOf,
    // in steps, so the lines are redrawn only when the zoom changes by a step: panning or turning
    // the map doesn't redraw this whole screen on every frame.
    val zoom by remember {
        derivedStateOf { floor(cameraPositionState.position.zoom / ZOOM_STEP) * ZOOM_STEP }
    }
    val latitude by remember {
        derivedStateOf { cameraPositionState.position.target.latitude.roundToInt().toDouble() }
    }
    val lineWidthPx = max(
        metersToPixels(ROAD_LINE_WIDTH_M, zoom, latitude, density),
        MIN_ROAD_LINE_WIDTH_DP * density,
    )
    // Road quality lines each have their own width on the ground (see lineWidthM).
    val pixelsPerMeter = metersToPixels(1.0, zoom, latitude, density)
    val minLineWidthPx = MIN_ROAD_LINE_WIDTH_DP * density
    Box(modifier.onSizeChanged { mapSize = it }) {
        GoogleMap(
            modifier = Modifier.fillMaxSize(),
            cameraPositionState = cameraPositionState,
            // Google's compass can only sit in the top-left; ours (below) sits above the zoom buttons.
            uiSettings = MapUiSettings(compassEnabled = false),
            // Light or dark map with the app's theme (Settings > Theme), not the phone's.
            mapColorScheme = if (LocalDarkTheme.current) ComposeMapColorScheme.DARK else ComposeMapColorScheme.LIGHT,
            onMapLoaded = { mapLoaded = true },
        ) {
            if (view == MapView.ROAD_QUALITY) {
                pieces.forEach { piece ->
                    RoadPieceOnMap(piece, max((piece.lineWidthM() * pixelsPerMeter).toFloat(), minLineWidthPx), colorMode, onClick = {
                        status = "%.3f g · %d drive(s)".format(piece.roughness, piece.drives)
                    })
                }
                // Only potholes and speed bumps are shown; unknown bumps stay in the data for tuning.
                if (zoom >= MIN_EVENT_MARKER_ZOOM) {
                    spots.filter { it.eventClass != EventClass.UNKNOWN }.forEach { EventSpotMarker(it, colorMode) }
                }
            }
            if (view == MapView.TRIPS) drives.forEach { drive -> DriveOnMap(drive, lineWidthPx, colorMode) }
        }

        NorthButton(
            bearing = { cameraPositionState.position.bearing },
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

        // What the colors mean, in the current color-blind mode. Bottom center, between Google's
        // logo (bottom left, which must stay visible) and the zoom buttons (bottom right).
        if (view == MapView.ROAD_QUALITY) {
            MapLegend(colorMode, Modifier.align(Alignment.BottomCenter).padding(12.dp))
        }

        val topBar = Modifier.align(Alignment.TopCenter).fillMaxWidth().padding(10.dp)
        if (BuildConfig.DEBUG) {
            DebugBar(view = view, onViewChange = { view = it }, status = status, modifier = topBar)
        } else if (status.isNotEmpty()) {
            MapOverlayCard(topBar) {
                Text(status, style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

/** The rounded card that floats over the map, in the app's card style. */
@Composable
private fun MapOverlayCard(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surfaceContainerLowest,
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Box(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) { content() }
    }
}

/** Smooth to rough, as a small color bar. */
@Composable
private fun MapLegend(colorMode: ColorMode, modifier: Modifier = Modifier) {
    val dark = LocalDarkTheme.current
    val steps = remember(colorMode, dark) {
        (0 until LEGEND_STEPS).map { i ->
            val roughness = RoughnessScale.SMOOTH +
                (RoughnessScale.VERY_ROUGH - RoughnessScale.SMOOTH) * i / (LEGEND_STEPS - 1)
            roughnessColor(roughness, colorMode, dark)
        }
    }
    MapOverlayCard(modifier) {
        Column(Modifier.width(IntrinsicSize.Max), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Row(Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp))) {
                steps.forEach { Box(Modifier.weight(1f).fillMaxHeight().background(it)) }
            }
            Row(Modifier.fillMaxWidth()) {
                Text("Smooth", style = MaterialTheme.typography.labelSmall, modifier = Modifier.weight(1f))
                Text("Rough", style = MaterialTheme.typography.labelSmall)
            }
            // Road quality lines follow OpenStreetMap roads, whose licence (ODbL) requires this.
            Text(
                "© OpenStreetMap contributors",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                softWrap = false,
            )
        }
    }
}

private const val LEGEND_STEPS = 12

/** Zoom levels are rounded down to this step for line widths (about 7% wider per step). */
private const val ZOOM_STEP = 0.1f

// Lines the north button up above Google's +/− zoom buttons in the bottom-right corner.
private val NORTH_BUTTON_END_PADDING = 12.dp
private val NORTH_BUTTON_BOTTOM_PADDING = 112.dp
private val NORTH_BUTTON_SIZE = 40.dp

/**
 * Turns the map back to north up. The needle turns with the map so it always points north, like
 * the compass in Google Maps; red is the north end. [bearing] is read while drawing, so turning
 * the map only redraws the needle, not the screen.
 */
@Composable
private fun NorthButton(bearing: () -> Float, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Surface(
        onClick = onClick,
        shape = CircleShape,
        color = MaterialTheme.colorScheme.surfaceContainerLowest,
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
        modifier = modifier.size(NORTH_BUTTON_SIZE),
    ) {
        Canvas(
            Modifier
                .padding(8.dp)
                // The map turned clockwise by the bearing, so north is that far counterclockwise.
                .graphicsLayer { rotationZ = -bearing() }
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

/** One tile's road pieces and pins, both requests at once. Debug builds count fake trips too. */
private suspend fun loadTile(repository: SupabaseTripRepository, tile: RoadTile): TileData = coroutineScope {
    val box = tile.box
    val pieces = async {
        repository.roadQuality(box, tile.band.pieceM, includeFake = BuildConfig.DEBUG, sinceMs = MAP_DRIVES_SINCE_MS)
    }
    val spots = async {
        if (tile.band.withEvents) {
            repository.eventsInArea(box, includeFake = BuildConfig.DEBUG, sinceMs = MAP_DRIVES_SINCE_MS)
        } else {
            emptyList()
        }
    }
    TileData(pieces.await(), spots.await())
}

/** Debug builds only: pick what the map shows. */
@Composable
private fun DebugBar(
    view: MapView,
    onViewChange: (MapView) -> Unit,
    status: String,
    modifier: Modifier = Modifier,
) {
    MapOverlayCard(modifier) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                MapView.entries.forEach { option ->
                    FilterChip(
                        selected = view == option,
                        onClick = { onViewChange(option) },
                        label = { Text(option.label) },
                        modifier = Modifier.padding(end = 8.dp),
                        shape = RoundedCornerShape(50),
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
                            selectedLabelColor = MaterialTheme.colorScheme.primary,
                        ),
                    )
                }
            }
            if (status.isNotEmpty()) {
                Text(status, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/**
 * Road quality view: one piece of road, drawn along its path in one color. Snapped pieces on a
 * two-way road are moved to their direction's side of the road.
 */
@Composable
@GoogleMapComposable
private fun RoadPieceOnMap(piece: RoadPiece, lineWidthPx: Float, colorMode: ColorMode, onClick: () -> Unit) {
    val points = remember(piece) {
        val path = piece.path.map { LatLng(it.lat, it.lon) }
        if (piece.kind == RoadPieceKind.ROAD && piece.oneway == false) {
            shiftPathRight(path, piece.lineWidthM() / 2 + ROAD_SIDE_GAP_M)
        } else {
            path
        }
    }
    if (points.size < 2) return
    Polyline(
        points = points,
        color = roughnessColor(piece.roughness, colorMode, LocalDarkTheme.current),
        width = lineWidthPx,
        // Round ends and joints overlap the next piece a little, so the road looks continuous.
        startCap = RoundCap(),
        endCap = RoundCap(),
        jointType = JointType.ROUND,
        clickable = true,
        onClick = { onClick() },
    )
}

/** Width on the ground: the lanes in the piece's direction for ROAD pieces, about one lane for CELL. */
private fun RoadPiece.lineWidthM(): Double =
    if (kind == RoadPieceKind.ROAD) (lanesDir ?: 1).coerceIn(1, 8) * LANE_WIDTH_M * LANE_FILL else CELL_LINE_WIDTH_M

/** Road quality view: a pothole or speed bump spot, merged over every drive that hit it. */
@Composable
@GoogleMapComposable
private fun EventSpotMarker(spot: EventSpot, colorMode: ColorMode) {
    val position = remember(spot) {
        val point = LatLng(spot.lat, spot.lon)
        // On its direction's side of the road, like the road pieces. No heading: leave it centered.
        spot.headingDeg?.let { shiftRight(point, headingDirection(it), DIRECTION_OFFSET_M) } ?: point
    }
    EventMarker(
        colorMode = colorMode,
        eventClass = spot.eventClass,
        position = position,
        // 0 g means no device reading (an older hand label), so no g is shown then.
        snippet = "Hit by %d of %d drives".format(spot.hits, spot.passes) +
            (if (spot.maxPeakZ > 0) " · %.1f g".format(spot.maxPeakZ) else ""),
    )
}

/** One trip: its road lines and its event markers, shifted to the side of the road it drove on. */
@Composable
@GoogleMapComposable
private fun DriveOnMap(drive: Drive, lineWidthPx: Float, colorMode: ColorMode) {
    // Drawn: segments with a GPS fix and a heading. Without a heading the car was barely moving,
    // and its GPS points jitter in place, which made the line zigzag. Road quality leaves them
    // out too. Where segments are missing for a while (lost GPS or device link), the line breaks
    // instead of jumping straight across the gap.
    val stretches = remember(drive) {
        splitAtGaps(drive.segments.filter { it.latLng() != null && it.headingDeg != null })
    }
    stretches.forEach { RoadLine(it, lineWidthPx, colorMode) }

    // Events without a GPS fix can't be placed; skip them.
    drive.events.filter { it.latLng() != null }.forEach { event ->
        // A user label wins, except UNKNOWN, which means "not sure" (contract §7).
        val eventClass = event.userLabel?.takeIf { it != EventClass.UNKNOWN } ?: event.eventClass
        // Only potholes and speed bumps are shown; unknown bumps stay in the data for tuning.
        if (eventClass == EventClass.UNKNOWN) return@forEach
        val position = remember(event) {
            // On its direction's side of the road, like the line. No heading: leave it unshifted.
            event.headingDeg?.let { shiftRight(event.latLng()!!, headingDirection(it), DIRECTION_OFFSET_M) }
                ?: event.latLng()!!
        }
        EventMarker(
            colorMode = colorMode,
            eventClass = eventClass,
            position = position,
            snippet = (if (event.peakZ > 0) "%.1f g · ".format(event.peakZ) else "") +
                "%d%% sure".format((event.conf * 100).toInt()),
        )
    }
}

/** A gap longer than this between two segments breaks a trip's line. */
private const val MAX_SEGMENT_GAP_MS = 5_000L

/** Splits a trip's segments into stretches with no gap longer than [MAX_SEGMENT_GAP_MS]. */
private fun splitAtGaps(segments: List<Segment>): List<List<Segment>> {
    val stretches = mutableListOf<MutableList<Segment>>()
    segments.forEach { segment ->
        val previous = stretches.lastOrNull()?.last()
        if (previous == null || segment.tMid - previous.tMid > MAX_SEGMENT_GAP_MS) {
            stretches += mutableListOf(segment)
        } else {
            stretches.last() += segment
        }
    }
    return stretches
}

/** One unbroken stretch of a trip, its color fading from each segment's roughness to the next. */
@Composable
@GoogleMapComposable
private fun RoadLine(located: List<Segment>, lineWidthPx: Float, colorMode: ColorMode) {
    val path = remember(located) { located.mapNotNull { it.latLng() } }
    if (path.size < 2) return
    val shiftedPath = remember(path) { shiftPathRight(path, DIRECTION_OFFSET_M) }
    // Each piece between two points fades from the first point's color to the second's.
    // A span covers one piece by default, so there's one span per piece.
    val dark = LocalDarkTheme.current
    val spans = remember(located, colorMode, dark) {
        val colors = located.map { roughnessColor(it.rmsZ, colorMode, dark).toArgb() }
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
}

private fun Segment.latLng(): LatLng? = if (lat != null && lon != null) LatLng(lat, lon) else null

private fun RoadEvent.latLng(): LatLng? = if (lat != null && lon != null) LatLng(lat, lon) else null

/** A pothole or speed bump pin, colored for the chosen color mode (see eventMarkerHue). */
@Composable
@GoogleMapComposable
private fun EventMarker(colorMode: ColorMode, eventClass: EventClass, position: LatLng, snippet: String) {
    val title = when (eventClass) {
        EventClass.POTHOLE -> "Pothole"
        EventClass.SPEED_BUMP -> "Speed bump"
        EventClass.UNKNOWN -> "Bump"
    }
    val hue = eventMarkerHue(eventClass, colorMode)
    Marker(
        state = rememberUpdatedMarkerState(position = position),
        title = title,
        snippet = snippet,
        icon = BitmapDescriptorFactory.defaultMarker(hue),
    )
}
