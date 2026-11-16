package uw.edu.roadqualitymapper.ui.screens

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import uw.edu.roadqualitymapper.data.AppSettings
import uw.edu.roadqualitymapper.data.ColorMode
import uw.edu.roadqualitymapper.ui.ScreenTitle

/** Opened from the gear on the Connect tab. Back (the arrow or the phone's back) returns there. */
@Composable
fun SettingsScreen(onBack: () -> Unit, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val colorMode by AppSettings.colorMode.collectAsState()
    BackHandler(onBack = onBack)

    Column(
        modifier
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Box(Modifier.fillMaxWidth()) {
            ScreenTitle("Settings")
            IconButton(onClick = onBack, modifier = Modifier.align(Alignment.CenterStart)) {
                Text("←", style = MaterialTheme.typography.headlineSmall)
            }
        }

        Text("Color-blind mode", style = MaterialTheme.typography.titleMedium)
        ColorMode.entries.forEach { mode ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .selectable(
                        selected = colorMode == mode,
                        onClick = { AppSettings.setColorMode(context, mode) },
                    ),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = colorMode == mode, onClick = null)
                Text(mode.label, Modifier.padding(start = 8.dp))
            }
        }
    }
}
