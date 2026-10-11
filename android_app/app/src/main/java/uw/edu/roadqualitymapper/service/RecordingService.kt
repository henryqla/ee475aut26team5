package uw.edu.roadqualitymapper.service

import android.app.Service
import android.content.Intent
import android.os.IBinder

/**
 * Phase 3: becomes a foreground service (location|connectedDevice) so recording continues with
 * the screen off. It will own the BLE connection, location updates, stamping, and uploading.
 * Declared in the manifest now; nothing starts it yet.
 */
class RecordingService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null
}