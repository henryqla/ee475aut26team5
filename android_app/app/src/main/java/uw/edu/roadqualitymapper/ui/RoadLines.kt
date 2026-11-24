package uw.edu.roadqualitymapper.ui

import com.google.android.gms.maps.model.LatLng
import uw.edu.roadqualitymapper.data.PathPoint
import uw.edu.roadqualitymapper.data.RoadPiece
import kotlin.math.floor

/**
 * The Road quality map gets its roads as short pieces (15 m when zoomed in), thousands on screen.
 * Drawn as one Google Maps line each, the map has to redraw thousands of lines on every frame of
 * a pinch or pan, which makes it lag. So pieces that follow on from each other (one ends where the
 * next starts, same direction of travel, drawn the same width) are joined into one line, with the
 * color changing along it. Same picture, far fewer lines.
 */

/** Pieces closer than this end to start count as touching (the server cuts them from one road). */
private const val JOIN_TOLERANCE_M = 1.0

/** Grid cells for finding touching ends: 1e-5 degrees, about 1.1 m north-south, 0.75 m east-west here. */
private const val CELLS_PER_DEGREE = 100_000.0

/** [pieces] grouped into lines, each in order along the road. Every piece is in exactly one line. */
fun joinPieces(pieces: List<RoadPiece>): List<List<RoadPiece>> {
    val drawable = pieces.filter { it.path.size >= 2 }

    // Pieces by the grid cell their path starts in, to find what follows a piece's end.
    val byStart = HashMap<Long, MutableList<RoadPiece>>()
    drawable.forEach { byStart.getOrPut(cellOf(it.path.first())) { mutableListOf() } += it }

    // What follows each piece: a piece starting where it ends, drawn the same way. At a junction,
    // the one that carries on straightest. Each piece follows at most one other.
    val next = HashMap<String, RoadPiece>()
    val hasPrevious = HashSet<String>()
    for (piece in drawable) {
        val end = piece.path.last()
        val following = cellsAround(end)
            .flatMap { byStart[it].orEmpty() }
            .filter {
                it.pieceId != piece.pieceId && it.pieceId !in hasPrevious && drawnAlike(piece, it) &&
                    metersBetween(end.toLatLng(), it.path.first().toLatLng()) <= JOIN_TOLERANCE_M
            }
            .minByOrNull { turnBetween(piece, it) }
        if (following != null) {
            next[piece.pieceId] = following
            hasPrevious += following.pieceId
        }
    }

    // Walk each line from its first piece. Then anything left is a loop with no first piece.
    val used = HashSet<String>()
    val lines = mutableListOf<List<RoadPiece>>()
    fun walkFrom(start: RoadPiece) {
        if (start.pieceId in used) return
        val line = mutableListOf<RoadPiece>()
        var piece: RoadPiece? = start
        while (piece != null && used.add(piece.pieceId)) {
            line += piece
            piece = next[piece.pieceId]
        }
        lines += line
    }
    drawable.filter { it.pieceId !in hasPrevious }.forEach(::walkFrom)
    drawable.forEach(::walkFrom)
    return lines
}

/** One line's path: the pieces' paths end to end, each joint point once. */
fun linePath(line: List<RoadPiece>): List<LatLng> =
    line.flatMapIndexed { i, piece ->
        val points = if (i == 0) piece.path else piece.path.drop(1)
        points.map { it.toLatLng() }
    }

/** Same direction setup and width, so one line can draw both. */
private fun drawnAlike(a: RoadPiece, b: RoadPiece) =
    a.kind == b.kind && a.lanesDir == b.lanesDir && a.oneway == b.oneway

/** How sharply the road turns from [a]'s last step into [b]'s first: 0 straight on, up to 2 for a U-turn. */
private fun turnBetween(a: RoadPiece, b: RoadPiece): Double {
    val inDirection = travelDirectionAt(a.path.takeLast(2).map { it.toLatLng() }, 1)
    val outDirection = travelDirectionAt(b.path.take(2).map { it.toLatLng() }, 0)
    return 1 - (inDirection.east * outDirection.east + inDirection.north * outDirection.north)
}

private fun PathPoint.toLatLng() = LatLng(lat, lon)

private fun cellOf(point: PathPoint): Long = cellKey(cellIndex(point.lat), cellIndex(point.lon))

/** The cell [point] is in and the 8 around it, so ends just across a cell edge still meet. */
private fun cellsAround(point: PathPoint): List<Long> {
    val row = cellIndex(point.lat)
    val column = cellIndex(point.lon)
    return (-1..1).flatMap { dRow -> (-1..1).map { dColumn -> cellKey(row + dRow, column + dColumn) } }
}

private fun cellIndex(degrees: Double): Int = floor(degrees * CELLS_PER_DEGREE).toInt()

private fun cellKey(row: Int, column: Int): Long = (row.toLong() shl 32) or (column.toLong() and 0xFFFF_FFFFL)
