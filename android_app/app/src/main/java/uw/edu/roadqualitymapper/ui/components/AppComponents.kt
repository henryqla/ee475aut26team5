package uw.edu.roadqualitymapper.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

// Small building blocks shared by the screens, so they all look the same.

val CardShape = RoundedCornerShape(16.dp)

/** A white rounded card with a hairline border: the basic container on every screen. */
@Composable
fun AppCard(
    modifier: Modifier = Modifier,
    contentPadding: PaddingValues = PaddingValues(horizontal = 16.dp, vertical = 14.dp),
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = CardShape,
        color = MaterialTheme.colorScheme.surfaceContainerLowest,
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(Modifier.padding(contentPadding), verticalArrangement = Arrangement.spacedBy(10.dp), content = content)
    }
}

/** The small gray heading at the top of a card, like "Device" or "Vehicle". */
@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        modifier = modifier,
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** One of our icons (res/drawable/ic_*.xml), in the current content color unless [tint] is given. */
@Composable
fun AppIcon(
    @DrawableRes id: Int,
    contentDescription: String?,
    modifier: Modifier = Modifier,
    size: Dp = 24.dp,
    tint: Color = LocalContentColor.current,
) {
    Icon(painterResource(id), contentDescription, modifier.size(size), tint = tint)
}

/** A small rounded label, optionally with a colored dot in front (e.g. "● Connected"). */
@Composable
fun StatusChip(
    text: String,
    container: Color,
    content: Color,
    modifier: Modifier = Modifier,
    dot: Color? = null,
    @DrawableRes icon: Int? = null,
) {
    Row(
        modifier
            .background(container, RoundedCornerShape(50))
            .padding(horizontal = 10.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        dot?.let { StatusDot(it) }
        icon?.let { AppIcon(it, null, size = 14.dp, tint = content) }
        Text(text, style = MaterialTheme.typography.labelMedium, color = content)
    }
}

@Composable
fun StatusDot(color: Color, size: Dp = 8.dp) {
    Box(Modifier.size(size).background(color, CircleShape))
}

/** A neutral gray chip, e.g. "Firmware 0.2.0". */
@Composable
fun NeutralChip(text: String) = StatusChip(
    text = text,
    container = MaterialTheme.colorScheme.surfaceVariant,
    content = MaterialTheme.colorScheme.onSurfaceVariant,
)

/** A big number with a label above it, e.g. "Segments / 742". */
@Composable
fun StatTile(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    unit: String? = null,
    swatch: Color? = null,
    note: String? = null,
) {
    AppCard(modifier, contentPadding = PaddingValues(horizontal = 14.dp, vertical = 10.dp)) {
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            SectionLabel(label)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                swatch?.let { Box(Modifier.size(12.dp).background(it, RoundedCornerShape(3.dp))) }
                Text(value, style = MaterialTheme.typography.headlineSmall)
                unit?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            note?.let {
                Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
