package uw.edu.roadqualitymapper.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import uw.edu.roadqualitymapper.R

// Space Grotesk for titles and big numbers, Inter for everything else. Both are free Google
// Fonts (SIL Open Font License), bundled in res/font so they work offline. Each file holds every
// weight (a variable font); these pick the weights we use.

@OptIn(ExperimentalTextApi::class)
private fun variableFont(resId: Int, weight: FontWeight) = Font(
    resId,
    weight,
    variationSettings = FontVariation.Settings(FontVariation.weight(weight.weight)),
)

val SpaceGrotesk = FontFamily(
    variableFont(R.font.space_grotesk, FontWeight.Medium),
    variableFont(R.font.space_grotesk, FontWeight.SemiBold),
)

val Inter = FontFamily(
    variableFont(R.font.inter, FontWeight.Normal),
    variableFont(R.font.inter, FontWeight.Medium),
    variableFont(R.font.inter, FontWeight.SemiBold),
)

private val base = Typography()

private fun TextStyle.title() = copy(fontFamily = SpaceGrotesk, fontWeight = FontWeight.SemiBold)
private fun TextStyle.body() = copy(fontFamily = Inter)

val Typography = Typography(
    displayLarge = base.displayLarge.title(),
    displayMedium = base.displayMedium.title(),
    displaySmall = base.displaySmall.title(),
    headlineLarge = base.headlineLarge.title(),
    headlineMedium = base.headlineMedium.title(),
    headlineSmall = base.headlineSmall.title(),
    titleLarge = base.titleLarge.title().copy(fontSize = 22.sp, letterSpacing = (-0.3).sp),
    titleMedium = base.titleMedium.body().copy(fontWeight = FontWeight.SemiBold),
    titleSmall = base.titleSmall.body().copy(fontWeight = FontWeight.Medium),
    bodyLarge = base.bodyLarge.body(),
    bodyMedium = base.bodyMedium.body(),
    bodySmall = base.bodySmall.body(),
    labelLarge = base.labelLarge.body().copy(fontWeight = FontWeight.Medium),
    labelMedium = base.labelMedium.body().copy(fontWeight = FontWeight.Medium),
    labelSmall = base.labelSmall.body().copy(fontWeight = FontWeight.Medium),
)
