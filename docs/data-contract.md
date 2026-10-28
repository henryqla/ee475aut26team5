# Road Quality Mapper — Data Contract

**Version 0.3 — DRAFT for team review (Oct 2026).** Owner: **Scott** (keeps this file up to date).
Everything here is a proposal. Sections marked **[Henry]** or **[Ben]** need that person's OK;
the app adapts. Once agreed, changes go through this file so firmware, app, and backend stay in sync.
What changed since v0.2 is listed in §10.

---

## 1. Pipeline overview

```mermaid
flowchart TD
    IMU["6-axis IMU, 416 Hz"] --> FW["ESP32 firmware [Henry]"]
    FW --> SD["microSD: raw 416 Hz log<br/>(backup, tuning, ML)"]
    FW -->|"BLE: 1 segment/s + events"| APP["Android app [Scott]"]
    GPS["Phone GPS, 1 Hz"] --> APP
    APP -->|"adds where the car was,<br/>matched by time"| ROOM["Room (database on the phone)"]
    ROOM -->|"batched uploads"| SB["Supabase [Ben]"]
    SB --> MAP["Map tab in the app"]
    SB --> WEB["Web map, read-only (stretch)"]
```
(GitHub draws this as a diagram. In an editor without diagram support, read the arrows:
IMU → firmware → SD card and BLE → app (+ GPS) → phone database → Supabase → maps.)

| Data | Rate | Lives in | Owner |
|---|---|---|---|
| Raw IMU samples | 416 Hz | microSD only | Henry / Kyle |
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
2. Firmware computes `offset = phone_ms − millis()` at the moment it receives the write, but
   **applies the new offset only at the next segment boundary**. Every segment's `t_start` and
   `t_end` use the same offset.
3. Firmware stamps everything with `millis() + offset`. An event's `t` uses **the same offset as
   the segment that contains its peak**.
4. Firmware **writes a sync row to the SD log** each time (`millis`, `phone_ms`), so SD data
   recorded *before* the first sync can still be corrected afterwards.
5. Until the first sync, firmware sets `"synced": false` on BLE messages (app discards or
   re-times them).

Why re-sync every 60 s: the ESP32 clock drifts (~0.1–0.2 s per hour). BLE write latency adds
~10–50 ms of error to each sync, which is ~1 m at highway speed — acceptable.

Why apply the offset only at segment boundaries: each re-sync shifts the offset by tens of ms. If
it changed mid-window, a segment's `t_start` and `t_end` would come from two different clocks,
making its duration and midpoint wrong, and an event could land slightly outside its own segment.

Every SD row also keeps the raw `millis()`, so it can always be re-timed afterwards.

Early drives that log only to the SD card (before BLE works) are for Henry's firmware tuning and
are not put on the map, so they need no time sync.

---

## 3. BLE interface [Henry, app]

| Item | UUID | Properties | Direction |
|---|---|---|---|
| Service | `6f9e0001-3c2a-4b8e-9d41-7a5c2e8b1f30` | — | — |
| DATA characteristic | `6f9e0002-3c2a-4b8e-9d41-7a5c2e8b1f30` | Notify | device → app |
| TIME characteristic | `6f9e0003-3c2a-4b8e-9d41-7a5c2e8b1f30` | Write | app → device |

- UUIDs are random; any values work as long as both sides match.
- One JSON object per notification, UTF-8, no trailing newline.
- Suggested ESP32 library: **NimBLE-Arduino** (lighter and better documented than the default BLE stack).

**Packet size**
- BLE's default packet carries only **20 bytes** of data (default MTU of 23 bytes, minus 3 bytes
  of header). Right after connecting, the app asks for a bigger MTU of **247**, which allows
  **244 bytes** per notification. ESP32-S3 (NimBLE) and Android both support this; newer Android
  versions may negotiate even more (up to 517), which is fine.
- Our messages are ~120–180 bytes (§4), so **each message fits in one notification**.
- A notification longer than MTU − 3 bytes is **cut off, not split**. So:
  - **App, on connect, in this order:** connect → request MTU → discover services → subscribe to
    DATA → write TIME. If the MTU comes back below 247, the app logs an error and disconnects.
  - **Firmware** sends DATA notifications only after the app has subscribed (which is always after
    the MTU is set), never sends a message over 244 bytes, and rounds decimals to 3 places
    (ArduinoJson prints up to 9 digits by default, which wastes space).
- Data rate is ~1 message/s × ~150 bytes ≈ 0.15 KB/s, far below what BLE can carry, so throughput
  and connection intervals aren't a concern.

**Why JSON and not a packed byte layout:** streaming every raw sample (e.g. 50 Hz) would need a
compact binary layout squeezed into 20 bytes, with an agreed byte order (little vs big endian).
We send 1 s summaries instead, so size doesn't matter, and JSON is readable in any BLE scanner app
(e.g. nRF Connect), needs no byte-order agreement, and lets us add fields without breaking the
app. If we ever stream raw samples over BLE, revisit this.

**Finding our device in a crowded room [Henry, app]**

The demo room will have dozens of Bluetooth devices (phones, laptops, other teams' ESP32s). The
app only ever shows and connects to **our** device:
- **First connection:** the app scans with a filter for our **service UUID** (table above). It's
  random and unique to this project, so the phone's Bluetooth chip ignores every other device in
  the room before the app even sees it. No long list to pick from. The name `RoadQuality` is a
  second check, and is what the app shows on screen.
- **After that:** the app remembers this exact device (its Bluetooth address) and reconnects to it
  directly and automatically, e.g. after the car is turned off and on.
- **Before the demo:** connect early and stay connected. A crowded room slows down scanning, but
  an open connection copes much better (BLE hops between channels to avoid interference).

What firmware must do for this:
- Advertise **continuously whenever no phone is connected**, and **restart advertising after a
  disconnect**. Without this the phone can never reconnect. AI-written firmware often forgets to
  start advertising at all.
- Put the **service UUID** in the advertising packet and the name in the **scan response**:
  flags (3 bytes) + 128-bit UUID (18) + name (13) don't fit in the 31-byte advertising packet.
- Advertising interval ~100 ms. The car powers it, so there's no battery reason to go slower, and
  the phone finds it faster.

Not used (for now): BLE's *directed* advertising, where the device advertises to one specific
phone's Bluetooth address. Android changes its Bluetooth address periodically for privacy unless
the two devices are **paired** (bonded), so directed advertising also needs pairing. The UUID
filter plus the remembered address already gives "only our device, reconnect fast" without
pairing. If reconnects turn out slow in testing, pairing + directed advertising can be added later.

---

## 4. Messages: device → app (DATA characteristic)

All messages share: `type` (string), `seq` (32-bit unsigned integer, +1 per message, any type;
lets the app detect drops), `synced` (bool, see §2).

`seq` **restarts at 0 on every boot**. A reboot always drops the BLE connection, so after a
reconnect the app checks `seq`:
- `seq` continues from where it was → no reboot; any gap means dropped messages.
- `seq` is back at 0 → the firmware rebooted; the gap is expected, not data loss.

At ~1 message/s a 32-bit `seq` takes over 100 years to wrap, so wrap-around needs no handling.

### 4.0 Car axes and mounting [Henry, Kyle]

This is about which way the **box faces inside the car**, not which way the car faces on the map.
The device never needs compass direction; the phone's GPS provides that (`heading_deg`, §6).

`rms_z`, roll, and pitch only mean something in the **car's** axes, not the IMU chip's:

| Axis | Points | Rotation around it |
|---|---|---|
| x | forward (the way the car drives) | **roll**: side-to-side rocking |
| y | left | **pitch**: nose up/down |
| z | up | yaw: turning (not sent) |

Why it matters: speed bumps show up mostly as **pitch** (both front wheels, then both rear),
potholes as **roll** (usually one wheel). If the axes are mixed up, that test stops working.

The IMU measures along its own chip's axes. To convert to the car's axes, firmware needs two things:
- **Which way is up:** from gravity, measured with the car parked at power-on. This also corrects
  for a tilted mount.
- **Which way the front of the car is:** gravity can't tell it this. If the box sits rotated 90°,
  a speed bump's nose-up/down motion lands on the chip's side-to-side axis, and potholes and speed
  bumps get mixed up. So the box must always be mounted the same way, with a known side toward
  the front of the car.

That's what the **"FRONT →" arrow** is: a mounting instruction marked on the enclosure. It
doesn't send anything to the device; firmware simply assumes the box was mounted that way.
- **Kyle:** mark the arrow on the enclosure. Ideally the case fits the PCB only one way and the
  mount fits the car only one way. Tell Henry which IMU chip axis the arrow points along (from the
  PCB layout and the axis diagram in the IMU's datasheet).
- **Henry:** hard-code that chip axis as forward, then apply the gravity tilt correction.
- **Whoever mounts it:** arrow toward the front of the car, roughly level, rigidly attached.
  Backwards only flips signs (harmless for RMS values); sideways swaps roll and pitch.

Optional later: detect forward automatically during a straight-line brake. Not needed if the
arrow is followed.

Gravity removal: after rotating into car axes, subtract 1 g from z, or high-pass filter z
(~0.5 Hz), which also removes the slow tilt from driving on hills.

IMU ranges: accelerometer **±8 g or ±16 g** (big hits shouldn't clip), gyroscope **±500 deg/s**.

The SD log keeps the **raw chip axes**, with the calibration (rotation) written at the top of
each file, so the rotation can be redone afterwards if it turns out wrong.

### 4.1 Segment summary — `seg`, 1 per second
```json
{"type":"seg","seq":1042,"synced":true,"t_start":1792500000000,"t_end":1792500001000,
 "n":416,"rms_z":0.041,"peak_z":0.22,"roll_rms":1.8,"pitch_rms":2.1}
```

| Field | Unit | Meaning |
|---|---|---|
| `t_start`, `t_end` | epoch ms | First and last sample time of the 1 s window |
| `n` | count | Samples in the window (expect about `rate_hz`, §4.3; a large shortfall means dropped samples) |
| `rms_z` | g | **Roughness.** RMS of vertical acceleration, **gravity removed**, in car axes (§4.0) |
| `peak_z` | g | Largest absolute vertical acceleration in the window |
| `roll_rms` | deg/s | RMS of roll rate (side-to-side rocking) |
| `pitch_rms` | deg/s | RMS of pitch rate (nose up/down) |

**IMU rate: 416 Hz.** The LSM6DSO32X has no 400 Hz rate; its rates step through fixed values
(…, 208, 416, 833 Hz, …). Firmware uses 416 Hz and doesn't resample to 400, since nothing
downstream needs exactly 400.

**`n` is not a constant**, even with nothing dropped:
- A 1000 ms window holds 416 or 417 samples depending on where its edges fall between samples.
- The IMU's rate comes from its own oscillator, the window from the ESP32's clock; they don't run
  at exactly the same rate, which adds more spread.

So the app compares `n` to `rate_hz` from the `status` message (§4.3), not a hard-coded number.
The drop threshold is set after measuring real `n` values on hardware. **Placeholder until then:
flag a segment if `n` < 400.**

### 4.2 Event — `evt`, when detected
```json
{"type":"evt","seq":1043,"synced":true,"t":1792500000420,"class":"pothole",
 "peak_z":1.9,"roll_peak":45.0,"pitch_peak":12.0,"conf":0.7}
```

| Field | Unit | Meaning |
|---|---|---|
| `t` | epoch ms | Time of the **largest peak within the lockout window** (below), not the time the event was sent |
| `class` | — | `"pothole"`, `"speed_bump"`, or `"unknown"` |
| `peak_z` | g | Peak vertical acceleration (gravity removed) |
| `roll_peak`, `pitch_peak` | deg/s | Peak roll / pitch rate during the event |
| `conf` | 0–1 | Detector confidence (use `1.0` if the detector doesn't produce one) |

**Lockout, lag, and ordering.** After the first above-threshold sample, firmware counts no new
events for a lockout window of 1.0–1.5 s (exact value not final), so the front and rear axles
hitting one pothole count once. The event is sent when the lockout ends, so it can report the
largest peak. This means:
- An `evt` may be sent up to the lockout duration (max 1.5 s) after its peak.
- An `evt` can arrive **after** the `seg` that contains its peak. The app must not assume events
  arrive before their segment.

§6 still works unchanged: the app interpolates location at `t`, not at arrival time.

Known limitation (for the backend): the lockout is time-based, so it covers different distances at
different speeds. At very low speed the rear-axle hit can land after the lockout and be counted
twice; at high speed two potholes close together can merge into one event.

### 4.3 Status — `status`, required, on every connect
```json
{"type":"status","seq":0,"synced":false,"fw":"0.1.0","imu_ok":true,"sd_ok":true,"rate_hz":416}
```

Firmware sends this on every connect. The app:
- copies `fw` into the trip's `fw_version` (§7), so each trip records which firmware logic
  (thresholds, filters, event rules) produced its data;
- uses `rate_hz` to check `n` (§4.1).

`fw` changes with every firmware change, including ones that don't affect scoring (e.g. a BLE
fix). Telling scoring changes apart needs a firmware changelog. A separate scoring version can be
added later if this becomes a problem.

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

## 7. Supabase [Ben]

**Agreed by Ben (Oct 2026),** except `trips.fw_version` (added for CR-1, waiting for Ben's OK).
Changes to anything here go through this file, like the rest.

**Connecting**
- The app reaches Supabase at the laptop's Tailscale Funnel URL (`https://<machine>.<tailnet>.ts.net`).
  Ben gives the app team the URL and the anon key. Both go in `local.properties`
  (`SUPABASE_URL`, `SUPABASE_ANON_KEY`), never in source code.
- The laptop is sometimes unreachable (restart, Wi-Fi drop). Uploads that fail should be retried
  later from Room; nothing is lost on the server side by sending late.

**Sign-in**
- On first launch the app calls Supabase **anonymous sign-in** (`signInAnonymously()`): no account,
  no password, nothing for the user to do. Supabase keeps the session on the phone and reuses it.
- Each app install gets its own user ID. The database uses it to know which trips belong to which
  phone, so one phone can't edit another phone's trips.
- The web map uses the anon key **without** signing in, which makes it read-only.

**General rules**
- **Times** are epoch milliseconds as `bigint`, the same numbers the app and firmware use. The
  database also keeps a readable copy of each time (`<column>_ts`, e.g. `start_time_ts`) for
  looking at data in Studio. The app doesn't send or need these.
- **`lat`/`lon` are `double precision`**: a 4-byte `real` keeps only ~7 digits, which isn't enough
  for meter-level positions like `47.661303`. Sensor values are fine as `real`.
- **IDs for trips and events are UUIDs made by the app**, so trips can be created offline and
  uploaded later.
- **Uploads only add rows, they never overwrite.** Send segments and events with the
  *ignore duplicates* option (REST header `Prefer: resolution=ignore-duplicates`, or
  `ignoreDuplicates = true` on upsert in supabase-kt). A re-sent row that is already stored is
  skipped quietly instead of causing an error, so retrying a whole batch is always safe.
- **Upload order:** a trip must be uploaded before its segments and events. The database checks
  that each segment/event belongs to a trip made by the same phone.
- **Allowed values are UPPERCASE**, the same as the Kotlin enum names; anything else is rejected:
  - `source`: `REAL`, `FAKE_ASSET`, `FAKE_DEVICE`
  - `event_class`, `user_label`: `POTHOLE`, `SPEED_BUMP`, `UNKNOWN`

  The BLE message keeps lowercase `"class":"pothole"` (§4.2); the app converts it.
- **Filled in by the database, not sent by the app:** `created_by` (the phone's user ID),
  `received_at` (when the row arrived), `geog`, and the readable `_ts` times.
- **Range checks:** `lat` −90…90, `lon` −180…180, `heading_deg` 0 to <360, `conf` 0…1,
  `speed_mps` and `gps_accuracy_m` ≥ 0, `t_end` ≥ `t_start`. Rows outside these are rejected.

`vehicles` — added by Ben (vehicle list: §9 Team). The app only reads this table.

| Column | Type | Notes |
|---|---|---|
| `id` | `text` **primary key** | Short readable IDs: `truck`, `camry`, `escape` |
| `name` | `text` not null | |
| `type` | `text` not null | `truck`, `sedan`, `suv` (plain text, lowercase) |
| `scale_factor` | `double precision` not null, default `1.0` | From calibration drives; set by Ben |

`trips`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` **primary key** | Made by the app when the trip starts |
| `vehicle_id` | `text` not null → `vehicles.id` | |
| `start_time` | `bigint` not null | |
| `end_time` | `bigint` | `null` while the trip is in progress; the app sets it once when the trip ends |
| `source` | `text` not null | |
| `app_version` | `text` | |
| `fw_version` | `text` | `fw` from the `status` message (§4.3). **Pending Ben (CR-1)** |
| `created_by` | `uuid` | Filled in by the database |
| `received_at` | `timestamptz` | Filled in by the database |

`segments` — **primary key is (`trip_id`, `t_start`)**: a trip can't have two segments starting at
the same millisecond, so a re-sent segment is recognized and skipped.

| Column | Type | Notes |
|---|---|---|
| `trip_id` | `uuid` not null → `trips.id` | |
| `t_start`, `t_end` | `bigint` not null | |
| `lat`, `lon` | `double precision` | `null` when no GPS fix (§6) |
| `speed_mps`, `gps_accuracy_m`, `heading_deg` | `real` | `null` when no GPS fix |
| `rms_z`, `peak_z`, `roll_rms`, `pitch_rms` | `real` not null | |
| `source` | `text` not null | Same as the trip's |
| `geog` | generated `geography(Point, 4326)` | Computed from `lon`/`lat`; `null` when they are |
| `received_at` | `timestamptz` | Filled in by the database |

`events`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` **primary key** | Made by the app |
| `trip_id` | `uuid` not null → `trips.id` | |
| `vehicle_id` | `text` not null → `vehicles.id` | Must be the trip's vehicle |
| `t` | `bigint` not null | |
| `lat`, `lon` | `double precision` | `null` when no GPS fix |
| `speed_mps`, `gps_accuracy_m`, `heading_deg` | `real` | `null` when no GPS fix |
| `event_class` | `text` not null | Called `class` over BLE; renamed because `class` is a reserved word in Kotlin |
| `peak_z`, `roll_peak`, `pitch_peak`, `conf` | `real` not null | |
| `user_label` | `text` | Set from the Drive screen's label buttons; the app may change it later |
| `source` | `text` not null | Same as the trip's |
| `geog` | generated `geography(Point, 4326)` | Computed from `lon`/`lat` |
| `received_at` | `timestamptz` | Filled in by the database |

Deleting a trip deletes its segments and events too. Only Ben does this (e.g. to clear fake data);
the app can't delete anything.

**Who can do what** (Row Level Security is on for every table; anything not listed is blocked)

| Table | App (signed in anonymously) | Web map (not signed in) |
|---|---|---|
| `vehicles` | read | read |
| `trips` | read all; add its own; set `end_time` on its own | read |
| `segments` | read all; add to its own trips | read |
| `events` | read all; add to its own trips; set `user_label` on its own | read |

**Map functions** (Ben writes them). Map logic lives in SQL functions, so the app and the web map
always show the same thing. The app calls them through the API (supabase-kt `rpc(...)`).
- `road_quality_in_area(south, west, north, east, include_fake default false)`: segments combined
  per small area **and per direction of travel**, with vehicle/speed normalization applied.
  Median rather than average, so one odd drive doesn't skew a road.
  Proposed result, one row per area and direction: `lat`, `lon`, `heading_deg`, `roughness`,
  `drives` (number of drives that contributed).
- `events_in_area(south, west, north, east, include_fake default false)`: events merged into one
  when they are within **10 m** of each other, have the same `event_class`, and headings within
  ~45° (so potholes on opposite sides of a road stay separate). Merged position = average of the
  hits. Proposed result, one row per merged event: `lat`, `lon`, `event_class`, `heading_deg`,
  `hits` (drives that hit it), `passes` (drives that passed that spot in the same direction),
  `last_t`. The map shows "hit by `hits` of `passes` drives". This is a different shape from
  `RoadEvent`, so the app will need a small model for it.
- Directions are compared around the circle: 350° and 10° count as the same direction.
- Rows with no heading (car barely moving) are left out of `road_quality_in_area`; events with no
  heading are merged by distance and class only. Rows with no location are stored but not shown.
- 10 m is a starting value: GPS is typically off by 3–5 m per fix. Measure the real spread by
  driving over the same speed bump ~10 times, and adjust.
- **Timing:** a first version (raw `rms_z` × `scale_factor`, no speed normalization) right after the
  tables exist; speed normalization after the calibration drives. The exact result columns are
  confirmed with Scott before each function is written.

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
- [x] OK with the fields and units in §4? Anything you'd add or drop? Yes, with CR-1 to CR-6
  applied (§10).
- [x] OK with the car axes, mounting, gravity removal, and IMU ranges in §4.0?
- [x] OK with phone-time stamping + 60 s re-sync (§2), and sync rows in the SD log?
- [x] OK with the packet-size and advertising rules in §3 (restart advertising after disconnect)?
- [x] BLE library (NimBLE-Arduino?) and JSON (ArduinoJson?) OK?
- [x] Rough date for a firmware build that sends fake `seg`/`evt` messages over BLE?

**Kyle (hardware)**
- [ ] "FRONT →" arrow on the enclosure, and a rigid, roughly level mount (§4.0)
- [ ] How the IMU chip's axes sit relative to the arrow (from the PCB layout), for Henry

**Ben (backend)**
- [x] Final table and column names, types, and primary keys: see §7. Changed from v0.3:
  `class` → `event_class` in the `events` table; allowed values are UPPERCASE; the database adds
  `created_by`, `received_at`, and readable `_ts` times; uploads skip duplicates instead of
  overwriting.
- [x] Auth: Supabase anonymous sign-in for the app; the web map stays signed out (§7 Sign-in).
- [x] RLS: app reads everything and adds rows to its own trips, and may only change `end_time`
  and `user_label` on its own rows; web map read-only; no deletes (§7 Who can do what).
- [x] SQL functions: Ben writes them. First version right after the tables exist; speed
  normalization after the calibration drives (§7 Map functions).
- [x] Grouping by direction and the event-merge rules: yes, with directions compared around the
  circle and a rule for rows with no heading (§7 Map functions).
- [ ] Add `fw_version text` to the `trips` table (§7, CR-1).
- [ ] **Scott:** confirm the result columns proposed for `road_quality_in_area` and
  `events_in_area` (§7) fit what the map screens need.

**Team**
- [ ] Confirm IMU rate 416 Hz (the IMU has no 400 Hz rate, §4.1) and 1 s segment windows
- [ ] Vehicle list and IDs (truck, Camry, Escape)
- [ ] Where speed normalization happens (proposal: in the SQL functions, after calibration drives)
- [ ] Web map owner and timing (after the app → Supabase → map pipeline works)
- [ ] Per-direction (not per-lane) grading is acceptable for the project

---

## 10. Changes since v0.2

Firmware change requests from Henry, CR-1 to CR-6:
- CR-1: `status` message is now required on every connect; the app stores `fw` in a new
  `trips.fw_version` column (pending Ben) (§4.3, §7).
- CR-2: IMU rate 400 Hz → 416 Hz, the nearest rate the LSM6DSO32X supports (§1, §4.1, §4.3, §9).
- CR-3: `n` is compared to `rate_hz` with a tolerance; placeholder threshold `n` < 400 until
  measured on hardware (§4.1).
- CR-4: new time offsets are applied only at segment boundaries; events use their segment's
  offset (§2).
- CR-5: event lockout, `t` is the largest peak, events may arrive up to 1.5 s late and after their
  segment (§4.2).
- CR-6: `seq` is 32-bit unsigned and restarts at 0 on every boot (§4).

Earlier v0.3 changes:
- Scott owns this file.
- App adds `heading_deg` (GPS direction of travel) to segments and events (§6, §7).
- Events table now has `gps_accuracy_m`, matching segments (§7).
- Clarified that `vehicle_id` is on events and trips; segments get it through their trip (§6).
- Unit table for the app-added fields, and when they are `null` (§6).
- Suggested per-direction grouping and event-merge rules for the SQL functions (§7).
- Map display: per-direction lines, zoom-scaled width, per-direction not per-lane (§8).
- Pipeline overview redrawn as a Mermaid diagram (§1).
- `millis()` kept on every SD row; SD-only test drives need no time sync (§2).
- BLE packet size (20-byte default, MTU 247), connect order, and why JSON (§3).
- Finding our device in a crowded room: UUID scan filter, remembered device, advertising rules (§3).
- Car axes, mounting arrow, gravity removal, and IMU ranges (§4.0).
- Column types, primary keys, and app-made UUIDs for the tables (§7).
