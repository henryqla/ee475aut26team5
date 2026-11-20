package uw.edu.roadqualitymapper.data

import android.content.Context
import androidx.core.content.edit
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Which colors the map uses for color-blind viewers. OFF keeps the original colors. */
enum class ColorMode(val label: String, val detail: String?) {
    OFF("Off", null),
    RED_GREEN("Red-green", "Protanopia, deuteranopia"),
    BLUE_YELLOW("Blue-yellow", "Tritanopia"),
}

/** Light or dark app and map. Until one is picked, the app follows the phone's dark mode. */
enum class ThemeChoice(val label: String) {
    LIGHT("Light"),
    DARK("Dark"),
}

/** Choices made on the Settings screen, remembered between launches. */
object AppSettings {
    private const val PREFS_NAME = "settings"
    private const val KEY_COLOR_MODE = "color_mode"
    private const val KEY_KEEP_SCREEN_ON = "keep_screen_on"
    private const val KEY_THEME = "theme"

    private val _colorMode = MutableStateFlow(ColorMode.OFF)
    val colorMode: StateFlow<ColorMode> = _colorMode.asStateFlow()

    /** null until picked in Settings: follow the phone's dark mode. */
    private val _theme = MutableStateFlow<ThemeChoice?>(null)
    val theme: StateFlow<ThemeChoice?> = _theme.asStateFlow()

    /** Keep the display awake while a trip is recording. */
    private val _keepScreenOn = MutableStateFlow(true)
    val keepScreenOn: StateFlow<Boolean> = _keepScreenOn.asStateFlow()

    /** Call once at app start. */
    fun load(context: Context) {
        val prefs = prefs(context)
        _colorMode.value = when (val saved = prefs.getString(KEY_COLOR_MODE, null)) {
            // Saved by older versions, which had a separate mode per type.
            "PROTANOPIA", "DEUTERANOPIA" -> ColorMode.RED_GREEN
            "TRITANOPIA" -> ColorMode.BLUE_YELLOW
            else -> ColorMode.entries.firstOrNull { it.name == saved } ?: ColorMode.OFF
        }
        _keepScreenOn.value = prefs.getBoolean(KEY_KEEP_SCREEN_ON, true)
        _theme.value = prefs.getString(KEY_THEME, null)?.let { saved -> ThemeChoice.entries.firstOrNull { it.name == saved } }
    }

    fun setTheme(context: Context, choice: ThemeChoice) {
        prefs(context).edit { putString(KEY_THEME, choice.name) }
        _theme.value = choice
    }

    fun setColorMode(context: Context, mode: ColorMode) {
        prefs(context).edit { putString(KEY_COLOR_MODE, mode.name) }
        _colorMode.value = mode
    }

    fun setKeepScreenOn(context: Context, keepOn: Boolean) {
        prefs(context).edit { putBoolean(KEY_KEEP_SCREEN_ON, keepOn) }
        _keepScreenOn.value = keepOn
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
