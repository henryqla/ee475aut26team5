package uw.edu.roadqualitymapper.data.local

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow
import uw.edu.roadqualitymapper.data.DataSource

@Dao
interface RoadDao {

    // ------------------------------------------------------------ trips

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertTrip(trip: TripEntity)

    @Query("SELECT * FROM trips WHERE id = :id")
    suspend fun trip(id: String): TripEntity?

    @Query("UPDATE trips SET endTime = :endTime WHERE id = :id")
    suspend fun setEndTime(id: String, endTime: Long)

    /** Only until the trip row is uploaded: the server never changes fw_version or source after. */
    @Query("UPDATE trips SET fwVersion = :fwVersion, source = :source WHERE id = :id AND tripUploaded = 0")
    suspend fun setDeviceInfo(id: String, fwVersion: String, source: DataSource)

    @Query("SELECT * FROM trips WHERE tripUploaded = 0 ORDER BY startTime")
    suspend fun tripsNotUploaded(): List<TripEntity>

    @Query("UPDATE trips SET tripUploaded = 1 WHERE id = :id")
    suspend fun markTripUploaded(id: String)

    @Query("SELECT * FROM trips WHERE endTime IS NOT NULL AND endUploaded = 0")
    suspend fun tripsWithEndNotUploaded(): List<TripEntity>

    @Query("UPDATE trips SET endUploaded = 1 WHERE id = :id")
    suspend fun markEndUploaded(id: String)

    // ------------------------------------------------------------ segments and events

    /** A repeat of a stored segment (same trip and t_start) is skipped. */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertSegment(segment: SegmentEntity)

    @Query("SELECT * FROM segments WHERE uploaded = 0 ORDER BY tripId, tStart LIMIT :limit")
    suspend fun segmentsNotUploaded(limit: Int): List<SegmentEntity>

    @Query("UPDATE segments SET uploaded = 1 WHERE tripId = :tripId AND tStart IN (:tStarts)")
    suspend fun markSegmentsUploaded(tripId: String, tStarts: List<Long>)

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertEvent(event: EventEntity)

    /**
     * Sets a user label on an event that hasn't been uploaded yet. [label] is an EventClass name.
     * Returns 0 if the event was already uploaded (the caller then saves a separate labeled event).
     */
    @Query("UPDATE events SET userLabel = :label WHERE id = :id AND uploaded = 0")
    suspend fun labelEvent(id: String, label: String): Int

    @Query("SELECT * FROM events WHERE uploaded = 0 ORDER BY tripId, t LIMIT :limit")
    suspend fun eventsNotUploaded(limit: Int): List<EventEntity>

    @Query("UPDATE events SET uploaded = 1 WHERE id IN (:ids)")
    suspend fun markEventsUploaded(ids: List<String>)

    /** Segments and events still waiting to be uploaded, for the Drive screen. */
    @Query(
        "SELECT (SELECT COUNT(*) FROM segments WHERE uploaded = 0) + " +
            "(SELECT COUNT(*) FROM events WHERE uploaded = 0)"
    )
    fun countNotUploaded(): Flow<Int>

    // ------------------------------------------------------------ raw data (kept on the phone)

    @Insert
    suspend fun insertFix(fix: GpsFixEntity)

    @Insert
    suspend fun insertMessage(message: DeviceMessageEntity)
}
