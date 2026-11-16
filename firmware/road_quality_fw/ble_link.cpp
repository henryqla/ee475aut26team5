/*
 * File:        ble_link.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-03
 * Version:     0.2.0-draft (new file)
 * Description: ble_link task (core 0). Sole owner of BLE. Implements the
 *              device side of docs/data-contract.md sections 3-5:
 *              - Advertises the service UUID, with the name in the scan
 *                response, every ~100 ms whenever no phone is connected, and
 *                again after every disconnect.
 *              - On every subscribe to DATA, sends status. Then one JSON
 *                notification per seg and evt from dsp. While nobody is
 *                subscribed, those are dropped (nothing is buffered).
 *              - seq counts every message of any type, from 0 at boot (CR-6).
 *              - A TIME write goes to dsp through time_q; dsp applies it at
 *                the next segment boundary (CR-4).
 *              - In the safe state, status says imu_ok:false.
 *              The NimBLE callbacks run in NimBLE's host task (also core 0).
 *              They only copy what they got, queue it, and return.
 *
 *              Adapted from tools/fake_device/fake_device.ino (same UUIDs,
 *              advertising setup, and JSON shapes), restructured into the
 *              task / queue architecture. Library: NimBLE-Arduino 2.x
 *              (https://github.com/h2zero/NimBLE-Arduino), built against 2.5.2.
 */

// ============================================================================
// Includes
// ============================================================================

#include <NimBLEDevice.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "esp_timer.h"
#include "config.h"
#include "types.h"
#include "ble_link.h"
#include "supervisor.h"

// ============================================================================
// Macros
// ============================================================================

// Contract section 3. The app looks for exactly these.
#define BLE_SERVICE_UUID  "6f9e0001-3c2a-4b8e-9d41-7a5c2e8b1f30"
#define BLE_DATA_UUID     "6f9e0002-3c2a-4b8e-9d41-7a5c2e8b1f30"  // Notify, device -> app
#define BLE_TIME_UUID     "6f9e0003-3c2a-4b8e-9d41-7a5c2e8b1f30"  // Write, app -> device

// Bigger than BLE_MAX_PAYLOAD, so an over-long message is caught and counted
// instead of being cut short by snprintf.
#define JSON_BUF_CHARS    256

// A TIME write is {"type":"time","t":1792500000000}, 33 characters. Anything
// at least this long is not a TIME write.
#define TIME_BUF_CHARS    64

// ============================================================================
// Globals: types
// ============================================================================

// State owned by ble_link_task.
typedef struct {
  bool     subscribed;    // The app has DATA notifications on
  bool     time_written;  // At least one valid TIME write since boot
  bool     imu_ok;        // false once the supervisor reports the safe state
  uint32_t seq;           // Next message number, any type (contract section 4)
  uint32_t sent;          // Notifications sent
  uint32_t fails;         // Notifications that failed or were too long
  uint32_t out_q_drops;   // BLE lines for Serial that out_q had no room for
} link_state_t;

// ============================================================================
// Prototypes
// ============================================================================

static void queue_link(link_event_t what, int64_t value);
static bool parse_time(const uint8_t *data, size_t len, int64_t *phone_ms);
static bool ble_init(const app_ctx_t *ctx);
static void notify_json(link_state_t *st, const char *json, int len);
static void send_status(link_state_t *st);
static void send_seg(link_state_t *st, const seg_t *g);
static void send_evt(link_state_t *st, const evt_t *e);
static void log_link(const app_ctx_t *ctx, link_state_t *st, const link_t *link);
static const char *json_bool(bool b);
static const char *class_json(evt_class_t c);

// ============================================================================
// Globals: callbacks and handles
// ============================================================================

// The NimBLE callbacks run in NimBLE's host task, not in ble_link_task. Each
// one copies what it got, queues it, and returns: no Serial, no JSON, no
// waiting (architecture rule for ISRs and callbacks).

class ServerCallbacks : public NimBLEServerCallbacks {
 public:
  void onConnect(NimBLEServer * /*server*/, NimBLEConnInfo & /*info*/) override {
    queue_link(LINK_CONNECTED, 0);
  }
  // Advertising restarts by itself (advertiseOnDisconnect in ble_init).
  void onDisconnect(NimBLEServer * /*server*/, NimBLEConnInfo & /*info*/,
                    int reason) override {
    queue_link(LINK_DISCONNECTED, reason);
  }
  void onMTUChange(uint16_t mtu, NimBLEConnInfo & /*info*/) override {
    queue_link(LINK_MTU, mtu);
  }
};

class DataCallbacks : public NimBLECharacteristicCallbacks {
 public:
  void onSubscribe(NimBLECharacteristic * /*chr*/, NimBLEConnInfo & /*info*/,
                   uint16_t sub_value) override {
    // Bit 0 = notifications on. The app never asks for indications.
    queue_link((sub_value & 0x0001) ? LINK_SUBSCRIBED : LINK_UNSUBSCRIBED, 0);
  }
};

class TimeCallbacks : public NimBLECharacteristicCallbacks {
 public:
  void onWrite(NimBLECharacteristic *chr, NimBLEConnInfo & /*info*/) override;
};

// Written once in ble_init(), before advertising starts, so before any
// callback can run. Read-only afterwards.
static QueueHandle_t s_ble_q = NULL;
static QueueHandle_t s_time_q = NULL;
static NimBLECharacteristic *s_data_char = NULL;  // Used by ble_link_task only

// Static objects: they live forever, so the library must never delete them.
static ServerCallbacks s_server_cb;
static DataCallbacks s_data_cb;
static TimeCallbacks s_time_cb;

// ============================================================================
// Implementations
// ============================================================================

/**
 * TIME write callback (contract section 5). Records the local time first, as
 * close to arrival as possible, then hands phone time and local time to dsp.
 * dsp applies the new offset at its next segment boundary (CR-4).
 * @param chr  The TIME characteristic.
 * @return None.
 */
void TimeCallbacks::onWrite(NimBLECharacteristic *chr, NimBLEConnInfo & /*info*/) {
  const int64_t local_ms = esp_timer_get_time() / 1000;
  const NimBLEAttValue value = chr->getValue();
  int64_t phone_ms = 0;
  if (parse_time(value.data(), value.length(), &phone_ms)) {
    const time_sync_t ts = {phone_ms, local_ms};
    xQueueOverwrite(s_time_q, &ts);  // Length 1: the latest write wins
    queue_link(LINK_TIME, phone_ms);
  } else {
    queue_link(LINK_TIME_BAD, (int64_t)value.length());
  }
}

/**
 * Queues one link change for ble_link_task. Called from the NimBLE callbacks.
 * Goes to the front, so a link change never waits behind data, and never
 * waits for space, so the NimBLE host task never blocks.
 * @param what   What changed.
 * @param value  Reason code, MTU, or phone time; 0 if none.
 * @return None.
 */
static void queue_link(link_event_t what, int64_t value) {
  ble_msg_t m = {};
  m.tag = BLE_LINK;
  m.link.what = what;
  m.link.value = value;
  xQueueSendToFront(s_ble_q, &m, 0);
}

/**
 * Reads the phone time out of a TIME write: the number after "t":. Rejects
 * anything without digits there, or with a time that is not positive. Unlike
 * fake_device, a bad write is reported and does not count as a sync.
 * @param data      Bytes written by the app (not 0-terminated).
 * @param len       Number of bytes.
 * @param phone_ms  Receives the phone epoch ms on success.
 * @return true if a usable time was found.
 */
static bool parse_time(const uint8_t *data, size_t len, int64_t *phone_ms) {
  char text[TIME_BUF_CHARS];
  if (len == 0 || len >= sizeof(text)) {
    return false;
  }
  memcpy(text, data, len);
  text[len] = '\0';

  const char *at = strstr(text, "\"t\":");
  if (at == NULL) {
    return false;
  }
  const char *digits = at + 4;  // Just past "t":
  char *end = NULL;
  const long long t = strtoll(digits, &end, 10);
  if (end == digits || t <= 0) {
    return false;
  }
  *phone_ms = (int64_t)t;
  return true;
}

/**
 * Starts NimBLE, creates the service with DATA and TIME, and starts
 * advertising (contract section 3).
 * @param ctx  Queue handles the callbacks need.
 * @return true if the stack and advertising started.
 */
static bool ble_init(const app_ctx_t *ctx) {
  s_ble_q = ctx->ble_q;
  s_time_q = ctx->time_q;

  if (!NimBLEDevice::init(BLE_DEVICE_NAME)) {
    return false;
  }
  // The largest MTU we accept. The app asks for 247 right after connecting.
  if (!NimBLEDevice::setMTU(BLE_MTU)) {
    return false;
  }

  NimBLEServer *server = NimBLEDevice::createServer();
  server->setCallbacks(&s_server_cb, false);  // false: static object, never delete
  server->advertiseOnDisconnect(true);        // Contract: phone must always be able to reconnect

  NimBLEService *service = server->createService(BLE_SERVICE_UUID);
  s_data_char = service->createCharacteristic(BLE_DATA_UUID, NIMBLE_PROPERTY::NOTIFY);
  s_data_char->setCallbacks(&s_data_cb);
  NimBLECharacteristic *time_char =
      service->createCharacteristic(BLE_TIME_UUID, NIMBLE_PROPERTY::WRITE);
  time_char->setCallbacks(&s_time_cb);
  // No service->start(): in NimBLE-Arduino 2.x, advertising->start() below
  // starts the GATT server, and with it the service.

  // Service UUID in the advertising packet, name in the scan response: flags
  // (3 bytes) + 128-bit UUID (18) + name (13) don't fit in one 31-byte packet.
  NimBLEAdvertisementData adv_data;
  adv_data.setFlags(BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP);
  adv_data.addServiceUUID(NimBLEUUID(BLE_SERVICE_UUID));
  NimBLEAdvertisementData scan_data;
  scan_data.setName(BLE_DEVICE_NAME);

  NimBLEAdvertising *advertising = NimBLEDevice::getAdvertising();
  advertising->setAdvertisementData(adv_data);
  advertising->setScanResponseData(scan_data);
  advertising->setMinInterval(BLE_ADV_INTERVAL);
  advertising->setMaxInterval(BLE_ADV_INTERVAL);
  return advertising->start();
}

/**
 * Sends one JSON message as one notification. Every message uses up one seq,
 * even if it fails, so the app sees a gap exactly where a message was lost.
 * @param st    Link state: seq, sent and fails are updated.
 * @param json  The message, without a trailing newline.
 * @param len   snprintf's return value for it.
 * @return None.
 */
static void notify_json(link_state_t *st, const char *json, int len) {
  st->seq++;
  // Longer than MTU - 3 would arrive cut off, not split (contract section 3).
  if (len < 0 || len > BLE_MAX_PAYLOAD) {
    st->fails++;
    return;
  }
  if (s_data_char->notify((const uint8_t *)json, (size_t)len)) {
    st->sent++;
  } else {
    st->fails++;
  }
}

/**
 * Sends the status message (contract 4.3). sd_ok is false: no SD card in this
 * build. synced is true once any valid TIME write has arrived.
 * @param st  Link state.
 * @return None.
 */
static void send_status(link_state_t *st) {
  char buf[JSON_BUF_CHARS];
  const int len = snprintf(buf, sizeof(buf),
      "{\"type\":\"status\",\"seq\":%" PRIu32 ",\"synced\":%s,\"fw\":\"%s\","
      "\"imu_ok\":%s,\"sd_ok\":false,\"rate_hz\":%d}",
      st->seq, json_bool(st->time_written), FW_VERSION, json_bool(st->imu_ok),
      IMU_ODR_HZ);
  notify_json(st, buf, len);
}

/**
 * Sends one seg message (contract 4.1). Decimals rounded to 3 places.
 * @param st  Link state.
 * @param g   The segment from dsp.
 * @return None.
 */
static void send_seg(link_state_t *st, const seg_t *g) {
  char buf[JSON_BUF_CHARS];
  const int len = snprintf(buf, sizeof(buf),
      "{\"type\":\"seg\",\"seq\":%" PRIu32 ",\"synced\":%s,\"t_start\":%" PRId64
      ",\"t_end\":%" PRId64 ",\"n\":%" PRIu32 ",\"rms_z\":%.3f,\"peak_z\":%.3f,"
      "\"roll_rms\":%.3f,\"pitch_rms\":%.3f}",
      st->seq, json_bool(g->synced), g->t_start_ms, g->t_end_ms, g->n,
      g->rms_z, g->peak_z, g->roll_rms, g->pitch_rms);
  notify_json(st, buf, len);
}

/**
 * Sends one evt message (contract 4.2). t is the time of the largest peak.
 * @param st  Link state.
 * @param e   The event from dsp.
 * @return None.
 */
static void send_evt(link_state_t *st, const evt_t *e) {
  char buf[JSON_BUF_CHARS];
  const int len = snprintf(buf, sizeof(buf),
      "{\"type\":\"evt\",\"seq\":%" PRIu32 ",\"synced\":%s,\"t\":%" PRId64
      ",\"class\":\"%s\",\"peak_z\":%.3f,\"roll_peak\":%.3f,"
      "\"pitch_peak\":%.3f,\"conf\":%.3f}",
      st->seq, json_bool(e->synced), e->t_ms, class_json(e->cls),
      e->peak_z_g, e->peak_roll_dps, e->peak_pitch_dps, EVT_CONF);
  notify_json(st, buf, len);
}

/**
 * Forwards one link change to serial_out as a BLE line. Never waits.
 * @param ctx   For out_q.
 * @param st    Link state: out_q_drops is updated on a drop.
 * @param link  The change.
 * @return None.
 */
static void log_link(const app_ctx_t *ctx, link_state_t *st, const link_t *link) {
  out_msg_t m = {};
  m.tag = OUT_BLE;
  m.link = *link;
  if (xQueueSend(ctx->out_q, &m, 0) != pdTRUE) {
    st->out_q_drops++;
  }
}

/**
 * @param b  A flag.
 * @return "true" or "false" for JSON.
 */
static const char *json_bool(bool b) {
  return b ? "true" : "false";
}

/**
 * @param c  Event class.
 * @return The contract's class string (contract 4.2).
 */
static const char *class_json(evt_class_t c) {
  switch (c) {
    case EVT_POTHOLE: return "pothole";
    case EVT_BUMP:    return "speed_bump";
    default:          return "unknown";
  }
}

void ble_link_task(void *param) {
  const app_ctx_t *ctx = (const app_ctx_t *)param;

  if (!ble_init(ctx)) {
    sys_fault_and_stop(ctx->sys_q, TASK_BLE_LINK, FAULT_BLE_INIT);  // F4
  }

  link_state_t st = {};
  st.imu_ok = true;  // Until the supervisor says otherwise

  const link_t adv = {LINK_ADVERTISING, 0};
  log_link(ctx, &st, &adv);

  int64_t last_hb_us = esp_timer_get_time();
  for (;;) {
    ble_msg_t m;
    // The timeout lets the heartbeat go out even when ble_q stays empty.
    if (xQueueReceive(ctx->ble_q, &m, pdMS_TO_TICKS(HEARTBEAT_PERIOD_MS)) == pdTRUE) {
      switch (m.tag) {
        case BLE_LINK:
          if (m.link.what == LINK_SUBSCRIBED) {
            // Contract: data only after subscribe, and status first, on every
            // connect. Subscribe always comes after the MTU exchange.
            st.subscribed = true;
            send_status(&st);
          } else if (m.link.what == LINK_UNSUBSCRIBED ||
                     m.link.what == LINK_DISCONNECTED) {
            st.subscribed = false;
          } else if (m.link.what == LINK_TIME) {
            st.time_written = true;
          }
          log_link(ctx, &st, &m.link);
          break;
        case BLE_SEG:
          if (st.subscribed) {
            send_seg(&st, &m.seg);
          }
          break;
        case BLE_EVT:
          if (st.subscribed) {
            send_evt(&st, &m.evt);
          }
          break;
        case BLE_IMU_DOWN:
          // Sent every second in the safe state; only the first one matters.
          if (st.imu_ok) {
            st.imu_ok = false;
            if (st.subscribed) {
              send_status(&st);
            }
          }
          break;
        default:
          break;  // Unknown tag: ignore
      }
    }

    const int64_t now_us = esp_timer_get_time();
    if (now_us - last_hb_us >= (int64_t)HEARTBEAT_PERIOD_MS * 1000) {
      last_hb_us = now_us;
      sys_msg_t hb = {};
      hb.task = TASK_BLE_LINK;
      hb.out_q_drops = st.out_q_drops;
      hb.ble_sent = st.sent;
      hb.ble_fails = st.fails;
      sys_heartbeat(ctx->sys_q, &hb);
    }
  }
}
