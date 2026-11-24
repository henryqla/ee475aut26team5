package uw.edu.roadqualitymapper.data

import kotlin.math.pow
import kotlin.math.sqrt

/**
 * The same road reads rougher the faster you drive it. Roughness shown in the app is "what this
 * stretch would read at 25 mph", so roads driven at different speeds can be compared and colored
 * on one scale. Keep in sync with data contract §8 (the map's SQL functions use the same formula).
 *
 * Model, from the Nov 22 2026 speed test (Escape, 21 passes of one straight road at 10–50 mph):
 * road roughness = a × speed^b with b = 0.56 (R² 0.84 over the passes). Close to the textbook
 * random-road model's √speed. Only tested on a smooth road; assumed to hold on rough ones too.
 */
object SpeedAdjustment {
    /** What the device reads with the car still and the engine running (engine and sensor noise). */
    const val NOISE_FLOOR_G = 0.015

    /** b in roughness ∝ speed^b. */
    const val SPEED_EXPONENT = 0.56

    /** 25 mph: the speed all roughness is scaled to. */
    const val REFERENCE_SPEED_MPS = 11.176

    /** 10 mph: slower speeds count as this, since the test didn't go lower (the correction blows up near 0). */
    const val MIN_SPEED_MPS = 4.4704
}

/**
 * [rmsZ] (g, as the device sends it) scaled to 25 mph. The noise floor is removed first (RMS values
 * from independent sources add as squares), then the rest is scaled along the speed curve.
 * With no speed (no GPS fix), only the noise floor is removed.
 */
fun speedAdjustedRoughness(rmsZ: Double, speedMps: Double?): Double {
    val road = sqrt((rmsZ * rmsZ - SpeedAdjustment.NOISE_FLOOR_G * SpeedAdjustment.NOISE_FLOOR_G).coerceAtLeast(0.0))
    if (speedMps == null) return road
    val speed = speedMps.coerceAtLeast(SpeedAdjustment.MIN_SPEED_MPS)
    return road * (SpeedAdjustment.REFERENCE_SPEED_MPS / speed).pow(SpeedAdjustment.SPEED_EXPONENT)
}
