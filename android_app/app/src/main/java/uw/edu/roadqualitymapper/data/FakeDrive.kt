package uw.edu.roadqualitymapper.data

import android.content.Context
import org.json.JSONObject
import java.io.FileNotFoundException

/** Reads hand-made trips from a JSON file, for seeing the map before real data exists. */
object FakeDriveLoader {
    /** Lives in src/debug/assets, so release builds don't contain it. */
    private const val ASSET_NAME = "fake_drive.json"

    /** Returns an empty list when the asset isn't there (release builds). */
    fun load(context: Context): List<Drive> {
        val text = try {
            context.assets.open(ASSET_NAME).bufferedReader().use { it.readText() }
        } catch (e: FileNotFoundException) {
            return emptyList()
        }
        val tripsJson = JSONObject(text).getJSONArray("trips")
        return List(tripsJson.length()) { i -> parseTrip(tripsJson.getJSONObject(i)) }
    }

    /** JSON keys are snake_case to match the planned Supabase columns. */
    private fun parseTrip(tripJson: JSONObject): Drive {
        val trip = Trip(
            id = tripJson.getString("id"),
            vehicleId = tripJson.getString("vehicle_id"),
            startTime = tripJson.getLong("start_time"),
            endTime = tripJson.getLong("end_time"),
            source = DataSource.FAKE_ASSET,
        )

        val segmentsJson = tripJson.getJSONArray("segments")
        val segments = List(segmentsJson.length()) { i ->
            val s = segmentsJson.getJSONObject(i)
            Segment(
                tStart = s.getLong("t_start"),
                tEnd = s.getLong("t_end"),
                lat = s.doubleOrNull("lat"),
                lon = s.doubleOrNull("lon"),
                speedMps = s.doubleOrNull("speed_mps"),
                gpsAccuracyM = s.doubleOrNull("gps_accuracy_m"),
                headingDeg = s.doubleOrNull("heading_deg"),
                rmsZ = s.getDouble("rms_z"),
                peakZ = s.getDouble("peak_z"),
                rollRms = s.getDouble("roll_rms"),
                pitchRms = s.getDouble("pitch_rms"),
            )
        }

        val eventsJson = tripJson.getJSONArray("events")
        val events = List(eventsJson.length()) { i ->
            val e = eventsJson.getJSONObject(i)
            RoadEvent(
                id = e.getString("id"),
                tripId = trip.id,
                vehicleId = trip.vehicleId,
                t = e.getLong("t"),
                lat = e.doubleOrNull("lat"),
                lon = e.doubleOrNull("lon"),
                speedMps = e.doubleOrNull("speed_mps"),
                gpsAccuracyM = e.doubleOrNull("gps_accuracy_m"),
                headingDeg = e.doubleOrNull("heading_deg"),
                eventClass = when (e.getString("class")) {
                    "pothole" -> EventClass.POTHOLE
                    "speed_bump" -> EventClass.SPEED_BUMP
                    else -> EventClass.UNKNOWN
                },
                peakZ = e.getDouble("peak_z"),
                rollPeak = e.getDouble("roll_peak"),
                pitchPeak = e.getDouble("pitch_peak"),
                conf = e.getDouble("conf"),
                source = DataSource.FAKE_ASSET,
            )
        }

        return Drive(trip, segments, events)
    }

    /** Location fields can be missing or null (no GPS fix close enough in time). */
    private fun JSONObject.doubleOrNull(key: String): Double? = if (isNull(key)) null else getDouble(key)
}
