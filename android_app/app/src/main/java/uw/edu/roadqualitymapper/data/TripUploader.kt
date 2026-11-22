package uw.edu.roadqualitymapper.data

import android.content.Context
import android.util.Log
import io.github.jan.supabase.postgrest.exception.PostgrestRestException
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

private const val TAG = "TripUploader"

/** Postgres error code for a row that breaks one of a table's check rules. */
private const val CHECK_VIOLATION = "23514"

data class UploadStatus(
    val uploading: Boolean = false,
    val lastResult: String? = null,   // e.g. "Uploaded 120" or the error
    val failed: Boolean = false,      // the last try didn't get everything up
)

/**
 * Sends everything in the phone's database that isn't on Supabase yet. Called every minute during
 * a trip, when a trip stops, at app start, and from the Drive screen's "Upload now".
 * Safe to call any time: calls run one at a time, and re-sent rows are skipped by the server.
 */
object TripUploader {
    private val lock = Mutex()

    // Rows the server rejected even on their own. They stay on the phone (still not uploaded) and
    // are skipped until the app restarts, so one bad row can't hold up everything after it.
    private val rejectedSegments = mutableSetOf<Pair<String, Long>>()   // tripId, tStart
    private val rejectedEvents = mutableSetOf<String>()                // event id
    private var lastBrokenRule: String? = null
    private val _status = MutableStateFlow(UploadStatus())
    val status: StateFlow<UploadStatus> = _status.asStateFlow()

    suspend fun uploadPending(context: Context) {
        if (!SupabaseConnection.isConfigured) {
            _status.value = UploadStatus(lastResult = "Server not set up", failed = true)
            return
        }
        lock.withLock {
            _status.value = _status.value.copy(uploading = true)
            _status.value = try {
                val rows = upload(RoadDatabase.get(context).dao(), SupabaseTripRepository())
                val kept = rejectedSegments.size + rejectedEvents.size
                when {
                    kept > 0 -> UploadStatus(
                        lastResult = "Uploaded $rows, $kept kept on phone: broke $lastBrokenRule",
                        failed = true,
                    )
                    rows > 0 -> UploadStatus(lastResult = "Uploaded $rows")
                    else -> UploadStatus(lastResult = "All uploaded")
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Nothing is lost: rows stay marked as not uploaded and go next time.
                Log.w(TAG, "Upload failed", e)
                UploadStatus(lastResult = "Upload failed, will retry: ${e.shortMessage()}", failed = true)
            }
        }
    }

    /** Returns how many segments and events were sent. Order matters: trip rows first (contract §7). */
    private suspend fun upload(dao: RoadDao, repository: TripRepository): Int {
        dao.tripsNotUploaded().forEach { uploadTripRow(dao, repository, it) }

        var rows = 0
        while (true) {
            // Asks for extra rows to make up for the rejected ones, which are skipped.
            val batch = dao.segmentsNotUploaded(BATCH_SIZE + rejectedSegments.size)
                .filter { (it.tripId to it.tStart) !in rejectedSegments }
                .take(BATCH_SIZE)
            if (batch.isEmpty()) break
            batch.groupBy { it.tripId }.forEach { (tripId, segments) ->
                val trip = tripReadyForRows(dao, repository, tripId)
                val rejected = if (trip == null) emptyList() else sendRows(segments) { some ->
                    repository.uploadSegments(trip.toTrip(), some.map { it.toSegment() })
                }
                rejectedSegments += rejected.map { it.tripId to it.tStart }
                // Without its trip a segment can never be uploaded, so it's marked done either way.
                val done = segments - rejected.toSet()
                dao.markSegmentsUploaded(tripId, done.map { it.tStart })
                rows += done.size
            }
        }
        while (true) {
            val batch = dao.eventsNotUploaded(BATCH_SIZE + rejectedEvents.size)
                .filter { it.id !in rejectedEvents }
                .take(BATCH_SIZE)
            if (batch.isEmpty()) break
            batch.groupBy { it.tripId }.forEach { (tripId, events) ->
                val trip = tripReadyForRows(dao, repository, tripId)
                val rejected = if (trip == null) emptyList() else sendRows(events) { some ->
                    repository.uploadEvents(some.map { it.toEvent(trip) })
                }
                rejectedEvents += rejected.map { it.id }
                val done = events - rejected.toSet()
                dao.markEventsUploaded(done.map { it.id })
                rows += done.size
            }
        }

        dao.tripsWithEndNotUploaded().forEach { trip ->
            repository.endTrip(trip.id, trip.endTime!!)
            dao.markEndUploaded(trip.id)
        }
        return rows
    }

    /**
     * Sends [rows] in one request. If the server rejects it because a row breaks one of the
     * table's rules, sends the rows one at a time instead, so the good ones still get through.
     * Returns the rows it rejected even then. Any other error (no signal, say) is thrown as usual.
     */
    private suspend fun <T> sendRows(rows: List<T>, send: suspend (List<T>) -> Unit): List<T> {
        try {
            send(rows)
            return emptyList()
        } catch (e: PostgrestRestException) {
            lastBrokenRule = e.brokenRule() ?: throw e
        }
        if (rows.size == 1) return rows
        return rows.filter { row ->
            try {
                send(listOf(row))
                false
            } catch (e: PostgrestRestException) {
                lastBrokenRule = e.brokenRule() ?: throw e
                Log.w(TAG, "Row kept on the phone, it breaks $lastBrokenRule: $row")
                true
            }
        }
    }

    /** The name of the table rule a rejected row broke, or null if the error was something else. */
    private fun PostgrestRestException.brokenRule(): String? {
        if (code != CHECK_VIOLATION) return null
        return Regex("constraint \"([^\"]+)\"").find(error)?.groupValues?.get(1) ?: "a table rule"
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
