package uw.edu.roadqualitymapper.data

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import uw.edu.roadqualitymapper.data.local.RoadDao
import uw.edu.roadqualitymapper.data.local.RoadDatabase
import uw.edu.roadqualitymapper.data.local.TripEntity
import uw.edu.roadqualitymapper.data.local.toEvent
import uw.edu.roadqualitymapper.data.local.toSegment
import uw.edu.roadqualitymapper.data.local.toTrip
import uw.edu.roadqualitymapper.data.supabase.SupabaseConnection
import uw.edu.roadqualitymapper.data.supabase.SupabaseTripRepository

/** Rows per upload request (contract §6). */
private const val BATCH_SIZE = 60

data class UploadStatus(
    val uploading: Boolean = false,
    val lastResult: String? = null,   // e.g. "Uploaded 120 rows at 3:41 PM" or the error
)

/**
 * Sends everything in the phone's database that isn't on Supabase yet. Called every minute during
 * a trip, when a trip stops, at app start, and from the Drive screen's "Upload now".
 * Safe to call any time: calls run one at a time, and re-sent rows are skipped by the server.
 */
object TripUploader {
    private val lock = Mutex()
    private val _status = MutableStateFlow(UploadStatus())
    val status: StateFlow<UploadStatus> = _status.asStateFlow()

    suspend fun uploadPending(context: Context) {
        if (!SupabaseConnection.isConfigured) {
            _status.value = UploadStatus(lastResult = "Supabase isn't set up in local.properties")
            return
        }
        lock.withLock {
            _status.value = _status.value.copy(uploading = true)
            val result = try {
                val rows = upload(RoadDatabase.get(context).dao(), SupabaseTripRepository())
                if (rows > 0) "Uploaded $rows row(s)" else "Everything is uploaded"
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Nothing is lost: rows stay marked as not uploaded and go next time.
                "Upload failed, will retry: ${e.message}"
            }
            _status.value = UploadStatus(uploading = false, lastResult = result)
        }
    }

    /** Returns how many segments and events were sent. Order matters: trip rows first (contract §7). */
    private suspend fun upload(dao: RoadDao, repository: TripRepository): Int {
        dao.tripsNotUploaded().forEach { uploadTripRow(dao, repository, it) }

        var rows = 0
        while (true) {
            val batch = dao.segmentsNotUploaded(BATCH_SIZE)
            if (batch.isEmpty()) break
            batch.groupBy { it.tripId }.forEach { (tripId, segments) ->
                val trip = tripReadyForRows(dao, repository, tripId)
                if (trip != null) repository.uploadSegments(trip.toTrip(), segments.map { it.toSegment() })
                // Without its trip a segment can never be uploaded, so it's marked done either way.
                dao.markSegmentsUploaded(tripId, segments.map { it.tStart })
                rows += segments.size
            }
        }
        while (true) {
            val batch = dao.eventsNotUploaded(BATCH_SIZE)
            if (batch.isEmpty()) break
            batch.groupBy { it.tripId }.forEach { (tripId, events) ->
                val trip = tripReadyForRows(dao, repository, tripId)
                if (trip != null) repository.uploadEvents(events.map { it.toEvent(trip) })
                dao.markEventsUploaded(events.map { it.id })
                rows += events.size
            }
        }

        dao.tripsWithEndNotUploaded().forEach { trip ->
            repository.endTrip(trip.id, trip.endTime!!)
            dao.markEndUploaded(trip.id)
        }
        return rows
    }

    private suspend fun uploadTripRow(dao: RoadDao, repository: TripRepository, trip: TripEntity) {
        repository.uploadTrip(trip.toTrip())
        dao.markTripUploaded(trip.id)
    }

    /** The trip, uploaded first if it started since this upload began. */
    private suspend fun tripReadyForRows(dao: RoadDao, repository: TripRepository, tripId: String): TripEntity? {
        val trip = dao.trip(tripId) ?: return null
        if (!trip.tripUploaded) uploadTripRow(dao, repository, trip)
        return trip
    }
}
