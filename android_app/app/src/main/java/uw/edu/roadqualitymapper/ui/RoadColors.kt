package uw.edu.roadqualitymapper.ui

import androidx.compose.ui.graphics.Color

/**
 * Roughness anchors, in g (RMS vertical acceleration), like Google Maps traffic colors.
 * Placeholders until the calibration drives: new pavement should come out at SMOOTH (green)
 * and I-5 at I5 (light red). Keep in sync with the data contract so the web map matches.
 */
object RoughnessScale {
    const val SMOOTH = 0.03
    const val MODERATE = 0.06
    const val I5 = 0.10
    const val VERY_ROUGH = 0.20
}

/**
 * A color as hue (degrees), saturation, and value (brightness), all 0–1 except hue.
 * Blending these one at a time walks around the color wheel (green → yellow-green → yellow →
 * orange → red), so in-between colors stay bright instead of turning olive or brown.
 */
private class HsvColor(val hue: Float, val saturation: Float, val value: Float)

private val SmoothGreen = HsvColor(hue = 141f, saturation = 0.81f, value = 0.62f)
private val ModerateYellow = HsvColor(hue = 47f, saturation = 0.90f, value = 0.96f)
private val I5LightRed = HsvColor(hue = 3f, saturation = 0.74f, value = 0.91f)
private val VeryRoughDarkRed = HsvColor(hue = 0f, saturation = 0.92f, value = 0.48f)

/** Blends between the anchor colors, so values between anchors get an in-between color. */
fun roughnessColor(roughness: Double): Color = when {
    roughness <= RoughnessScale.SMOOTH -> SmoothGreen.toColor()
    roughness <= RoughnessScale.MODERATE ->
        blend(SmoothGreen, ModerateYellow, fraction(roughness, RoughnessScale.SMOOTH, RoughnessScale.MODERATE))
    roughness <= RoughnessScale.I5 ->
        blend(ModerateYellow, I5LightRed, fraction(roughness, RoughnessScale.MODERATE, RoughnessScale.I5))
    roughness <= RoughnessScale.VERY_ROUGH ->
        blend(I5LightRed, VeryRoughDarkRed, fraction(roughness, RoughnessScale.I5, RoughnessScale.VERY_ROUGH))
    else -> VeryRoughDarkRed.toColor()
}

// Anchor hues only ever decrease (141 → 47 → 3 → 0), so a straight blend never wraps past 0°/360°.
private fun blend(from: HsvColor, to: HsvColor, t: Float): Color = Color.hsv(
    hue = mix(from.hue, to.hue, t),
    saturation = mix(from.saturation, to.saturation, t),
    value = mix(from.value, to.value, t),
)

private fun HsvColor.toColor(): Color = Color.hsv(hue, saturation, value)

private fun mix(from: Float, to: Float, t: Float): Float = from + (to - from) * t

private fun fraction(value: Double, from: Double, to: Double): Float =
    ((value - from) / (to - from)).toFloat()
