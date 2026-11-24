package uw.edu.roadqualitymapper.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

// Fixed brand colors in both modes (not the phone's wallpaper colors), so the app looks the same
// on every phone. Cards use surfaceContainerLowest; hairlines and dividers use outlineVariant.

private val LightColorScheme = lightColorScheme(
    primary = Ink,
    onPrimary = Color.White,
    primaryContainer = InkContainer,
    onPrimaryContainer = Ink,
    secondary = Ink,
    onSecondary = Color.White,
    secondaryContainer = InkContainer,
    onSecondaryContainer = Ink,
    background = PageLight,
    onBackground = TextLight,
    surface = PageLight,
    onSurface = TextLight,
    surfaceVariant = NeutralChipLight,
    onSurfaceVariant = TextMutedLight,
    surfaceContainerLowest = CardLight,
    surfaceContainerLow = CardLight,
    surfaceContainer = CardLight,
    surfaceContainerHigh = NeutralChipLight,
    surfaceContainerHighest = NeutralChipLight,
    outline = OutlineLight,
    outlineVariant = HairlineLight,
    error = ErrorLight,
    onError = Color.White,
)

private val DarkColorScheme = darkColorScheme(
    primary = Paper,
    onPrimary = Ink,
    primaryContainer = PaperContainer,
    onPrimaryContainer = Paper,
    secondary = Paper,
    onSecondary = Ink,
    secondaryContainer = PaperContainer,
    onSecondaryContainer = Paper,
    background = PageDark,
    onBackground = TextDark,
    surface = PageDark,
    onSurface = TextDark,
    surfaceVariant = NeutralChipDark,
    onSurfaceVariant = TextMutedDark,
    surfaceContainerLowest = CardDark,
    surfaceContainerLow = CardDark,
    surfaceContainer = CardDark,
    surfaceContainerHigh = NeutralChipDark,
    surfaceContainerHighest = NeutralChipDark,
    outline = OutlineDark,
    outlineVariant = HairlineDark,
    error = ErrorDark,
    onError = Color.Black,
)

/**
 * Whether the app is in dark mode (Settings > Theme, or the phone's dark mode until one is picked).
 * Use this instead of isSystemInDarkTheme(), which only knows the phone's setting.
 */
val LocalDarkTheme = staticCompositionLocalOf { false }

@Composable
fun RoadQualityMapperTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    CompositionLocalProvider(LocalDarkTheme provides darkTheme) {
        MaterialTheme(
            colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme,
            typography = Typography,
            content = content
        )
    }
}
