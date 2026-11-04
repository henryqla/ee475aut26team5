package uw.edu.roadqualitymapper.service

import uw.edu.roadqualitymapper.data.GpsFix
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin

/** A fix further than this from a message's time doesn't count (contract §6). */
const val MAX_FIX_GAP_MS = 2_000L

/** Older fixes are dropped; messages never wait this long. */
private const val KEEP_FIXES_MS = 30_000L

/**
 * The last few seconds of GPS fixes, and where the phone was at any moment in between
 * (contract §6: interpolate between the fixes before and after).
 */
internal class FixHistory {
    private val fixes = ArrayDeque<GpsFix>()   // oldest first

    val newestTime: Long? get() = fixes.lastOrNull()?.t

    fun add(fix: GpsFix) {
        if (fix.t <= (newestTime ?: Long.MIN_VALUE)) return   // out of order or a repeat
        fixes.addLast(fix)
        while (fix.t - fixes.first().t > KEEP_FIXES_MS) fixes.removeFirst()
    }

    /** Where the phone was at [t], or null if no fix is within [MAX_FIX_GAP_MS] of it. */
    fun at(t: Long): GpsFix? {
        val before = fixes.lastOrNull { it.t <= t }?.takeIf { t - it.t <= MAX_FIX_GAP_MS }
        val after = fixes.firstOrNull { it.t >= t }?.takeIf { it.t - t <= MAX_FIX_GAP_MS }
        return when {
            before != null && after != null -> interpolate(before, after, t)
            else -> before ?: after
        }
    }

    private fun interpolate(a: GpsFix, b: GpsFix, t: Long): GpsFix {
        if (b.t == a.t) return a
        val f = (t - a.t).toDouble() / (b.t - a.t)
        return GpsFix(
            t = t,
            lat = a.lat + (b.lat - a.lat) * f,
            lon = a.lon + (b.lon - a.lon) * f,
            speedMps = lerpOrEither(a.speedMps, b.speedMps, f),
            headingDeg = if (a.headingDeg != null && b.headingDeg != null) {
                lerpAngle(a.headingDeg, b.headingDeg, f)
            } else {
                // Moving too slowly for a heading at one end: keep the nearer one's (maybe null).
                if (f < 0.5) a.headingDeg else b.headingDeg
            },
            accuracyM = if (a.accuracyM != null && b.accuracyM != null) max(a.accuracyM, b.accuracyM) else a.accuracyM ?: b.accuracyM,
        )
    }

    private fun lerpOrEither(a: Double?, b: Double?, f: Double): Double? =
        if (a != null && b != null) a + (b - a) * f else a ?: b

    /** Blends two compass headings the short way round, so 350° and 10° give 0°, not 180°. */
    private fun lerpAngle(a: Double, b: Double, f: Double): Double {
        val ra = Math.toRadians(a)
        val rb = Math.toRadians(b)
        val x = cos(ra) * (1 - f) + cos(rb) * f
        val y = sin(ra) * (1 - f) + sin(rb) * f
        return (Math.toDegrees(atan2(y, x)) + 360) % 360
    }
}
