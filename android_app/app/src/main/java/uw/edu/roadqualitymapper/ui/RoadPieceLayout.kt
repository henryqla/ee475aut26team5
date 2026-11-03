package uw.edu.roadqualitymapper.ui

import com.google.android.gms.maps.model.LatLng
import uw.edu.roadqualitymapper.data.RoadPiece
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.floor

private const val METERS_PER_DEGREE_LAT = 111_320.0

/** How far a piece may be off from where the next one along the road should be, sideways or along. */
private const val JOIN_TOLERANCE_M = 5.0

/** Pieces whose directions differ by more than this aren't the same road and direction. */
private const val MAX_JOIN_HEADING_DIFF_DEG = 45.0

/**
 * A road piece ready to draw: a line from [start] through [middle] to [end], with the roughness to
 * color each point. Where two pieces meet along a road, both use the average of their roughness
 * there, so the color fades from one piece into the next with no visible seam.
 */
class DrawnPiece(
    val piece: RoadPiece,
    val start: LatLng,
    val middle: LatLng,
    val end: LatLng,
    val startRoughness: Double,
    val endRoughness: Double,
)

/**
 * Places each piece on its direction's side of the road ([offsetM] to the right of travel) and
 * finds, for each piece, the next piece along the same road in the same direction.
 * The pieces come from the database in no particular order, so neighbors are found by position:
 * the next piece's middle is ahead, about half of both lengths away, and roughly in line.
 */
fun layOutPieces(pieces: List<RoadPiece>, offsetM: Double): List<DrawnPiece> {
    if (pieces.isEmpty()) return emptyList()
    val directions = pieces.map { headingDirection(it.headingDeg) }
    val middles = pieces.mapIndexed { i, p -> shiftRight(LatLng(p.lat, p.lon), directions[i], offsetM) }
    val startRoughness = DoubleArray(pieces.size) { pieces[it].roughness }
    val endRoughness = DoubleArray(pieces.size) { pieces[it].roughness }

    // Pieces sorted into a coarse grid by their middle, so each piece only checks those nearby.
    val gridM = pieces.maxOf { it.lengthM } + JOIN_TOLERANCE_M
    val cosLat = cos(Math.toRadians(pieces.first().lat))
    fun cellOf(point: LatLng) = Pair(
        floor(point.latitude * METERS_PER_DEGREE_LAT / gridM).toLong(),
        floor(point.longitude * METERS_PER_DEGREE_LAT * cosLat / gridM).toLong(),
    )
    val grid = HashMap<Pair<Long, Long>, MutableList<Int>>()
    middles.forEachIndexed { i, middle -> grid.getOrPut(cellOf(middle)) { mutableListOf() } += i }

    pieces.indices.forEach { i ->
        val (cellY, cellX) = cellOf(middles[i])
        var next = -1
        var nextAhead = Double.MAX_VALUE
        for (dy in -1L..1L) for (dx in -1L..1L) {
            grid[Pair(cellY + dy, cellX + dx)]?.forEach { j ->
                if (j == i || headingDiff(pieces[i].headingDeg, pieces[j].headingDeg) > MAX_JOIN_HEADING_DIFF_DEG) return@forEach
                // Where j's middle is, in meters ahead of and to the side of i's middle.
                val north = (middles[j].latitude - middles[i].latitude) * METERS_PER_DEGREE_LAT
                val east = (middles[j].longitude - middles[i].longitude) * METERS_PER_DEGREE_LAT * cosLat
                val ahead = east * directions[i].east + north * directions[i].north
                val side = abs(east * directions[i].north - north * directions[i].east)
                val expected = (pieces[i].lengthM + pieces[j].lengthM) / 2
                if (ahead > 0 && ahead <= expected + JOIN_TOLERANCE_M && side <= JOIN_TOLERANCE_M && ahead < nextAhead) {
                    next = j
                    nextAhead = ahead
                }
            }
        }
        if (next >= 0) {
            val shared = (pieces[i].roughness + pieces[next].roughness) / 2
            endRoughness[i] = shared
            startRoughness[next] = shared
        }
    }

    return pieces.mapIndexed { i, p ->
        DrawnPiece(
            piece = p,
            start = moveAlong(middles[i], directions[i], -p.lengthM / 2),
            middle = middles[i],
            end = moveAlong(middles[i], directions[i], p.lengthM / 2),
            startRoughness = startRoughness[i],
            endRoughness = endRoughness[i],
        )
    }
}

/** Difference between two compass headings, the short way round (0–180°). */
private fun headingDiff(a: Double, b: Double): Double {
    val d = abs(a - b) % 360
    return if (d > 180) 360 - d else d
}
