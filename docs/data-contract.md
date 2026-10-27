# Road Quality Mapper — Data Contract

**Version 0.3 — DRAFT for team review (Oct 2026).** Owner: **Scott** (keeps this file up to date).
Everything here is a proposal. Sections marked **[Henry]** or **[Ben]** need that person's OK;
the app adapts. Once agreed, changes go through this file so firmware, app, and backend stay in sync.
What changed since v0.2 is listed in §10.

---

## 1. Pipeline overview

```
IMU 400 Hz ──► ESP32 firmware ──┬──► microSD: raw 400 Hz log (backup, tuning, ML)
               [Henry]          │
                                └──► BLE: 1 segment/s + events ──► Android app [app]
                                                                      │  + phone GPS 1 Hz
                                                                      │  matches each message to
                                                                      │  where the car was, by time
                                                                      ▼
                                                     Room (on phone) ──► Supabase [Ben]
                                                                            │
                                                          ┌─────────────────┴────────────┐
                                                          ▼                              ▼
                                                   Map tab in the app            Web map (read-only)
```

| Data | Rate | Lives in | Owner |
|---|---|---|---|
| Raw IMU samples | 400 Hz | microSD only | Henry / Kyle |
| Segment summaries + events | 1/s + as detected | BLE → phone | Henry produces, app receives |
| Phone GPS fixes (lat, lon, speed, heading, accuracy) | 1/s | phone | app |
| Stamped segments + events | 1/s + as detected | Supabase | app uploads, Ben stores |

---

## 2. Time: one clock for everything

All timestamps in BLE messages and SD rows are **phone time: Unix epoch milliseconds**
(e.g. `1792500000000`). Use a 64-bit integer (`int64_t` / `uint64_t`) on the ESP32.

**Sync procedure**
1. On every BLE connect, and then **every 60 s**, the app writes the phone's current
   epoch ms to the TIME characteristic (§3).
2. Firmware stores `offset = phone_ms − millis()` at the moment it receives the write.
3. Firmware stamps everything with `millis() + offset`.
4. Firmware **writes a sync row to the SD log** each time (`millis`, `phone_ms`), so SD data
   recorded *before* the first sync can still be corrected afterwards.
5. Until the first sync, firmware sets `"synced": false` on BLE messages (app discards or
   re-times them).

Why re-sync every 60 s: the ESP32 clock drifts (~0.1–0.2 s per hour). BLE write latency adds
~10–50 ms of error to each sync, which is ~1 m at highway speed — acceptable.

---

## 3. BLE interface [Henry, app]

| Item | UUID | Properties | Direction |
|---|---|---|---|
| Service | `6f9e0001-3c2a-4b8e-9d41-7a5c2e8b1f30` | — | — |
| DATA characteristic | `6f9e0002-3c2a-4b8e-9d41-7a5c2e8b1f30` | Notify | device → app |
| TIME characteristic | `6f9e0003-3c2a-4b8e-9d41-7a5c2e8b1f30` | Write | app → device |

- UUIDs are random; any values work as long as both sides match. The app scans for the service UUID.
- Device advertises with the name `RoadQuality`.
- App requests **MTU 247** → max payload **244 bytes** per notification. Every message must fit
  in one notification (all below are ~150 bytes).
- One JSON object per notification, UTF-8, no trailing newline.
- Suggested ESP32 library: **NimBLE-Arduino** (lighter and better documented than the default BLE stack).

---

## 4. Messages: device → app (DATA characteristic)

All messages share: `type` (string), `seq` (integer, +1 per message, any type; lets the app
detect drops), `synced` (bool, see §2).

### 4.1 Segment summary — `seg`, 1 per second
```json
{"type":"seg","seq":1042,"synced":true,"t_start":1792500000000,"t_end":1792500001000,
 "n":400,"rms_z":0.041,"peak_z":0.22,"roll_rms":1.8,"pitch_rms":2.1}
```

| Field | Unit | Meaning |
|---|---|---|
| `t_start`, `t_end` | epoch ms | First and last sample time of the 1 s window |
| `n` | count | Samples in the window (expect 400; fewer means dropped samples) |
| `rms_z` | g | **Roughness.** RMS of vertical acceleration, **gravity removed**, in the vehicle frame (after mounting-orientation calibration) |
| `peak_z` | g | Largest absolute vertical acceleration in the window |
| `roll_rms` | deg/s | RMS of roll rate (side-to-side rocking) |
| `pitch_rms` | deg/s | RMS of pitch rate (nose up/down) |

### 4.2 Event — `evt`, when detected
```json
{"type":"evt","seq":1043,"synced":true,"t":1792500000420,"class":"pothole",
 "peak_z":1.9,"roll_peak":45.0,"pitch_peak":12.0,"conf":0.7}
```

| Field | Unit | Meaning |
|---|---|---|
| `t` | epoch ms | Time of the **peak**, not the time the event was sent (detection can lag) |
| `class` | — | `"pothole"`, `"speed_bump"`, or `"unknown"` |
| `peak_z` | g | Peak vertical acceleration (gravity removed) |
| `roll_peak`, `pitch_peak` | deg/s | Peak roll / pitch rate during the event |
| `conf` | 0–1 | Detector confidence (use `1.0` if the detector doesn't produce one) |

### 4.3 Status — `status`, on connect (optional but very helpful for debugging)
```json
{"type":"status","seq":0,"synced":false,"fw":"0.1.0","imu_ok":true,"sd_ok":true,"rate_hz":400}
```

---

## 5. Messages: app → device (TIME characteristic)
```json
{"type":"time","t":1792500000000}
```
Written on connect and every 60 s (§2).

---

## 6. What the app does with each message [app]

- Records phone GPS fixes at 1 Hz using each fix's **own measurement time** (not arrival time).
- Stores fixes and incoming messages **as received** in the phone's local database (Room),
  so drives survive no signal / app restarts and can be re-processed or replayed later.
- Waits ~1–2 s for the GPS fix *after* each message, then **interpolates** between the two
  surrounding fixes:
  - segment → location at its midpoint `(t_start + t_end) / 2`
  - event → location at `t`
- If no fix exists within ±2 s, the segment is kept with **no location** (not dropped).
- Uploads stamped segments/events to Supabase in batches (~60 rows per request).

Fields the app adds to every uploaded segment and event: `lat`, `lon`, `speed_mps`,
`gps_accuracy_m`, `heading_deg`, `trip_id`, `source` (`REAL` / `FAKE_ASSET` / `FAKE_DEVICE`).
Events also get `vehicle_id`; segments get their vehicle through their trip.

| Field | Unit | Meaning |
|---|---|---|
| `lat`, `lon` | degrees | Interpolated position (see above) |
| `speed_mps` | m/s | GPS speed |
| `gps_accuracy_m` | m | Android's accuracy estimate for the fix (68% chance the true position is within this radius) |
| `heading_deg` | degrees | Direction of travel from GPS, clockwise from true north (0 = north, 90 = east). Lets the database keep the two directions of a road apart |

`lat`, `lon`, `speed_mps`, `gps_accuracy_m`, and `heading_deg` are `null` when no fix was close
enough in time. `heading_deg` is also `null` when the car is barely moving (GPS heading is
unreliable below ~1–2 m/s).

---

## 7. Supabase (suggestions only) [Ben]

**Tables** (snake_case; names are Ben's call)
- `vehicles`: `id`, `name`, `type` (truck/sedan/suv), `scale_factor`
- `trips`: `id`, `vehicle_id`, `start_time`, `end_time`, `source`, `app_version`
- `segments`: `trip_id`, `t_start`, `t_end`, `lat`, `lon`, `speed_mps`, `gps_accuracy_m`,
  `heading_deg`, `rms_z`, `peak_z`, `roll_rms`, `pitch_rms`, `source`
- `events`: `id`, `trip_id`, `vehicle_id`, `t`, `lat`, `lon`, `speed_mps`, `gps_accuracy_m`,
  `heading_deg`, `class`, `peak_z`, `roll_peak`, `pitch_peak`, `conf`, `user_label`, `source`

**Suggestions**
- App sends plain `lat` / `lon` numbers; the database computes a PostGIS geography column
  from them (generated column). Neither the app nor the web map reads PostGIS types directly.
- **Map logic lives in SQL functions**, so the app and the web map always show the same thing:
  - `road_quality_in_area(south, west, north, east, …filters)`: segments combined per small
    area **and per direction of travel** (`heading_deg`), with vehicle/speed normalization
    applied. Median rather than average, so one odd drive doesn't skew a road.
  - `events_in_area(south, west, north, east, …)`: events merged into one when they are within
    **10 m** of each other, have the same `class`, and headings within ~45° (so potholes on
    opposite sides of a road stay separate). Merged position = average of the hits. Returns the
    number of drives that hit it and the number of drives that passed that spot in the same
    direction; the map shows this as "hit by N of M drives".
  - 10 m is a starting value: GPS is typically off by 3–5 m per fix. Measure the real spread by
    driving over the same speed bump ~10 times, and adjust.
- Row Level Security: the app can insert + read; the web map can **only read**.
- Free-tier projects pause after ~1 week without activity (data is kept; resume from the dashboard).

---

## 8. Map display (shared by app and web map)

- Segments drawn as lines colored by (normalized) roughness, like Google Maps traffic:
  **green** (new pavement) → **yellow** → **light red** (≈ I-5) → **dark red** (worse).
- Numeric thresholds are **TBD** from calibration drives (new construction = green baseline,
  I-5 = light red) and will be listed here once measured, so both maps use identical values.
- Each direction of a road is drawn on its own side (offset to the right of travel, as US traffic
  drives on the right). Zoomed out, the two directions overlap.
- Lines are about 5 m wide on the ground, so they widen as you zoom in.
- Distinct markers for pothole and speed bump.
- Grading is per direction, **not per lane**: phone GPS can't reliably tell lanes apart.

---

## 9. Decisions needed

**Henry (firmware)**
- [ ] OK with the fields and units in §4? Anything you'd add or drop?
- [ ] How is gravity removed and the vertical axis found (orientation calibration at rest)?
- [ ] OK with phone-time stamping + 60 s re-sync (§2), and sync rows in the SD log?
- [ ] BLE library (NimBLE-Arduino?) and JSON (ArduinoJson?) OK?
- [ ] Rough date for a firmware build that sends fake `seg`/`evt` messages over BLE?

**Ben (backend)**
- [ ] Final table and column names (§7)
- [ ] Anonymous sign-in for the app, or another auth approach?
- [ ] RLS policies: app read/write, web read-only
- [ ] Who writes the aggregation/normalization SQL functions, and when?
- [ ] OK with grouping by direction (`heading_deg`) and the event-merge rules in §7?

**Team**
- [ ] Confirm IMU rate 400 Hz and 1 s segment windows
- [ ] Vehicle list and IDs (truck, Camry, Escape)
- [ ] Where speed normalization happens (proposal: in the SQL functions, after calibration drives)
- [ ] Web map owner and timing (after the app → Supabase → map pipeline works)
- [ ] Per-direction (not per-lane) grading is acceptable for the project

---

## 10. Changes since v0.2

- Scott owns this file.
- App adds `heading_deg` (GPS direction of travel) to segments and events (§6, §7).
- Events table now has `gps_accuracy_m`, matching segments (§7).
- Clarified that `vehicle_id` is on events and trips; segments get it through their trip (§6).
- Unit table for the app-added fields, and when they are `null` (§6).
- Suggested per-direction grouping and event-merge rules for the SQL functions (§7).
- Map display: per-direction lines, zoom-scaled width, per-direction not per-lane (§8).
