package uw.edu.roadqualitymapper.data.supabase

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import uw.edu.roadqualitymapper.data.BoundingBox
import uw.edu.roadqualitymapper.data.DataSource
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.EventSpot
import uw.edu.roadqualitymapper.data.PathPoint
import uw.edu.roadqualitymapper.data.RoadEvent
import uw.edu.roadqualitymapper.data.RoadPiece
import uw.edu.roadqualitymapper.data.RoadPieceKind
import uw.edu.roadqualitymapper.data.Segment
import uw.edu.roadqualitymapper.data.Trip
import uw.edu.roadqualitymapper.data.Vehicle

// The only file that knows Supabase table, column and function names (docs/data-contract.md §7).
// If one is renamed, change it here and nowhere else.
//
// Columns the database fills in itself (created_by, received_at, geog, the *_ts times) are left
// out: the app never sends them, and extra columns in query results are ignored.

internal object Tables {
    const val VEHICLES = "vehicles"
    const val TRIPS = "trips"
    const val SEGMENTS = "segments"
    const val EVENTS = "events"
}

internal object Columns {
    const val ID = "id"
    const val TRIP_ID = "trip_id"
    const val T_START = "t_start"
    const val T = "t"
    const val START_TIME = "start_time"
    const val END_TIME = "end_time"
    const val SOURCE = "source"
    const val LAT = "lat"
    const val LON = "lon"

    /** Primary keys, used to recognize rows that are already stored. */
    const val TRIPS_KEY = ID
    const val SEGMENTS_KEY = "$TRIP_ID,$T_START"
    const val EVENTS_KEY = ID
}

/** Ben's map functions, which combine all drives in an area (contract §7 "Map functions"). */
internal object Functions {
    const val ROAD_QUALITY_SNAPPED = "road_quality_snapped"
    const val EVENTS_IN_AREA = "events_in_area"
}

@Serializable
internal data class VehicleRow(
    @SerialName("id") val id: String,
    @SerialName("name") val name: String,
    @SerialName("type") val type: String,
    @SerialName("scale_factor") val scaleFactor: Double,
)

@Serializable
internal data class TripRow(
    @SerialName("id") val id: String,
    @SerialName("vehicle_id") val vehicleId: String,
    @SerialName("start_time") val startTime: Long,
    @SerialName("end_time") val endTime: Long? = null,
    @SerialName("source") val source: String,
    @SerialName("app_version") val appVersion: String? = null,
    @SerialName("fw_version") val fwVersion: String? = null,
)

@Serializable
internal data class SegmentRow(
    @SerialName("trip_id") val tripId: String,
    @SerialName("t_start") val tStart: Long,
    @SerialName("t_end") val tEnd: Long,
    @SerialName("lat") val lat: Double? = null,
    @SerialName("lon") val lon: Double? = null,
    @SerialName("speed_mps") val speedMps: Double? = null,
    @SerialName("gps_accuracy_m") val gpsAccuracyM: Double? = null,
    @SerialName("heading_deg") val headingDeg: Double? = null,
    @SerialName("rms_z") val rmsZ: Double,
    @SerialName("peak_z") val peakZ: Double,
    @SerialName("roll_rms") val rollRms: Double,
    @SerialName("pitch_rms") val pitchRms: Double,
    @SerialName("source") val source: String,
)

@Serializable
internal data class EventRow(
    @SerialName("id") val id: String,
    @SerialName("trip_id") val tripId: String,
    @SerialName("vehicle_id") val vehicleId: String,
    @SerialName("t") val t: Long,
    @SerialName("lat") val lat: Double? = null,
    @SerialName("lon") val lon: Double? = null,
    @SerialName("speed_mps") val speedMps: Double? = null,
    @SerialName("gps_accuracy_m") val gpsAccuracyM: Double? = null,
    @SerialName("heading_deg") val headingDeg: Double? = null,
    @SerialName("event_class") val eventClass: String,
    @SerialName("peak_z") val peakZ: Double,
    @SerialName("roll_peak") val rollPeak: Double,
    @SerialName("pitch_peak") val pitchPeak: Double,
    @SerialName("conf") val conf: Double,
    @SerialName("user_label") val userLabel: String? = null,
    @SerialName("source") val source: String,
)

/**
 * Parameters of both map functions. No default values here on purpose: values equal to a default
 * aren't sent, and every parameter should be sent as written.
 */
@Serializable
internal data class AreaParams(
    @SerialName("south") val south: Double,
    @SerialName("west") val west: Double,
    @SerialName("north") val north: Double,
    @SerialName("east") val east: Double,
    @SerialName("include_fake") val includeFake: Boolean,
    @SerialName("vehicle_id") val vehicleId: String?,   // null = every vehicle
    @SerialName("since_ms") val sinceMs: Long?,         // only trips that started at or after this; null = all
)

/** road_quality_snapped takes the usual area parameters plus the piece length. */
@Serializable
internal data class SnappedAreaParams(
    @SerialName("south") val south: Double,
    @SerialName("west") val west: Double,
    @SerialName("north") val north: Double,
    @SerialName("east") val east: Double,
    @SerialName("include_fake") val includeFake: Boolean,
    @SerialName("vehicle_id") val vehicleId: String?,   // null = every vehicle
    @SerialName("since_ms") val sinceMs: Long?,         // only trips that started at or after this; null = all
    @SerialName("piece_m") val pieceM: Int,             // rounded by the server to a multiple of 15, at most 3000
)

@Serializable
internal data class RoadPieceRow(
    @SerialName("piece_id") val pieceId: String,
    @SerialName("kind") val kind: String,
    @SerialName("path") val path: List<List<Double>>,   // [[lat, lon], ...]
    @SerialName("lat") val lat: Double,
    @SerialName("lon") val lon: Double,
    @SerialName("heading_deg") val headingDeg: Double? = null,
    @SerialName("length_m") val lengthM: Double,
    @SerialName("roughness") val roughness: Double,
    @SerialName("drives") val drives: Int,
    @SerialName("weight") val weight: Double,
    @SerialName("last_t") val lastT: Long,
    @SerialName("oneway") val oneway: Boolean? = null,
    @SerialName("lanes_dir") val lanesDir: Int? = null,
    @SerialName("road_name") val roadName: String? = null,
)

@Serializable
internal data class EventSpotRow(
    @SerialName("lat") val lat: Double,
    @SerialName("lon") val lon: Double,
    @SerialName("event_class") val eventClass: String,
    @SerialName("heading_deg") val headingDeg: Double? = null,
    @SerialName("hits") val hits: Int,
    @SerialName("passes") val passes: Int,
    @SerialName("last_t") val lastT: Long,
    @SerialName("max_peak_z") val maxPeakZ: Double,
)

// ---------------------------------------------------------------- app models <-> rows
// Stored values are the UPPERCASE enum names (REAL, POTHOLE, ...), the same as the Kotlin enums.

internal fun VehicleRow.toVehicle() = Vehicle(id = id, name = name, type = type, scaleFactor = scaleFactor)

internal fun Trip.toRow(appVersion: String) = TripRow(
    id = id,
    vehicleId = vehicleId,
    startTime = startTime,
    endTime = endTime,
    source = source.name,
    appVersion = appVersion,
    fwVersion = fwVersion,
)

internal fun TripRow.toTrip() = Trip(
    id = id,
    vehicleId = vehicleId,
    startTime = startTime,
    endTime = endTime,
    source = dataSourceOf(source),
    fwVersion = fwVersion,
)

/**
 * A segment's source must match its trip's, so it comes from the trip. Values are cleaned to
 * what the table's checks allow (see the helpers at the bottom), so one odd value can't get a
 * whole batch rejected.
 */
internal fun Segment.toRow(trip: Trip): SegmentRow {
    val position = validPosition(lat, lon)
    return SegmentRow(
        tripId = trip.id,
        tStart = tStart,
        tEnd = maxOf(tEnd, tStart),
        lat = position?.first,
        lon = position?.second,
        speedMps = nonNegativeOrNull(speedMps),
        gpsAccuracyM = nonNegativeOrNull(gpsAccuracyM),
        headingDeg = headingForDb(headingDeg),
    rmsZ = rmsZ,
    peakZ = peakZ,
        rollRms = rollRms,
        pitchRms = pitchRms,
        source = trip.source.name,
    )
}

internal fun SegmentRow.toSegment() = Segment(
    tStart = tStart,
    tEnd = tEnd,
    lat = lat,
    lon = lon,
    speedMps = speedMps,
    gpsAccuracyM = gpsAccuracyM,
    headingDeg = headingDeg,
    rmsZ = rmsZ,
    peakZ = peakZ,
    rollRms = rollRms,
    pitchRms = pitchRms,
)

/** Values are cleaned to what the table's checks allow, as for segments. */
internal fun RoadEvent.toRow(): EventRow {
    val position = validPosition(lat, lon)
    return EventRow(
        id = id,
        tripId = tripId,
        vehicleId = vehicleId,
        t = t,
        lat = position?.first,
        lon = position?.second,
        speedMps = nonNegativeOrNull(speedMps),
        gpsAccuracyM = nonNegativeOrNull(gpsAccuracyM),
        headingDeg = headingForDb(headingDeg),
        eventClass = eventClass.name,
        peakZ = peakZ,
        rollPeak = rollPeak,
        pitchPeak = pitchPeak,
        conf = if (conf.isNaN()) 0.0 else conf.coerceIn(0.0, 1.0),
        userLabel = userLabel?.name,
        source = source.name,
    )
}

internal fun EventRow.toEvent() = RoadEvent(
    id = id,
    tripId = tripId,
    vehicleId = vehicleId,
    t = t,
    lat = lat,
    lon = lon,
    speedMps = speedMps,
    gpsAccuracyM = gpsAccuracyM,
    headingDeg = headingDeg,
    eventClass = eventClassOf(eventClass) ?: EventClass.UNKNOWN,
    peakZ = peakZ,
    rollPeak = rollPeak,
    pitchPeak = pitchPeak,
    conf = conf,
    userLabel = userLabel?.let { eventClassOf(it) },
    source = dataSourceOf(source),
)

internal fun BoundingBox.toParams(includeFake: Boolean, vehicleId: String?, sinceMs: Long?) = AreaParams(
    south = south,
    west = west,
    north = north,
    east = east,
    includeFake = includeFake,
    vehicleId = vehicleId,
    sinceMs = sinceMs,
)

internal fun BoundingBox.toSnappedParams(pieceM: Int, includeFake: Boolean, vehicleId: String?, sinceMs: Long?) = SnappedAreaParams(
    south = south,
    west = west,
    north = north,
    east = east,
    includeFake = includeFake,
    vehicleId = vehicleId,
    sinceMs = sinceMs,
    pieceM = pieceM,
)

internal fun RoadPieceRow.toRoadPiece() = RoadPiece(
    pieceId = pieceId,
    // An unknown kind (e.g. one added later) is drawn like CELL: as sent, with no side offset.
    kind = RoadPieceKind.entries.firstOrNull { it.name == kind } ?: RoadPieceKind.CELL,
    path = path.filter { it.size >= 2 }.map { PathPoint(lat = it[0], lon = it[1]) },
    lat = lat,
    lon = lon,
    headingDeg = headingDeg,
    lengthM = lengthM,
    roughness = roughness,
    drives = drives,
    weight = weight,
    lastT = lastT,
    oneway = oneway,
    lanesDir = lanesDir,
    roadName = roadName,
)

internal fun EventSpotRow.toEventSpot() = EventSpot(
    lat = lat,
    lon = lon,
    eventClass = eventClassOf(eventClass) ?: EventClass.UNKNOWN,
    headingDeg = headingDeg,
    hits = hits,
    passes = passes,
    lastT = lastT,
    maxPeakZ = maxPeakZ,
)

// Unknown values (e.g. one added to the database later) fall back instead of crashing the app.
private fun dataSourceOf(value: String) = DataSource.entries.firstOrNull { it.name == value } ?: DataSource.REAL

private fun eventClassOf(value: String) = EventClass.entries.firstOrNull { it.name == value }

// Cleaning values to the tables' checks (contract §7). The fixes are tiny or replace a reading
// that can't be right with "unknown", so no real data is lost.

/**
 * Heading as the table stores it: a 4-byte real, at least 0 and under 360. A heading just under
 * 360 (e.g. 359.99999, from averaging two headings either side of north) rounds up to 360.0 in
 * a 4-byte real, which the table rejects, so it's sent as 0 (the same direction).
 */
private fun headingForDb(degrees: Double?): Double? {
    if (degrees == null || degrees.isNaN() || degrees.isInfinite()) return null
    val heading = ((degrees % 360) + 360) % 360
    return if (heading.toFloat() >= 360f) 0.0 else heading
}

/** Speed and GPS accuracy can't be negative; a negative (or NaN) reading is sent as unknown. */
private fun nonNegativeOrNull(value: Double?): Double? = value?.takeIf { it >= 0 && !it.isInfinite() }

/** Latitude and longitude go together and must be in range; otherwise neither is sent. */
private fun validPosition(lat: Double?, lon: Double?): Pair<Double, Double>? =
    if (lat != null && lon != null && lat in -90.0..90.0 && lon in -180.0..180.0) lat to lon else null
