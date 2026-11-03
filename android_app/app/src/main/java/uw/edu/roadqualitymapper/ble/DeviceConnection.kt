package uw.edu.roadqualitymapper.ble

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.ParcelUuid
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import no.nordicsemi.android.ble.observer.ConnectionObserver
import java.text.DateFormat
import java.util.Date

/** Re-send the phone's time this often while connected (contract §2). */
private const val TIME_SYNC_INTERVAL_MS = 60_000L

private const val SCAN_DURATION_MS = 10_000L
private const val CONNECT_TIMEOUT_MS = 15_000L
private const val RAW_LOG_SIZE = 20

private const val PREFS_NAME = "settings"
private const val KEY_DEVICE_ADDRESS = "device_address"
private const val KEY_DEVICE_NAME = "device_name"

enum class DeviceState {
    NOT_CONNECTED,
    CONNECTING,
    CONNECTED,      // subscribed and time written: messages are arriving
    RECONNECTING,   // lost the link; Android reconnects by itself as soon as the device is back
}

data class DeviceStatus(
    val state: DeviceState = DeviceState.NOT_CONNECTED,
    val address: String? = null,   // the remembered device
    val name: String? = null,
    // From the device's status message (contract §4.3)
    val fwVersion: String? = null,
    val imuOk: Boolean? = null,
    val sdOk: Boolean? = null,
    val rateHz: Int? = null,
    // Counts since the app started, to spot problems
    val received: Int = 0,
    val dropped: Int = 0,          // gaps in seq: messages the device sent that never arrived
    val unsynced: Int = 0,         // sent before the device had the time; thrown away
    val unreadable: Int = 0,       // not a message we understand (see the raw log)
    val error: String? = null,
)

/** A device found by a scan. */
data class FoundDevice(val address: String, val name: String, val rssi: Int)

/** One message as received, for the recording service. [message] is null if it couldn't be read. */
data class ReceivedMessage(val receivedAt: Long, val text: String, val message: DeviceMessage?)

/**
 * The app's one connection to the device. The Connect tab scans and connects; the recording
 * service reads [messages] during a trip. The device stays connected between trips.
 *
 * Only our device is ever shown or connected: scans are filtered by our service UUID, and after the
 * first connection the device's address is remembered and reconnected to (contract §3).
 */
@SuppressLint("MissingPermission")   // every Bluetooth call is behind hasBluetoothPermissions()
object DeviceConnection {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private lateinit var appContext: Context
    private var manager: RoadDeviceManager? = null
    private var timeSyncJob: Job? = null
    private var scanStopJob: Job? = null
    private var wantConnected = false   // false after the user taps Disconnect: don't reconnect
    private var lastSeq: Long? = null

    private val _status = MutableStateFlow(DeviceStatus())
    val status: StateFlow<DeviceStatus> = _status.asStateFlow()

    private val _scanning = MutableStateFlow(false)
    val scanning: StateFlow<Boolean> = _scanning.asStateFlow()

    private val _found = MutableStateFlow(emptyList<FoundDevice>())
    val found: StateFlow<List<FoundDevice>> = _found.asStateFlow()

    /** The last few raw messages, newest first, for the Connect tab. */
    private val _rawLog = MutableStateFlow(emptyList<String>())
    val rawLog: StateFlow<List<String>> = _rawLog.asStateFlow()

    private val _messages = MutableSharedFlow<ReceivedMessage>(extraBufferCapacity = 100)
    val messages: SharedFlow<ReceivedMessage> = _messages.asSharedFlow()

    /** Call once at app start. */
    fun init(context: Context) {
        if (::appContext.isInitialized) return
        appContext = context.applicationContext
        val prefs = prefs()
        _status.update {
            it.copy(address = prefs.getString(KEY_DEVICE_ADDRESS, null), name = prefs.getString(KEY_DEVICE_NAME, null))
        }
    }

    fun hasBluetoothPermissions(): Boolean = listOf(
        Manifest.permission.BLUETOOTH_SCAN,
        Manifest.permission.BLUETOOTH_CONNECT,
    ).all { ContextCompat.checkSelfPermission(appContext, it) == PackageManager.PERMISSION_GRANTED }

    fun isBluetoothOn(): Boolean = adapter()?.isEnabled == true

    // ------------------------------------------------------------ scanning

    fun startScan() {
        val scanner = readyAdapter()?.bluetoothLeScanner ?: return
        stopScan()
        _found.value = emptyList()
        _scanning.value = true
        val filter = ScanFilter.Builder().setServiceUuid(ParcelUuid(DeviceUuids.SERVICE)).build()
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        scanner.startScan(listOf(filter), settings, scanCallback)
        scanStopJob = scope.launch {
            delay(SCAN_DURATION_MS)
            stopScan()
        }
    }

    fun stopScan() {
        scanStopJob?.cancel()
        if (_scanning.value && hasBluetoothPermissions()) adapter()?.bluetoothLeScanner?.stopScan(scanCallback)
        _scanning.value = false
    }

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val device = FoundDevice(
                address = result.device.address,
                name = result.scanRecord?.deviceName ?: "Unnamed device",
                rssi = result.rssi,
            )
            _found.update { list -> (list.filterNot { it.address == device.address } + device).sortedByDescending { it.rssi } }
        }

        override fun onScanFailed(errorCode: Int) {
            _scanning.value = false
            setError("Scan failed (error $errorCode). Try turning Bluetooth off and on.")
        }
    }

    // ------------------------------------------------------------ connecting

    /** Connects to a device from a scan, and remembers it for next time. */
    fun connect(device: FoundDevice) {
        prefs().edit {
            putString(KEY_DEVICE_ADDRESS, device.address)
            putString(KEY_DEVICE_NAME, device.name)
        }
        _status.update { it.copy(address = device.address, name = device.name) }
        connectToRemembered()
    }

    /** Connects to the remembered device, if there is one and it isn't connected already. */
    fun connectToRemembered() {
        val address = _status.value.address ?: return
        if (_status.value.state != DeviceState.NOT_CONNECTED) return
        val bluetoothDevice = readyAdapter()?.getRemoteDevice(address) ?: return
        stopScan()
        wantConnected = true
        lastSeq = null
        _status.update { it.copy(state = DeviceState.CONNECTING, error = null) }
        manager().connect(bluetoothDevice)
            .useAutoConnect(false)   // the first try is direct and fast
            .retry(3, 200)
            .timeout(CONNECT_TIMEOUT_MS)
            .enqueue()
    }

    /** Disconnects and stops reconnecting. The device stays remembered. */
    fun disconnect() {
        wantConnected = false
        manager?.disconnect()?.enqueue()
        _status.update { it.copy(state = DeviceState.NOT_CONNECTED) }
    }

    /** Disconnects and forgets the device, so the next one has to be picked from a scan. */
    fun forget() {
        disconnect()
        prefs().edit {
            remove(KEY_DEVICE_ADDRESS)
            remove(KEY_DEVICE_NAME)
        }
        _status.update { it.copy(address = null, name = null, fwVersion = null, imuOk = null, sdOk = null, rateHz = null) }
    }

    private fun manager(): RoadDeviceManager = manager ?: RoadDeviceManager(
        appContext,
        onMessage = ::onMessage,
        onMtuTooSmall = { mtu ->
            // A message would be cut off (contract §3), so don't use this link.
            setError("The device only allows $mtu-byte packets; $REQUIRED_MTU are needed")
            disconnect()
        },
    ).also {
        it.setConnectionObserver(connectionObserver)
        manager = it
    }

    private val connectionObserver = object : ConnectionObserver {
        override fun onDeviceConnecting(device: BluetoothDevice) {}
        override fun onDeviceConnected(device: BluetoothDevice) {}
        override fun onDeviceDisconnecting(device: BluetoothDevice) {}

        override fun onDeviceReady(device: BluetoothDevice) {
            _status.update { it.copy(state = DeviceState.CONNECTED, error = null) }
            timeSyncJob?.cancel()
            timeSyncJob = scope.launch {
                while (isActive) {
                    delay(TIME_SYNC_INTERVAL_MS)
                    manager?.writeTime()
                }
            }
        }

        override fun onDeviceFailedToConnect(device: BluetoothDevice, reason: Int) {
            when (reason) {
                ConnectionObserver.REASON_NOT_SUPPORTED -> {
                    wantConnected = false
                    setError("That device doesn't have the Road Quality service")
                    _status.update { it.copy(state = DeviceState.NOT_CONNECTED) }
                }
                // Not around right now: wait for it in the background.
                else -> reconnectWhenBack(device)
            }
        }

        override fun onDeviceDisconnected(device: BluetoothDevice, reason: Int) {
            timeSyncJob?.cancel()
            when {
                // We asked for it (Disconnect, Forget, or a bad MTU).
                reason == ConnectionObserver.REASON_SUCCESS || !wantConnected ->
                    _status.update { it.copy(state = DeviceState.NOT_CONNECTED) }
                reason == ConnectionObserver.REASON_NOT_SUPPORTED -> {
                    wantConnected = false
                    setError("That device doesn't have the Road Quality service")
                    _status.update { it.copy(state = DeviceState.NOT_CONNECTED) }
                }
                // Unplugged, out of range, or the device restarted.
                else -> reconnectWhenBack(device)
            }
        }
    }

    /**
     * Asks Android to connect whenever the device shows up again, with no time limit. Android keeps
     * trying in the background even with the screen off, e.g. after the car is turned off and on.
     */
    private fun reconnectWhenBack(device: BluetoothDevice) {
        if (!wantConnected) return
        lastSeq = null
        _status.update { it.copy(state = DeviceState.RECONNECTING) }
        manager().connect(device).useAutoConnect(true).enqueue()
    }

    // ------------------------------------------------------------ messages

    private fun onMessage(text: String) {
        val now = System.currentTimeMillis()
        val time = DateFormat.getTimeInstance(DateFormat.MEDIUM).format(Date(now))
        _rawLog.update { (listOf("$time  $text") + it).take(RAW_LOG_SIZE) }

        val message = parseDeviceMessage(text)
        _status.update { status ->
            var s = status.copy(received = status.received + 1)
            if (message == null) {
                s = s.copy(unreadable = s.unreadable + 1)
            } else {
                // seq goes up by 1 per message; a jump means messages were lost on the way.
                // A smaller seq means the device restarted, which isn't a loss.
                val last = lastSeq
                if (last != null && message.seq > last + 1) s = s.copy(dropped = s.dropped + (message.seq - last - 1).toInt())
                lastSeq = message.seq
                if (message is StatusMessage) {
                    s = s.copy(fwVersion = message.fw, imuOk = message.imuOk, sdOk = message.sdOk, rateHz = message.rateHz)
                } else if (!message.synced) {
                    s = s.copy(unsynced = s.unsynced + 1)
                }
            }
            s
        }
        _messages.tryEmit(ReceivedMessage(now, text, message))
    }

    // ------------------------------------------------------------ helpers

    private fun adapter(): BluetoothAdapter? =
        appContext.getSystemService(BluetoothManager::class.java)?.adapter

    /** The adapter if Bluetooth can be used right now; otherwise shows why not and returns null. */
    private fun readyAdapter(): BluetoothAdapter? {
        val adapter = adapter()
        when {
            adapter == null -> setError("This phone has no Bluetooth")
            !hasBluetoothPermissions() -> setError("Allow \"Nearby devices\" to connect to the device")
            !adapter.isEnabled -> setError("Turn on Bluetooth")
            else -> return adapter
        }
        return null
    }

    private fun setError(message: String) = _status.update { it.copy(error = message) }

    private fun prefs() = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
