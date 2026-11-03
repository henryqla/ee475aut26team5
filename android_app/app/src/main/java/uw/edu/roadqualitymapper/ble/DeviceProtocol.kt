package uw.edu.roadqualitymapper.ble

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.util.UUID

// What the device and the app say to each other: docs/data-contract.md §3–§5.

object DeviceUuids {
    val SERVICE: UUID = UUID.fromString("6f9e0001-3c2a-4b8e-9d41-7a5c2e8b1f30")
    val DATA: UUID = UUID.fromString("6f9e0002-3c2a-4b8e-9d41-7a5c2e8b1f30")   // notify, device → app
    val TIME: UUID = UUID.fromString("6f9e0003-3c2a-4b8e-9d41-7a5c2e8b1f30")   // write, app → device
}

/** Bigger packets, so each ~150-byte message fits in one notification (contract §3). */
const val REQUIRED_MTU = 247

/** A test device's fw version starts with this; its trips are tagged FAKE_DEVICE (contract §4.3). */
const val FAKE_FW_PREFIX = "fake"

/** Every message has these (contract §4). */
sealed interface DeviceMessage {
    val seq: Long
    val synced: Boolean   // false until the app has written the time; times are then meaningless
}

/** ~1 second of road (§4.1). */
@Serializable
data class SegMessage(
    @SerialName("seq") override val seq: Long,
    @SerialName("synced") override val synced: Boolean,
    @SerialName("t_start") val tStart: Long,
    @SerialName("t_end") val tEnd: Long,
    @SerialName("n") val n: Int = 0,
    @SerialName("rms_z") val rmsZ: Double,
    @SerialName("peak_z") val peakZ: Double,
    @SerialName("roll_rms") val rollRms: Double,
    @SerialName("pitch_rms") val pitchRms: Double,
) : DeviceMessage

/** A pothole, speed bump or other bump (§4.2). */
@Serializable
data class EvtMessage(
    @SerialName("seq") override val seq: Long,
    @SerialName("synced") override val synced: Boolean,
    @SerialName("t") val t: Long,
    @SerialName("class") val eventClass: String,
    @SerialName("peak_z") val peakZ: Double,
    @SerialName("roll_peak") val rollPeak: Double,
    @SerialName("pitch_peak") val pitchPeak: Double,
    @SerialName("conf") val conf: Double = 1.0,
) : DeviceMessage

/** Sent once on connect (§4.3). */
@Serializable
data class StatusMessage(
    @SerialName("seq") override val seq: Long,
    @SerialName("synced") override val synced: Boolean,
    @SerialName("fw") val fw: String? = null,
    @SerialName("imu_ok") val imuOk: Boolean? = null,
    @SerialName("sd_ok") val sdOk: Boolean? = null,
    @SerialName("rate_hz") val rateHz: Int? = null,
) : DeviceMessage

// Fields added to the contract later are skipped instead of breaking older app versions.
private val json = Json { ignoreUnknownKeys = true }

/** Null if the text isn't a message we understand; the raw text still shows on the Connect tab. */
fun parseDeviceMessage(text: String): DeviceMessage? = try {
    val obj = json.parseToJsonElement(text).jsonObject
    when (obj["type"]?.jsonPrimitive?.contentOrNull) {
        "seg" -> json.decodeFromJsonElement<SegMessage>(obj)
        "evt" -> json.decodeFromJsonElement<EvtMessage>(obj)
        "status" -> json.decodeFromJsonElement<StatusMessage>(obj)
        else -> null
    }
} catch (e: SerializationException) {
    null
} catch (e: IllegalArgumentException) {   // not a JSON object, or a field of the wrong type
    null
}

/** Written to TIME on connect and every 60 s (§5). */
fun timeMessage(phoneMillis: Long) = """{"type":"time","t":$phoneMillis}"""
