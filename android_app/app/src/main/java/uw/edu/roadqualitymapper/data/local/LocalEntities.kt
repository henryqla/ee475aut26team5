package uw.edu.roadqualitymapper.data.local

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import uw.edu.roadqualitymapper.data.DataSource
import uw.edu.roadqualitymapper.data.EventClass
import uw.edu.roadqualitymapper.data.GpsFix
import uw.edu.roadqualitymapper.data.RoadEvent
import uw.edu.roadqualitymapper.data.Segment
import uw.edu.roadqualitymapper.data.Trip

// Tables in the phone's own database. Everything is saved here first and uploaded later
// (contract §6), so a drive survives no signal, a killed app, or the server being down.
// The `uploaded` flags record what has reached Supabase.

@Entity(tableName = "trips")
data class TripEntity(
    @PrimaryKey val id: String,
    val vehicleId: String,
    val startTime: Long,
    val endTime: Long? = null,
    val source: DataSource,
    val fwVersion: String? = null,
    val tripUploaded: Boolean = false,   // the trip row is on the server
    val endUploaded: Boolean = false,    // end_time is on the server
)

@Entity(
    tableName = "segments",
    primaryKeys = ["tripId", "tStart"],
    indices = [Index("uploaded")],
)
data class SegmentEntity(
    val tripId: String,
    val tStart: Long,
    val tEnd: Long,
    val lat: Double?,
    val lon: Double?,
    val speedMps: Double?,
    val gpsAccuracyM: Double?,
    val headingDeg: Double?,
    val rmsZ: Double,
    val peakZ: Double,
    val rollRms: Double,
    val pitchRms: Double,
    val uploaded: Boolean = false,
)

@Entity(tableName = "events", indices = [Index("uploaded")])
data class EventEntity(
    @PrimaryKey val id: String,
    val tripId: String,
    val vehicleId: String,
    val t: Long,
    val lat: Double?,
    val lon: Double?,
    val speedMps: Double?,
    val gpsAccuracyM: Double?,
    val headingDeg: Double?,
    val eventClass: EventClass,
    val peakZ: Double,
    val rollPeak: Double,
    val pitchPeak: Double,
    val conf: Double,
    val userLabel: EventClass? = null,
    val uploaded: Boolean = false,
)

/** Every GPS fix of a trip, as received. Not uploaded; kept for re-processing and replay. */
@Entity(tableName = "gps_fixes", indices = [Index("tripId")])
data class GpsFixEntity(
    @PrimaryKey(autoGenerate = true) val rowId: Long = 0,
    val tripId: String,
    val t: Long,
    val lat: Double,
    val lon: Double,
    val speedMps: Double?,
    val headingDeg: Double?,
    val accuracyM: Double?,
)

/** Every message from the device during a trip, exactly as received. Not uploaded. */
@Entity(tableName = "device_messages", indices = [Index("tripId")])
data class DeviceMessageEntity(
    @PrimaryKey(autoGenerate = true) val rowId: Long = 0,
    val tripId: String,
    val receivedAt: Long,   // phone time, epoch millis
    val text: String,
)

// ---------------------------------------------------------------- app models <-> rows

fun Trip.toEntity() = TripEntity(
    id = id,
    vehicleId = vehicleId,
    startTime = startTime,
    endTime = endTime,
    source = source,
    fwVersion = fwVersion,
)

fun TripEntity.toTrip() = Trip(
    id = id,
    vehicleId = vehicleId,
    startTime = startTime,
    endTime = endTime,
    source = source,
    fwVersion = fwVersion,
)

fun SegmentEntity.toSegment() = Segment(
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

/** An event's source must match its trip's, so it comes from the trip. */
fun EventEntity.toEvent(trip: TripEntity) = RoadEvent(
    id = id,
    tripId = tripId,
    vehicleId = vehicleId,
    t = t,
    lat = lat,
    lon = lon,
    speedMps = speedMps,
    gpsAccuracyM = gpsAccuracyM,
    headingDeg = headingDeg,
    eventClass = eventClass,
    peakZ = peakZ,
    rollPeak = rollPeak,
    pitchPeak = pitchPeak,
    conf = conf,
    userLabel = userLabel,
    source = trip.source,
)

fun GpsFix.toEntity(tripId: String) = GpsFixEntity(
    tripId = tripId,
    t = t,
    lat = lat,
    lon = lon,
    speedMps = speedMps,
    headingDeg = headingDeg,
    accuracyM = accuracyM,
)
