package uw.edu.roadqualitymapper.ui

import androidx.compose.ui.graphics.Color
import com.google.android.gms.maps.model.BitmapDescriptorFactory
import uw.edu.roadqualitymapper.data.ColorMode
import uw.edu.roadqualitymapper.data.EventClass

/**
 * Roughness anchors, in g (RMS vertical acceleration), like Google Maps traffic colors.
 * Set from the first clean drive (Nov 2026, board on the dashboard, no speed normalization):
 * slow driving read about 0.04 g, and a paved bridge at ~30 mph 0.10-0.25 g, which should be
 * light red at most. To be retuned after speed normalization and the calibration drives (new
 * pavement = SMOOTH, I-5 = I5). Keep in sync with data contract §8 so the web map matches.
 */
object RoughnessScale {
    const val SMOOTH = 0.05
    const val MODERATE = 0.12
    const val I5 = 0.25
    const val VERY_ROUGH = 0.50
}

/** Red-green palettes add a light blue halfway between smooth and moderate. */
private const val SMOOTH_TO_MODERATE_MIDPOINT = (RoughnessScale.SMOOTH + RoughnessScale.MODERATE) / 2

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

/**
 * The road color for [roughness] in the chosen color mode, on a light or [dark] map.
 * On a light map the rough end gets darker; on a dark map, where dark red would disappear, it
 * gets brighter and more vivid instead. Same order of colors either way.
 */
fun roughnessColor(roughness: Double, mode: ColorMode, dark: Boolean): Color = when (mode) {
    ColorMode.OFF -> if (dark) blendStops(roughness, StandardDarkMapStops) else standardColor(roughness)
    ColorMode.RED_GREEN -> blendStops(roughness, if (dark) RedGreenSafeDarkMapStops else RedGreenSafeStops)
    ColorMode.BLUE_YELLOW -> blendStops(roughness, if (dark) BlueYellowSafeDarkMapStops else BlueYellowSafeStops)
}

/** Dark map, color-blind mode off: green, yellow, light red, then vivid red. */
private val StandardDarkMapStops = listOf(
    RoughnessScale.SMOOTH to Color(0xFF34C77B),       // green
    RoughnessScale.MODERATE to Color(0xFFFFD54A),     // yellow
    RoughnessScale.I5 to Color(0xFFFF7A6B),           // light red
    RoughnessScale.VERY_ROUGH to Color(0xFFFF2D55),   // vivid red
)

/** Dark map, red-green: lighter blues than on a light map, ending in a bright red-orange. */
private val RedGreenSafeDarkMapStops = listOf(
    RoughnessScale.SMOOTH to Color(0xFF6FA8F5),       // blue
    SMOOTH_TO_MODERATE_MIDPOINT to Color(0xFFA7D3F0),                       // light blue
    RoughnessScale.MODERATE to Color(0xFFFFD54A),     // yellow
    RoughnessScale.I5 to Color(0xFFFF9E40),           // orange
    RoughnessScale.VERY_ROUGH to Color(0xFFFF5A36),   // red-orange
)

/** Dark map, blue-yellow: green, salmon, red, then vivid red. */
private val BlueYellowSafeDarkMapStops = listOf(
    RoughnessScale.SMOOTH to Color(0xFF2EC27E),       // green
    RoughnessScale.MODERATE to Color(0xFFF7B09A),     // salmon
    RoughnessScale.I5 to Color(0xFFF0605A),           // red
    RoughnessScale.VERY_ROUGH to Color(0xFFFF2A55),   // vivid red
)

/**
 * For red-green color blindness (protanopia and deuteranopia both): blue for smooth, through
 * yellow, to dark red. Blue and yellow stay distinct for these viewers, and the rough end also
 * gets darker, so the order shows even where hues look alike.
 */
private val RedGreenSafeStops = listOf(
    RoughnessScale.SMOOTH to Color(0xFF4575B4),       // blue
    SMOOTH_TO_MODERATE_MIDPOINT to Color(0xFF74ADD1),                       // light blue
    RoughnessScale.MODERATE to Color(0xFFF5C518),     // yellow
    RoughnessScale.I5 to Color(0xFFF46D43),           // orange-red
    RoughnessScale.VERY_ROUGH to Color(0xFFA50026),   // dark red
)

/**
 * For blue-yellow color blindness (tritanopia): green for smooth, skipping yellow (which these
 * viewers see as pale pink), through salmon and red, to dark red.
 */
private val BlueYellowSafeStops = listOf(
    RoughnessScale.SMOOTH to Color(0xFF1A9850),       // green
    RoughnessScale.MODERATE to Color(0xFFF4A582),     // salmon
    RoughnessScale.I5 to Color(0xFFD6604D),           // red
    RoughnessScale.VERY_ROUGH to Color(0xFF67001F),   // dark red
)

/** Straight blend between the two stops around [roughness]; flat past the first and last. */
private fun blendStops(roughness: Double, stops: List<Pair<Double, Color>>): Color {
    if (roughness <= stops.first().first) return stops.first().second
    for (i in 1 until stops.size) {
        val (to, toColor) = stops[i]
        if (roughness <= to) {
            val (from, fromColor) = stops[i - 1]
            val t = fraction(roughness, from, to)
            return Color(
                red = mix(fromColor.red, toColor.red, t),
                green = mix(fromColor.green, toColor.green, t),
                blue = mix(fromColor.blue, toColor.blue, t),
            )
        }
    }
    return stops.last().second
}

/** The original scale: blends between the anchor colors, so values between anchors get an in-between color. */
private fun standardColor(roughness: Double): Color = when {
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

/**
 * Marker pin color for an event, in the chosen color mode. Off: violet pothole, blue speed bump.
 * Red-green modes: orange pothole instead, since violet and blue look alike to these viewers.
 * Tritanopia keeps violet and blue, which these viewers see as pinkish and teal.
 */
fun eventMarkerHue(eventClass: EventClass, mode: ColorMode): Float = when (eventClass) {
    EventClass.POTHOLE -> when (mode) {
        ColorMode.RED_GREEN -> BitmapDescriptorFactory.HUE_ORANGE
        else -> BitmapDescriptorFactory.HUE_VIOLET
    }
    EventClass.SPEED_BUMP -> BitmapDescriptorFactory.HUE_AZURE
    EventClass.UNKNOWN -> BitmapDescriptorFactory.HUE_YELLOW
}

private fun mix(from: Float, to: Float, t: Float): Float = from + (to - from) * t

private fun fraction(value: Double, from: Double, to: Double): Float =
    ((value - from) / (to - from)).toFloat()
