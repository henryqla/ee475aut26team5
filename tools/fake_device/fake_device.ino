// Fake Road Quality device: an ESP32 that talks to the Android app exactly like the real device
// (docs/data-contract.md §3–§5), but sends made-up roughness instead of reading an IMU.
// Use it to test the app's BLE connection before the real firmware is ready.
//
// Setup (Arduino IDE 2):
//   1. Boards Manager: install "esp32" by Espressif Systems (version 3.x).
//   2. Library Manager: install "NimBLE-Arduino" by h2zero (version 2.x).
//   3. Tools → Board: "ESP32S3 Dev Module" (or your board). For Serial Monitor output over the
//      S3's native USB port, set Tools → USB CDC On Boot: Enabled.
//   4. Upload, open Serial Monitor at 115200 baud.
//
// What it does:
//   - Advertises the service UUID (name "RoadQuality" in the scan response), restarts
//     advertising after every disconnect.
//   - Once the app subscribes: sends a status message, then one seg per second and an evt
//     every 15 s (alternating pothole / speed bump).
//   - Messages say "synced": false until the app writes the time.
//   - Its fw version starts with "fake", so the app tags its trips FAKE_DEVICE (contract §4.3),
//     which keeps them off the real map and easy to delete.

#include <NimBLEDevice.h>

static const char* DEVICE_NAME = "RoadQuality";
static const char* FW_VERSION = "fake-0.1.0";

static const char* SERVICE_UUID = "6f9e0001-3c2a-4b8e-9d41-7a5c2e8b1f30";
static const char* DATA_UUID    = "6f9e0002-3c2a-4b8e-9d41-7a5c2e8b1f30";
static const char* TIME_UUID    = "6f9e0003-3c2a-4b8e-9d41-7a5c2e8b1f30";

static const uint32_t SEGMENT_MS = 1000;
static const uint32_t EVENT_EVERY_N_SEGMENTS = 15;

static NimBLECharacteristic* dataChar = nullptr;
static volatile bool subscribed = false;
static volatile bool statusPending = false;   // send status right after the app subscribes
static volatile bool synced = false;
static volatile int64_t offsetMs = 0;          // phone_ms - millis(), from the last TIME write

static uint32_t seq = 0;
static uint32_t segmentCount = 0;
static uint32_t lastSegmentMillis = 0;
static float roughness = 0.05f;                // g, wanders up and down like a real road

// Phone time (epoch ms) once synced; before that, just millis().
static int64_t nowMs() { return (int64_t)millis() + offsetMs; }

static void send(const char* json, int length) {
    dataChar->setValue(reinterpret_cast<const uint8_t*>(json), length);
    dataChar->notify();
    Serial.println(json);
}

static void sendStatus() {
    char buf[200];
    int n = snprintf(buf, sizeof buf,
        "{\"type\":\"status\",\"seq\":%lu,\"synced\":%s,\"fw\":\"%s\",\"imu_ok\":true,"
        "\"sd_ok\":true,\"rate_hz\":400}",
        (unsigned long)seq++, synced ? "true" : "false", FW_VERSION);
    send(buf, n);
}

static void sendSegment(int64_t tStart, int64_t tEnd) {
    // Random walk between smooth (0.02 g) and very rough (0.25 g).
    roughness += random(-15, 16) / 1000.0f;
    roughness = constrain(roughness, 0.02f, 0.25f);
    float peakZ = roughness * (2.5f + random(0, 200) / 100.0f);
    float rollRms = 1.0f + random(0, 300) / 100.0f;
    float pitchRms = 1.0f + random(0, 300) / 100.0f;

    char buf[220];
    int n = snprintf(buf, sizeof buf,
        "{\"type\":\"seg\",\"seq\":%lu,\"synced\":%s,\"t_start\":%lld,\"t_end\":%lld,\"n\":400,"
        "\"rms_z\":%.3f,\"peak_z\":%.3f,\"roll_rms\":%.3f,\"pitch_rms\":%.3f}",
        (unsigned long)seq++, synced ? "true" : "false", (long long)tStart, (long long)tEnd,
        roughness, peakZ, rollRms, pitchRms);
    send(buf, n);
}

static void sendEvent(int64_t t, bool pothole) {
    char buf[220];
    int n = snprintf(buf, sizeof buf,
        "{\"type\":\"evt\",\"seq\":%lu,\"synced\":%s,\"t\":%lld,\"class\":\"%s\","
        "\"peak_z\":%.3f,\"roll_peak\":%.3f,\"pitch_peak\":%.3f,\"conf\":%.3f}",
        (unsigned long)seq++, synced ? "true" : "false", (long long)t,
        pothole ? "pothole" : "speed_bump",
        pothole ? 1.9f : 0.9f,          // peak_z, g
        pothole ? 45.0f : 12.0f,        // roll_peak, deg/s: potholes rock side to side
        pothole ? 12.0f : 40.0f,        // pitch_peak, deg/s: speed bumps tip nose up/down
        0.7f);
    send(buf, n);
}

class ServerCallbacks : public NimBLEServerCallbacks {
    void onConnect(NimBLEServer* server, NimBLEConnInfo& connInfo) override {
        Serial.printf("Connected to %s\n", connInfo.getAddress().toString().c_str());
    }

    void onDisconnect(NimBLEServer* server, NimBLEConnInfo& connInfo, int reason) override {
        // Advertising restarts by itself (advertiseOnDisconnect below), so the phone can reconnect.
        Serial.printf("Disconnected (reason %d), advertising again\n", reason);
        subscribed = false;
    }

    void onMTUChange(uint16_t mtu, NimBLEConnInfo& connInfo) override {
        Serial.printf("MTU is now %u (messages can be up to %u bytes)\n", mtu, mtu - 3);
    }
};

class DataCallbacks : public NimBLECharacteristicCallbacks {
    void onSubscribe(NimBLECharacteristic* characteristic, NimBLEConnInfo& connInfo,
                     uint16_t subValue) override {
        subscribed = (subValue & 0x0001) != 0;   // bit 0 = notifications on
        Serial.printf("App %s DATA\n", subscribed ? "subscribed to" : "unsubscribed from");
        if (subscribed) statusPending = true;
    }
};

class TimeCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic* characteristic, NimBLEConnInfo& connInfo) override {
        // Expected: {"type":"time","t":1792500000000}
        NimBLEAttValue value = characteristic->getValue();
        String text(reinterpret_cast<const char*>(value.data()), value.length());
        int at = text.indexOf("\"t\":");
        if (at < 0) {
            Serial.printf("Ignoring TIME write without \"t\": %s\n", text.c_str());
            return;
        }
        int64_t phoneMs = strtoll(text.c_str() + at + 4, nullptr, 10);
        offsetMs = phoneMs - (int64_t)millis();
        synced = true;
        Serial.printf("Time synced: %lld\n", (long long)phoneMs);
    }
};

void setup() {
    Serial.begin(115200);
    randomSeed(esp_random());

    NimBLEDevice::init(DEVICE_NAME);
    NimBLEDevice::setMTU(247);   // the app asks for 247 (contract §3)

    NimBLEServer* server = NimBLEDevice::createServer();
    server->setCallbacks(new ServerCallbacks());
    server->advertiseOnDisconnect(true);

    NimBLEService* service = server->createService(SERVICE_UUID);
    dataChar = service->createCharacteristic(DATA_UUID, NIMBLE_PROPERTY::NOTIFY);
    dataChar->setCallbacks(new DataCallbacks());
    NimBLECharacteristic* timeChar = service->createCharacteristic(TIME_UUID, NIMBLE_PROPERTY::WRITE);
    timeChar->setCallbacks(new TimeCallbacks());
    service->start();

    // Service UUID in the advertising packet, name in the scan response: both together don't
    // fit in one 31-byte packet (contract §3).
    NimBLEAdvertisementData advData;
    advData.setFlags(BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP);
    advData.addServiceUUID(NimBLEUUID(SERVICE_UUID));
    NimBLEAdvertisementData scanData;
    scanData.setName(DEVICE_NAME);

    NimBLEAdvertising* advertising = NimBLEDevice::getAdvertising();
    advertising->setAdvertisementData(advData);
    advertising->setScanResponseData(scanData);
    advertising->setMinInterval(160);   // 160 × 0.625 ms = 100 ms
    advertising->setMaxInterval(160);
    advertising->start();

    Serial.printf("Advertising as %s, address %s\n", DEVICE_NAME,
                  NimBLEDevice::getAddress().toString().c_str());
}

void loop() {
    if (subscribed && statusPending) {
        statusPending = false;
        sendStatus();
    }

    uint32_t now = millis();
    if (now - lastSegmentMillis >= SEGMENT_MS) {
        lastSegmentMillis = now;
        if (subscribed) {
            int64_t tEnd = nowMs();
            sendSegment(tEnd - (SEGMENT_MS - 1), tEnd);
            segmentCount++;
            if (segmentCount % EVENT_EVERY_N_SEGMENTS == 0) {
                // Peak a bit before now, like a real detector that reports after the fact.
                sendEvent(tEnd - 300, (segmentCount / EVENT_EVERY_N_SEGMENTS) % 2 == 1);
            }
        }
    }
    delay(10);
}
