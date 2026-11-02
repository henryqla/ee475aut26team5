package uw.edu.roadqualitymapper.data

/** Where data came from. Fake data is tagged so it can be filtered out and deleted before the demo. */
enum class DataSource { FAKE_ASSET, FAKE_DEVICE, REAL }

enum class EventClass { POTHOLE, SPEED_BUMP, UNKNOWN }

/**
 * ~1 second of road. Field names follow docs/data-contract.md.
 * Location fields come from the phone and are null when no GPS fix was close enough in time;
 * the measurement fields come from the device.
 */
data class Segment(
    val tStart: Long = 0L,            // phone time, epoch millis
    val tEnd: Long = 0L,
    val lat: Double? = null,
    val lon: Double? = null,
    val speedMps: Double? = null,
    val gpsAccuracyM: Double? = null,
    val headingDeg: Double? = null,   // direction of travel, clockwise from north; null when barely moving
    val rmsZ: Double = 0.0,           // g, roughness: RMS vertical acceleration, gravity removed
    val peakZ: Double = 0.0,          // g
    val rollRms: Double = 0.0,        // deg/s
    val pitchRms: Double = 0.0,       // deg/s
) {
    /** Middle of the window; the segment's location is interpolated at this time. */
    val tMid: Long get() = (tStart + tEnd) / 2
}

data class RoadEvent(
    val id: String = "",
    val tripId: String = "",
    val vehicleId: String = "",
    val t: Long = 0L,                 // phone time of the peak, epoch millis
    val lat: Double? = null,
    val lon: Double? = null,
    val speedMps: Double? = null,
    val gpsAccuracyM: Double? = null,
    val headingDeg: Double? = null,
    val eventClass: EventClass = EventClass.UNKNOWN,
    val peakZ: Double = 0.0,          // g
    val rollPeak: Double = 0.0,       // deg/s
    val pitchPeak: Double = 0.0,      // deg/s
    val conf: Double = 0.0,           // detector confidence, 0–1
    val userLabel: EventClass? = null,   // set when someone taps a label button on the Drive screen
    val source: DataSource = DataSource.REAL,
)

data class Trip(
    val id: String = "",
    val vehicleId: String = "",
    val startTime: Long = 0L,
    val endTime: Long? = null,
    val source: DataSource = DataSource.REAL,
)

/** One whole trip with its data, as drawn on the map. Comes from the fake file or from Supabase. */
data class Drive(
    val trip: Trip,
    val segments: List<Segment>,
    val events: List<RoadEvent>,
)

data class Vehicle(
    val id: String = "",
    val name: String = "",
    val type: String = "",             // "truck", "sedan", "suv"
    val scaleFactor: Double = 1.0,     // set later from reference-route calibration
)

/** Visible map area. Kept separate from Google Maps types so the data layer doesn't depend on Maps. */
data class BoundingBox(
    val south: Double,
    val west: Double,
    val north: Double,
    val east: Double,
)