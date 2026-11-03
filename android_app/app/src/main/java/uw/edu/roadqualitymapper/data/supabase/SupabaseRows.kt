package uw.edu.roadqualitymapper.data.supabase

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import uw.edu.roadqualitymapper.data.BoundingBox
import uw.edu.roadqualitymapper.data.DataSource
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.EventSpot
import uw.edu.roadqualitymapper.data.RoadEvent
import uw.edu.roadqualitymapper.data.RoadPiece
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
    const val ROAD_QUALITY_IN_AREA = "road_quality_in_area"
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
)

@Serializable
internal data class RoadPieceRow(
    @SerialName("lat") val lat: Double,
    @SerialName("lon") val lon: Double,
    @SerialName("heading_deg") val headingDeg: Double,
    @SerialName("roughness") val roughness: Double,
    @SerialName("drives") val drives: Int,
    @SerialName("length_m") val lengthM: Double,
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

/** A segment's source must match its trip's, so it comes from the trip. */
internal fun Segment.toRow(trip: Trip) = SegmentRow(
    tripId = trip.id,
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
    source = trip.source.name,
)

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

internal fun RoadEvent.toRow() = EventRow(
    id = id,
    tripId = tripId,
    vehicleId = vehicleId,
    t = t,
    lat = lat,
    lon = lon,
    speedMps = speedMps,
    gpsAccuracyM = gpsAccuracyM,
    headingDeg = headingDeg,
    eventClass = eventClass.name,
    peakZ = peakZ,
    rollPeak = rollPeak,
    pitchPeak = pitchPeak,
    conf = conf,
    userLabel = userLabel?.name,
    source = source.name,
)

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

internal fun BoundingBox.toParams(includeFake: Boolean, vehicleId: String?) = AreaParams(
    south = south,
    west = west,
    north = north,
    east = east,
    includeFake = includeFake,
    vehicleId = vehicleId,
)

internal fun RoadPieceRow.toRoadPiece() = RoadPiece(
    lat = lat,
    lon = lon,
    headingDeg = headingDeg,
    roughness = roughness,
    drives = drives,
    lengthM = lengthM,
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
