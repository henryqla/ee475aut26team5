package uw.edu.roadqualitymapper.service

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import uw.edu.roadqualitymapper.data.GpsFix
import uw.edu.roadqualitymapper.data.Trip

/** What the recording service is doing right now. Screens only read this; the service writes it. */
data class RecordingStatus(
    val recording: Boolean = false,
    val trip: Trip? = null,           // the current trip, or the last one after Stop
    val lastFix: GpsFix? = null,
    val fixCount: Int = 0,
    val gpsAvailable: Boolean = true, // false while the phone can't get a GPS fix
    val segments: Int = 0,            // saved this trip
    val segmentsWithoutLocation: Int = 0,
    val events: Int = 0,
    val lastRoughness: Double? = null, // g, rms_z of the newest segment
    val error: String? = null,        // why recording couldn't start, if it couldn't
)

object RecordingState {
    private val _status = MutableStateFlow(RecordingStatus())
    val status: StateFlow<RecordingStatus> = _status.asStateFlow()

    internal fun update(change: (RecordingStatus) -> RecordingStatus) = _status.update(change)
}
