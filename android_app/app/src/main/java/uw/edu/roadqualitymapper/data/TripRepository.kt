package uw.edu.roadqualitymapper.data

import kotlinx.coroutines.flow.Flow

/**
 * Everything the app needs from the backend. The service and screens only use this interface,
 * so switching away from Firebase means writing one new implementation, nothing else.
 */
interface TripRepository {
    suspend fun startTrip(vehicleId: String, source: DataSource): String   // returns the new tripId
    suspend fun uploadSegments(tripId: String, segments: List<Segment>)    // batched, ~60 at a time
    suspend fun uploadEvent(event: RoadEvent)
    suspend fun endTrip(tripId: String)
    fun observeTrips(): Flow<List<Trip>>
    suspend fun segmentsForTrip(tripId: String): List<Segment>
    suspend fun eventsInArea(area: BoundingBox): List<RoadEvent>
    suspend fun vehicles(): List<Vehicle>
}