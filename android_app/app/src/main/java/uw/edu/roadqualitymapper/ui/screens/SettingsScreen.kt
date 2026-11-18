package uw.edu.roadqualitymapper.ui.screens

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import uw.edu.roadqualitymapper.R
import uw.edu.roadqualitymapper.data.AppSettings
import uw.edu.roadqualitymapper.data.ColorMode
import uw.edu.roadqualitymapper.ui.components.AppCard
import uw.edu.roadqualitymapper.ui.components.AppIcon
import uw.edu.roadqualitymapper.ui.components.SectionLabel

/** Opened from ☰ in the top bar. Back (the arrow or the phone's back) returns to the tabs. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val colorMode by AppSettings.colorMode.collectAsState()
    val keepScreenOn by AppSettings.keepScreenOn.collectAsState()
    BackHandler(onBack = onBack)

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
                navigationIcon = {
                    IconButton(onClick = onBack) { AppIcon(R.drawable.ic_arrow_left, contentDescription = "Back") }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
    ) { innerPadding ->
        Column(
            Modifier
                .padding(innerPadding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SectionLabel("Display", Modifier.padding(start = 4.dp, top = 4.dp))
            AppCard(contentPadding = PaddingValues(horizontal = 16.dp, vertical = 4.dp)) {
                Column(verticalArrangement = Arrangement.spacedBy(0.dp)) {
                    SettingText("Color-blind mode", "Changes road and marker colors", Modifier.padding(vertical = 10.dp))
                    ColorMode.entries.forEach { mode ->
                        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                        ChoiceRow(
                            title = mode.label,
                            detail = mode.detail,
                            selected = colorMode == mode,
                            onClick = { AppSettings.setColorMode(context, mode) },
                        )
                    }
                }
            }

            SectionLabel("Recording", Modifier.padding(start = 4.dp, top = 12.dp))
            AppCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    SettingText("Keep screen on", "While a trip is recording", Modifier.weight(1f))
                    Switch(checked = keepScreenOn, onCheckedChange = { AppSettings.setKeepScreenOn(context, it) })
                }
            }
        }
    }
}

@Composable
private fun SettingText(title: String, detail: String?, modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(title, style = MaterialTheme.typography.titleSmall)
        detail?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** One option in a pick-one list: its name, and a check when it's the chosen one. */
@Composable
private fun ChoiceRow(title: String, detail: String?, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SettingText(title, detail, Modifier.weight(1f))
        if (selected) {
            AppIcon(R.drawable.ic_circle_check, contentDescription = "Selected", tint = MaterialTheme.colorScheme.primary)
        } else {
            AppIcon(R.drawable.ic_circle, contentDescription = null, tint = MaterialTheme.colorScheme.outline)
        }
    }
}
