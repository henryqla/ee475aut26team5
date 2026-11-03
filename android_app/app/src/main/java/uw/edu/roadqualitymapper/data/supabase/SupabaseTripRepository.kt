package uw.edu.roadqualitymapper.data.supabase

import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.auth.Auth
import io.github.jan.supabase.auth.auth
import io.github.jan.supabase.createSupabaseClient
import io.github.jan.supabase.postgrest.Postgrest
import io.github.jan.supabase.postgrest.from
import io.github.jan.supabase.postgrest.postgrest
import io.github.jan.supabase.postgrest.query.Order
import io.github.jan.supabase.postgrest.query.PostgrestRequestBuilder
import io.github.jan.supabase.postgrest.rpc
import io.github.jan.supabase.serializer.KotlinXSerializer
import kotlinx.serialization.json.Json
import uw.edu.roadqualitymapper.BuildConfig
import uw.edu.roadqualitymapper.data.BoundingBox
import uw.edu.roadqualitymapper.data.DataSource
import uw.edu.roadqualitymapper.data.EventSpot
import uw.edu.roadqualitymapper.data.RoadEvent
import uw.edu.roadqualitymapper.data.RoadPiece
import uw.edu.roadqualitymapper.data.Segment
import uw.edu.roadqualitymapper.data.Trip
import uw.edu.roadqualitymapper.data.TripRepository
import uw.edu.roadqualitymapper.data.Vehicle

/** Rows per upload request (contract §6). */
private const val UPLOAD_BATCH_SIZE = 60

/** The server returns at most this many rows per request (Ben's config.toml, max_rows). */
private const val PAGE_SIZE = 1000L

/** How many trips the map loads at most, newest first. */
private const val MAX_TRIPS = 50L

/** The one Supabase connection for the whole app. URL and key come from local.properties. */
object SupabaseConnection {
    /** False while local.properties still has the placeholders from local.defaults.properties. */
    val isConfigured: Boolean =
        !BuildConfig.SUPABASE_URL.startsWith("DEFAULT_") &&
            !BuildConfig.SUPABASE_PUBLISHABLE_KEY.startsWith("DEFAULT_")

    val client: SupabaseClient by lazy {
        createSupabaseClient(BuildConfig.SUPABASE_URL, BuildConfig.SUPABASE_PUBLISHABLE_KEY) {
            // Query results include columns the app doesn't use (created_by, geog, ...); skip them.
            defaultSerializer = KotlinXSerializer(Json { ignoreUnknownKeys = true })
            install(Auth)       // keeps the anonymous session on the phone between launches
            install(Postgrest)  // reading and writing tables
        }
    }
}

class SupabaseTripRepository(
    private val client: SupabaseClient = SupabaseConnection.client,
) : TripRepository {

    override suspend fun uploadTrip(trip: Trip) {
        ensureSignedIn()
        client.from(Tables.TRIPS).upsert(trip.toRow(BuildConfig.VERSION_NAME)) {
            onConflict = Columns.TRIPS_KEY
            ignoreDuplicates = true
        }
    }

    override suspend fun uploadSegments(trip: Trip, segments: List<Segment>) {
        ensureSignedIn()
        segments.chunked(UPLOAD_BATCH_SIZE).forEach { batch ->
            client.from(Tables.SEGMENTS).upsert(batch.map { it.toRow(trip) }) {
                onConflict = Columns.SEGMENTS_KEY
                ignoreDuplicates = true
            }
        }
    }

    override suspend fun uploadEvents(events: List<RoadEvent>) {
        ensureSignedIn()
        events.chunked(UPLOAD_BATCH_SIZE).forEach { batch ->
            client.from(Tables.EVENTS).upsert(batch.map { it.toRow() }) {
                onConflict = Columns.EVENTS_KEY
                ignoreDuplicates = true
            }
        }
    }

    override suspend fun endTrip(tripId: String, endTime: Long) {
        ensureSignedIn()
        client.from(Tables.TRIPS).update({ set(Columns.END_TIME, endTime) }) {
            filter { eq(Columns.ID, tripId) }
        }
    }

    override suspend fun trips(includeFake: Boolean): List<Trip> =
        client.from(Tables.TRIPS).select {
            if (!includeFake) filter { eq(Columns.SOURCE, DataSource.REAL.name) }
            order(Columns.START_TIME, Order.DESCENDING)
            limit(MAX_TRIPS)
        }.decodeList<TripRow>().map { it.toTrip() }

    override suspend fun segmentsForTrip(tripId: String): List<Segment> =
        selectAllPages<SegmentRow>(Tables.SEGMENTS) {
            filter { eq(Columns.TRIP_ID, tripId) }
            order(Columns.T_START, Order.ASCENDING)
        }.map { it.toSegment() }

    override suspend fun eventsForTrip(tripId: String): List<RoadEvent> =
        selectAllPages<EventRow>(Tables.EVENTS) {
            filter { eq(Columns.TRIP_ID, tripId) }
            order(Columns.T, Order.ASCENDING)
        }.map { it.toEvent() }

    override suspend fun roadQualityInArea(area: BoundingBox, includeFake: Boolean, vehicleId: String?): List<RoadPiece> =
        rpcAllPages<RoadPieceRow>(Functions.ROAD_QUALITY_IN_AREA, area.toParams(includeFake, vehicleId))
            .map { it.toRoadPiece() }

    override suspend fun eventsInArea(area: BoundingBox, includeFake: Boolean, vehicleId: String?): List<EventSpot> =
        rpcAllPages<EventSpotRow>(Functions.EVENTS_IN_AREA, area.toParams(includeFake, vehicleId))
            .map { it.toEventSpot() }

    override suspend fun vehicles(): List<Vehicle> =
        client.from(Tables.VEHICLES).select().decodeList<VehicleRow>().map { it.toVehicle() }

    /**
     * Writing needs a signed-in phone. The first time, this makes an anonymous account (no
     * password, nothing for the user to do); after that, the saved session is reused.
     */
    private suspend fun ensureSignedIn() {
        val auth = client.auth
        auth.awaitInitialization()   // finish loading a saved session first
        if (auth.currentSessionOrNull() == null) auth.signInAnonymously()
    }

    /**
     * Reads every matching row, [PAGE_SIZE] at a time, since the server caps each request.
     * [query] must include an order, so pages don't overlap or skip rows.
     */
    private suspend inline fun <reified T : Any> selectAllPages(
        table: String,
        crossinline query: PostgrestRequestBuilder.() -> Unit,
    ): List<T> = readAllPages { from, to ->
        client.from(table).select {
            query()
            range(from, to)
        }.decodeList<T>()
    }

    /** Calls a database function and reads every result row. Ben's functions sort their results. */
    private suspend inline fun <reified T : Any> rpcAllPages(function: String, params: AreaParams): List<T> =
        readAllPages { from, to ->
            client.postgrest.rpc(function, params) { range(from, to) }.decodeList<T>()
        }

    /** Asks for rows [from]..[to] (inclusive) until a page comes back short. */
    private inline fun <T> readAllPages(readPage: (from: Long, to: Long) -> List<T>): List<T> {
        val rows = mutableListOf<T>()
        var from = 0L
        while (true) {
            val page = readPage(from, from + PAGE_SIZE - 1)
            rows += page
            if (page.size < PAGE_SIZE) return rows
            from += PAGE_SIZE
        }
    }
}
