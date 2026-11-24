 Bench Wiring and Expected Behavior

- File: `WIRING.md`
- Authors: Henry (owner); written with Claude
- Date: 2026-11-16
- Version: 0.2
- Change: `0.1.1-bench`. Gestures now depend on direction (pitch sequence). There is no `UNKNOWN`
  class. The IMU recovers from F1/F2 without a reset.
- Description: How to wire the ESP32-S3-DevKitC-1 to the Adafruit LSM6DSO32 breakout for the
  `0.1.1-bench` build, and what Serial should show once it runs. Pins come from `board_pins.h`
  (`BOARD_DEVKITC1_N8R8`), the same GPIOs as the team PCB.

---

## Parts

- ESP32-S3-DevKitC-1 (ESP32-S3-WROOM-1-N8R8)
- Adafruit LSM6DSO32 breakout
- Breadboard, 7 jumper wires, USB data cable

## Wiring

Unplug USB while wiring. The GPIO numbers are printed on the DevKit silkscreen.

| DevKit pin | Signal | Breakout pad |
|---|---|---|
| GPIO14 | SPI SCK | SCL |
| GPIO12 | SPI MISO | DO (**bottom row**) |
| GPIO13 | SPI MOSI | SDA |
| GPIO21 | IMU CS (active low) | CS (**bottom row**) |
| GPIO11 | IMU INT1 (data-ready) | INT1 |
| 3V3 | Power | VIN |
| GND | Ground | GND |

- **DO and CS:** use the bottom-row pads. The DO and CS pads on top are the auxiliary sensor
  port and will not work.
- **Leave unconnected:** 3Vo, INT2, the auxiliary pads, and the STEMMA QT connectors.
- **Never wire anything to:** GPIO35, 36, 37 (octal PSRAM), GPIO19, 20 (native USB), or GPIO0,
  3, 45, 46 (strapping pins).
- **USB:** plug into the DevKit connector marked **USB**, which carries Serial and upload. The
  **UART** connector is optional. It shows only ROM boot messages and crash output, which helps
  when the board is stuck in a reboot loop.

## Orientation

Lay the breakout flat with the chip facing up. At rest the `az` column reads about +1.0 g. The
end the X arrow points at is the car's **nose** (front).

The class comes from the **first** pitch movement. Nose down then back up is a pothole (the
front wheels drop in, then climb out). Nose up then back down is a speed bump. Every event is
`POTHOLE` or `BUMP`.

- **Pothole (`POTHOLE`, `pitch_seq` `DU`):** quickly tip the X-arrow end down, then back to
  flat.
- **Speed bump (`BUMP`, `pitch_seq` `UD`):** quickly tip the X-arrow end up, then back to flat.
- **One-wheel pothole (`POTHOLE`, basis `roll`):** tip the board around the X axis only (the
  edge the Y arrow points at goes up or down). Mostly `gx` moves.

Tilts must be quick, about 20–30° in about 0.2 s, to pass the 60 dps threshold. Hold still for
the first 2 s after boot or after an IMU recovery: that is when gyro bias is measured.

**First run: check the sign.** Tip the X-arrow end down quickly and watch `gy` in the `S`
lines. It should go **positive**. If it goes negative, set `PITCH_NOSE_DOWN_SIGN` to `-1` in
`config.h`, or every pothole will read as a bump.

## Expected behavior

Open the Serial monitor after flashing. The baud setting does not matter on native USB. Lines
printed before the monitor opens wait in the buffer, so `BOOT` still appears.

| When | Expected Serial output |
|---|---|
| Monitor opens | One `BOOT,fw=0.1.1-bench,...` line with every setting, then `#` column headers, then `BLE,advertising,0` |
| Every second | One `HLT` line: `samples` rises by about 416 and every drop count is 0. `serial_drops` can be nonzero only from time the monitor was closed |
| Every second | One `SEG` line: `n` is 416 or 417, and `synced` is 0 until a phone writes TIME. The first `SEG` after boot is partial |
| Continuously | About 52 `S` lines/s. Flat and still: `az` about 1.00, `ax` and `ay` about 0, gyro within a few dps of 0, `z_hp` about 0 |
| First 2 s after boot | No events (settle time) |
| One firm tap | One `EVT` line about 1.25 s later. A tap's class is not meaningful |
| Nose down-up / nose up-down / roll tilt | `EVT` with `POTHOLE,...,DU,pitch` / `BUMP,...,UD,pitch` / `POTHOLE,...,-,roll`. The last two columns are `pitch_seq` (first lobe, plus the opposite one if it followed within 300 ms) and `basis` (`pitch`, `roll`, or `z` when only vertical motion was seen) |
| Phone connects (app or nRF Connect) | Advertises as `RoadQuality`, then `BLE,connected`, `BLE,mtu,247`, `BLE,subscribed` |

## Troubleshooting

A fault stops the `HLT` lines, and an `FLT` line repeats once per second instead. The app
shows `imu_ok:false`.

- **IMU faults (F1, F2) recover by themselves.** The firmware retries every 0.5 s. Fix the
  wire or the power, and about 1.5–2 s later `HLT` lines return with `imu_recoveries` one
  higher. The app gets `imu_ok:true` again, and events resume after the 2 s settle time.
- **F3 and F4 latch** until you press RST.

| Symptom | Likely cause |
|---|---|
| `FLT,F1,IMU_INIT_FAILED` | IMU did not answer at boot. Check CS and DO (bottom row), SCK, SDA, and VIN/GND. It recovers once fixed |
| `FLT,F2,IMU_LOST` right after boot | INT1 not connected: no data-ready pulses |
| `FLT,F2,IMU_LOST` during a run | VIN, MISO, CS, or INT1 came loose. It recovers once reconnected |
| `FLT,F2` keeps coming back with `imu_recoveries` climbing | A loose breadboard wire. Reseat it |
| `FLT,F4,BLE_INIT_FAILED` | BLE stack did not start. Report it with the full log |
| `FLT,F3,HEARTBEAT_MISSED` | A task stopped responding. Report it with the full log |
| Only ROM boot text, no `BOOT` line | Monitor is on the UART connector. Use the USB connector |
| No serial port appears | Hold BOOT, press RST, release BOOT (download mode), then upload again |
| Build stops with `Bench build: set Tools > USB CDC On Boot...` | Set USB CDC On Boot: Enabled and USB Mode: Hardware CDC and JTAG |
| Board keeps rebooting | Plug in the UART connector as well and read the crash message there |
