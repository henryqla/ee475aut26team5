package uw.edu.roadqualitymapper.data

/**
 * Everything the app needs from the backend. The service and screens only use this interface,
 * so the Supabase backend (or any other) is one implementation, nothing else.
 *
 * Trip IDs are UUIDs made by the app, so trips can be recorded offline and uploaded later.
 * Uploads skip rows that are already stored, so retrying any upload is always safe.
 */
interface TripRepository {
    /** Must be uploaded before its segments and events (contract §7). */
    suspend fun uploadTrip(trip: Trip)
    suspend fun uploadSegments(trip: Trip, segments: List<Segment>)   // batched, ~60 at a time
    suspend fun uploadEvents(events: List<RoadEvent>)
    suspend fun endTrip(tripId: String, endTime: Long)
    /** Newest first. [includeFake] also returns FAKE_ASSET / FAKE_DEVICE trips. */
    suspend fun trips(includeFake: Boolean): List<Trip>
    suspend fun segmentsForTrip(tripId: String): List<Segment>
    suspend fun eventsForTrip(tripId: String): List<RoadEvent>
    suspend fun eventsInArea(area: BoundingBox): List<RoadEvent>
    suspend fun vehicles(): List<Vehicle>
}

/** Uploads a whole trip in the order the database requires: trip, then segments, then events. */
suspend fun TripRepository.uploadDrive(drive: Drive) {
    uploadTrip(drive.trip)
    // If the trip was already stored (e.g. uploaded when it started), uploadTrip skips it and
    // leaves end_time as it was, so set it separately.
    drive.trip.endTime?.let { endTrip(drive.trip.id, it) }
    uploadSegments(drive.trip, drive.segments)
    uploadEvents(drive.events)
}

/** Downloads trips with all their segments and events, for drawing on the map. */
suspend fun TripRepository.downloadDrives(includeFake: Boolean): List<Drive> =
    trips(includeFake).map { trip ->
        Drive(trip, segmentsForTrip(trip.id), eventsForTrip(trip.id))
    }
