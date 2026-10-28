package uw.edu.roadqualitymapper.ui

import com.google.android.gms.maps.model.LatLng
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sqrt

// Flat-earth approximations. Fine over the few meters to few kilometers we deal with.
private const val METERS_PER_DEGREE_LAT = 111_320.0

/** Ground meters per screen dp at the equator at zoom 0 (Google Maps uses 256 dp tiles). */
private const val EQUATOR_METERS_PER_DP_AT_ZOOM_0 = 156_543.03392

/** How many screen pixels [meters] of ground covers at this zoom level and latitude. */
fun metersToPixels(meters: Double, zoom: Float, latitude: Double, density: Float): Float {
    val metersPerDp = EQUATOR_METERS_PER_DP_AT_ZOOM_0 * cos(Math.toRadians(latitude)) / 2.0.pow(zoom.toDouble())
    return (meters / metersPerDp * density).toFloat()
}

/** Unit vector of the direction of travel, in east and north components. */
data class TravelDirection(val east: Double, val north: Double)

/**
 * Direction of travel at path[i]: the average of the way in and the way out, so corners
 * get a direction halfway between the two streets. Zero if it can't be worked out.
 */
fun travelDirectionAt(path: List<LatLng>, i: Int): TravelDirection {
    val incoming = if (i > 0) step(path[i - 1], path[i]) else null
    val outgoing = if (i < path.lastIndex) step(path[i], path[i + 1]) else null
    val east = (incoming?.east ?: 0.0) + (outgoing?.east ?: 0.0)
    val north = (incoming?.north ?: 0.0) + (outgoing?.north ?: 0.0)
    val length = sqrt(east * east + north * north)
    return when {
        length > 1e-6 -> TravelDirection(east / length, north / length)
        // A U-turn: way in and way out cancel out, so use the way in.
        incoming != null -> incoming
        else -> TravelDirection(0.0, 0.0)
    }
}

/** Moves [point] [meters] to the right of [direction] (US traffic drives on the right). */
fun shiftRight(point: LatLng, direction: TravelDirection, meters: Double): LatLng {
    // Rotating (east, north) a quarter turn clockwise gives (north, -east).
    val eastMeters = direction.north * meters
    val northMeters = -direction.east * meters
    return LatLng(
        point.latitude + northMeters / METERS_PER_DEGREE_LAT,
        point.longitude + eastMeters / (METERS_PER_DEGREE_LAT * cos(Math.toRadians(point.latitude))),
    )
}

/** Shifts a whole path to the right of travel, so each direction of a road draws on its own side. */
fun shiftPathRight(path: List<LatLng>, meters: Double): List<LatLng> =
    path.indices.map { i -> shiftRight(path[i], travelDirectionAt(path, i), meters) }

/** Unit vector from [from] to [to], or null if they're the same point. */
private fun step(from: LatLng, to: LatLng): TravelDirection? {
    val north = (to.latitude - from.latitude) * METERS_PER_DEGREE_LAT
    val east = (to.longitude - from.longitude) * METERS_PER_DEGREE_LAT * cos(Math.toRadians(from.latitude))
    val length = sqrt(east * east + north * north)
    return if (length > 1e-6) TravelDirection(east / length, north / length) else null
}
