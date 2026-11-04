package uw.edu.roadqualitymapper.data

import android.content.Context
import androidx.core.content.edit
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Which car the phone is in. Every trip is tagged with it, so it's remembered between launches,
 * and it starts out unset: a guessed default would silently tag trips with the wrong car.
 */
object SelectedVehicle {
    private const val PREFS_NAME = "settings"
    private const val KEY_VEHICLE_ID = "vehicle_id"

    private val _id = MutableStateFlow<String?>(null)
    val id: StateFlow<String?> = _id.asStateFlow()

    /** Call once at app start. */
    fun load(context: Context) {
        _id.value = prefs(context).getString(KEY_VEHICLE_ID, null)
    }

    fun set(context: Context, vehicleId: String) {
        prefs(context).edit { putString(KEY_VEHICLE_ID, vehicleId) }
        _id.value = vehicleId
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
