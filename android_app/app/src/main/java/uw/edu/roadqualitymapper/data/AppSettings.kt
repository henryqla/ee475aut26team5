package uw.edu.roadqualitymapper.data

import android.content.Context
import androidx.core.content.edit
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Which colors the map uses, like the color-blind modes in games. OFF keeps the original colors. */
enum class ColorMode(val label: String) {
    OFF("Off"),
    PROTANOPIA("Protanopia (red-weak)"),
    DEUTERANOPIA("Deuteranopia (green-weak)"),
    TRITANOPIA("Tritanopia (blue-weak)"),
}

/** Choices made on the Settings screen, remembered between launches. */
object AppSettings {
    private const val PREFS_NAME = "settings"
    private const val KEY_COLOR_MODE = "color_mode"

    private val _colorMode = MutableStateFlow(ColorMode.OFF)
    val colorMode: StateFlow<ColorMode> = _colorMode.asStateFlow()

    /** Call once at app start. */
    fun load(context: Context) {
        val saved = prefs(context).getString(KEY_COLOR_MODE, null)
        _colorMode.value = ColorMode.entries.firstOrNull { it.name == saved } ?: ColorMode.OFF
    }

    fun setColorMode(context: Context, mode: ColorMode) {
        prefs(context).edit { putString(KEY_COLOR_MODE, mode.name) }
        _colorMode.value = mode
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
