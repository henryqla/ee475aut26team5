package uw.edu.roadqualitymapper.data.supabase

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import uw.edu.roadqualitymapper.data.DataSource
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.RoadEvent
import uw.edu.roadqualitymapper.data.Segment
import uw.edu.roadqualitymapper.data.Trip
import uw.edu.roadqualitymapper.data.Vehicle

// The only file that knows Supabase table and column names (docs/data-contract.md §7).
// If a table or column is renamed, change it here and nowhere else.
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
)

internal fun TripRow.toTrip() = Trip(
    id = id,
    vehicleId = vehicleId,
    startTime = startTime,
    endTime = endTime,
    source = dataSourceOf(source),
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

// Unknown values (e.g. one added to the database later) fall back instead of crashing the app.
private fun dataSourceOf(value: String) = DataSource.entries.firstOrNull { it.name == value } ?: DataSource.REAL

private fun eventClassOf(value: String) = EventClass.entries.firstOrNull { it.name == value }
