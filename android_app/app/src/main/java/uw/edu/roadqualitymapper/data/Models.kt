package uw.edu.roadqualitymapper.data

/** Where data came from. Fake data is tagged so it can be filtered out and deleted before the demo. */
enum class DataSource { FAKE_ASSET, FAKE_DEVICE, REAL }

enum class EventClass { POTHOLE, SPEED_BUMP, UNKNOWN }

/** ~1 second of road. Location and speed come from the phone; roughness comes from the device. */
data class Segment(
    val t: Long = 0L,                 // phone time, epoch millis
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    val speedMps: Double = 0.0,
    val roughness: Double = 0.0,
)

data class RoadEvent(
    val id: String = "",
    val tripId: String = "",
    val vehicleId: String = "",
    val t: Long = 0L,
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    val speedMps: Double = 0.0,
    val eventClass: EventClass = EventClass.UNKNOWN,
    val peakG: Double = 0.0,
    val roll: Double = 0.0,
    val confidence: Double = 0.0,
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