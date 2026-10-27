package uw.edu.roadqualitymapper.data

import android.content.Context
import org.json.JSONObject
import java.io.FileNotFoundException

/** A whole drive read from a hand-made JSON file, for seeing the map before real data exists. */
data class FakeDrive(
    val trip: Trip,
    val segments: List<Segment>,
    val events: List<RoadEvent>,
)

object FakeDriveLoader {
    /** Lives in src/debug/assets, so release builds don't contain it. */
    private const val ASSET_NAME = "fake_drive.json"

    /** Returns null when the asset isn't there (release builds). */
    fun load(context: Context): FakeDrive? {
        val text = try {
            context.assets.open(ASSET_NAME).bufferedReader().use { it.readText() }
        } catch (e: FileNotFoundException) {
            return null
        }
        return parse(text)
    }

    /** JSON keys are snake_case to match the planned Supabase columns. */
    private fun parse(text: String): FakeDrive {
        val root = JSONObject(text)

        val tripJson = root.getJSONObject("trip")
        val trip = Trip(
            id = tripJson.getString("id"),
            vehicleId = tripJson.getString("vehicle_id"),
            startTime = tripJson.getLong("start_time"),
            endTime = tripJson.getLong("end_time"),
            source = DataSource.FAKE_ASSET,
        )

        val segmentsJson = root.getJSONArray("segments")
        val segments = List(segmentsJson.length()) { i ->
            val s = segmentsJson.getJSONObject(i)
            Segment(
                t = s.getLong("t"),
                lat = s.getDouble("lat"),
                lon = s.getDouble("lon"),
                speedMps = s.getDouble("speed_mps"),
                roughness = s.getDouble("roughness"),
            )
        }

        val eventsJson = root.getJSONArray("events")
        val events = List(eventsJson.length()) { i ->
            val e = eventsJson.getJSONObject(i)
            RoadEvent(
                id = e.getString("id"),
                tripId = trip.id,
                vehicleId = trip.vehicleId,
                t = e.getLong("t"),
                lat = e.getDouble("lat"),
                lon = e.getDouble("lon"),
                speedMps = e.getDouble("speed_mps"),
                eventClass = when (e.getString("class")) {
                    "pothole" -> EventClass.POTHOLE
                    "speed_bump" -> EventClass.SPEED_BUMP
                    else -> EventClass.UNKNOWN
                },
                peakG = e.getDouble("peak_g"),
                roll = e.getDouble("roll"),
                confidence = e.getDouble("conf"),
                source = DataSource.FAKE_ASSET,
            )
        }

        return FakeDrive(trip, segments, events)
    }
}
