package uw.edu.roadqualitymapper.ui

import uw.edu.roadqualitymapper.data.BoundingBox
import uw.edu.roadqualitymapper.data.EventSpot
import uw.edu.roadqualitymapper.data.RoadPiece
import kotlin.math.PI
import kotlin.math.atan
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.sinh
import kotlin.math.tan

/**
 * The Road quality map loads its data in tiles: a fixed grid of squares, the same grid web maps
 * use (Google's own map tiles too). The screen's tiles load first, side by side, then a ring of
 * tiles around them, so panning usually finds the next tiles ready. Loaded tiles are kept while the
 * app is open, so panning or zooming back is instant.
 */

/**
 * What the map asks the server for at one range of zoom levels: the road piece length, the tile
 * size (as a web-map tile zoom: each step down doubles the tile's width), and whether pothole and
 * speed bump pins are loaded. Tiles are about one to two phone screens across.
 */
data class ZoomBand(val pieceM: Int, val tileZoom: Int, val withEvents: Boolean)

/**
 * Longer pieces when zoomed out, so a big area loads fewer rows (3000 m is the server's longest).
 * Pins only from zoom 13 in (MIN_EVENT_MARKER_ZOOM in MapScreen): further out they'd pile up.
 */
fun zoomBand(zoom: Float): ZoomBand = when {
    zoom >= 15f -> ZoomBand(pieceM = 15, tileZoom = 14, withEvents = true)     // tiles ≈ 1.7 km
    zoom >= 14f -> ZoomBand(pieceM = 60, tileZoom = 13, withEvents = true)     // ≈ 3.3 km
    zoom >= 13f -> ZoomBand(pieceM = 240, tileZoom = 12, withEvents = true)    // ≈ 6.6 km
    zoom >= 11f -> ZoomBand(pieceM = 960, tileZoom = 10, withEvents = false)   // ≈ 26 km
    zoom >= 8f -> ZoomBand(pieceM = 3000, tileZoom = 7, withEvents = false)    // ≈ 210 km
    else -> ZoomBand(pieceM = 3000, tileZoom = max(0, floor(zoom).toInt() - 1), withEvents = false)
}

/** One square of the grid, for one zoom band: web-map tile [x], [y] at the band's tile zoom. */
data class RoadTile(val band: ZoomBand, val x: Int, val y: Int) {
    val box: BoundingBox
        get() {
            val n = 1 shl band.tileZoom
            return BoundingBox(
                south = tileLatitude(y + 1, n),
                west = x * 360.0 / n - 180.0,
                north = tileLatitude(y, n),
                east = (x + 1) * 360.0 / n - 180.0,
            )
        }
}

/** What the server sent for one tile. */
class TileData(val pieces: List<RoadPiece>, val spots: List<EventSpot>)

/** Tiles loaded while the app is open. Only touched from the main thread. */
object RoadQualityTiles {
    /** Enough for a lot of panning; the least recently used go first. */
    private const val MAX_TILES = 400

    val loaded: MutableMap<RoadTile, TileData> = object : LinkedHashMap<RoadTile, TileData>(64, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<RoadTile, TileData>?) = size > MAX_TILES
    }
}

/** The tiles that [box] touches, plus [ring] more tiles on every side. */
fun tilesCovering(box: BoundingBox, band: ZoomBand, ring: Int = 0): List<RoadTile> {
    val n = 1 shl band.tileZoom
    fun column(lon: Double) = floor((lon.coerceIn(-180.0, 180.0) + 180.0) / 360.0 * n).toInt()
    fun row(lat: Double): Int {
        val radians = Math.toRadians(lat.coerceIn(-MAX_TILE_LATITUDE, MAX_TILE_LATITUDE))
        return floor((1 - ln(tan(radians) + 1 / cos(radians)) / PI) / 2 * n).toInt()
    }
    val xs = (column(box.west) - ring).coerceAtLeast(0)..(column(box.east) + ring).coerceAtMost(n - 1)
    val ys = (row(box.north) - ring).coerceAtLeast(0)..(row(box.south) + ring).coerceAtMost(n - 1)
    return xs.flatMap { x -> ys.map { y -> RoadTile(band, x, y) } }
}

/** Web-map tiles stop here, where the square map ends. */
private const val MAX_TILE_LATITUDE = 85.0511

private fun tileLatitude(y: Int, n: Int): Double = Math.toDegrees(atan(sinh(PI * (1 - 2.0 * y / n))))
