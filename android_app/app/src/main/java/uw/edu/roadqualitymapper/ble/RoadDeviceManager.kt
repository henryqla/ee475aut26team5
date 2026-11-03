package uw.edu.roadqualitymapper.ble

import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCharacteristic
import android.content.Context
import android.util.Log
import no.nordicsemi.android.ble.BleManager

private const val TAG = "RoadDevice"

/**
 * The BLE link to one device, built on Nordic's Android-BLE-Library, which queues the GATT
 * operations and handles Android's BLE quirks. Only [DeviceConnection] uses this.
 *
 * Nordic's order on connect: connect → discover services → [isRequiredServiceSupported] →
 * [initialize] (bigger MTU → subscribe to DATA → write TIME) → ready.
 */
internal class RoadDeviceManager(
    context: Context,
    private val onMessage: (String) -> Unit,
    private val onMtuTooSmall: (Int) -> Unit,
) : BleManager(context) {

    private var dataCharacteristic: BluetoothGattCharacteristic? = null
    private var timeCharacteristic: BluetoothGattCharacteristic? = null

    /** False for a device without our service and characteristics; the library then disconnects. */
    override fun isRequiredServiceSupported(gatt: BluetoothGatt): Boolean {
        val service = gatt.getService(DeviceUuids.SERVICE) ?: return false
        val data = service.getCharacteristic(DeviceUuids.DATA)
        val time = service.getCharacteristic(DeviceUuids.TIME)
        dataCharacteristic = data
        timeCharacteristic = time
        val writable = BluetoothGattCharacteristic.PROPERTY_WRITE or
            BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE
        return data != null && time != null &&
            data.properties and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0 &&
            time.properties and writable != 0
    }

    override fun initialize() {
        requestMtu(REQUIRED_MTU)
            .with { _, mtu -> if (mtu < REQUIRED_MTU) onMtuTooSmall(mtu) }
            .enqueue()
        setNotificationCallback(dataCharacteristic).with { _, data ->
            data.value?.let { onMessage(String(it, Charsets.UTF_8)) }
        }
        enableNotifications(dataCharacteristic).enqueue()
        writeTime()
    }

    override fun onServicesInvalidated() {
        dataCharacteristic = null
        timeCharacteristic = null
    }

    /** Tells the device the phone's time (contract §2). */
    fun writeTime() {
        val characteristic = timeCharacteristic ?: return
        val bytes = timeMessage(System.currentTimeMillis()).toByteArray(Charsets.UTF_8)
        writeCharacteristic(characteristic, bytes, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)
            .fail { _, status -> Log.w(TAG, "TIME write failed, status $status") }
            .enqueue()
    }

    // The library's own log goes to Logcat under the "RoadDevice" tag.
    override fun getMinLogPriority() = Log.INFO

    override fun log(priority: Int, message: String) {
        Log.println(priority, TAG, message)
    }
}
