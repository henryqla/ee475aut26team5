# Bench Wiring and Expected Behavior

- File: `WIRING.md`
- Authors: Henry (owner); written with Claude
- Date: 2026-11-23
- Version: 0.3
- Change: 0.3 (`0.1.4-bench`, manual-recording-overhaul-handoff.md, request A): the board
  detects nothing and streams every sample as an `L` line, so the gesture and `EVT` checks
  are gone and the expected output describes `L` lines.
  2026-11-23: the board is turned so the Y arrow points to the nose (pitch = `gx`,
  roll = `gy`). A `CFG` line repeats the `BOOT` settings every 5 s.
  `0.1.1-bench`: the IMU recovers from F1/F2 without a reset.
- Description: How to wire the ESP32-S3-DevKitC-1 to the Adafruit LSM6DSO32 breakout for the
  `0.1.4-bench` build, and what Serial should show once it runs. Pins come from `board_pins.h`
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
end the Y arrow points at is the car's **nose** (front), so the X arrow points to the car's
right. Pitch is `gx` and roll is `gy` (`map_axes()` in `dsp.cpp`). `BOOT` prints the mapping
as `vert=az,roll=gy,pitch=gx`.

Hold still for the first 2 s after boot or after an IMU recovery: that is when the gyro bias is
measured. The last column of every `L` line (`ok`) is 0 during that time and 1 after it.

The board does not detect or classify anything. To record an event, press P, B or N in the
record GUI (`firmware/cnn/cnn_record_gui/`); the GUI saves a clip around the press.

## Expected behavior

Open the Serial monitor after flashing. The baud setting does not matter on native USB. Lines
printed before the monitor opens wait in the buffer, so `BOOT` still appears. The `L` lines
come about 416 times a second, so the Arduino Serial Monitor scrolls fast; the record GUI is the
better way to watch them.

| When | Expected Serial output |
|---|---|
| Monitor opens | One `BOOT,fw=0.1.4-bench,...` line with every setting (no thresholds), then `#` column headers, then `BLE,advertising,0` |
| Continuously | About 416 `L` lines/s: `L,n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok`. `n` goes up by exactly 1 each line. Flat and still: `az` about 1.00, `ax` and `ay` about 0, gyro within a few dps of 0, `z_hp`, `roll` and `pitch` about 0 |
| First 2 s after boot | `ok` is 0 (settle time: the gyro bias is being measured), then 1 |
| Every second | One `HLT` line: `samples` rises by about 416 and every drop count is 0. `serial_drops` can be nonzero only from time the monitor was closed |
| Every 5 s | One `CFG,fw=...` line: the `BOOT` settings again, so a program that connected after `BOOT` went by still gets them |
| Every 10 s | One `#LPERF` comment line: L lines handled, their average and longest write time, and the lowest free heap |
| Every second | One `SEG` line: `n` is 416 or 417, and `synced` is 0 until a phone writes TIME. The first `SEG` after boot is partial |
| Taps or shaking | The values in the `L` lines move. No other lines appear: there are no `EVT`, `S` or snapshot lines in this build |
| Phone connects (app or nRF Connect) | Advertises as `RoadQuality`, then `BLE,connected`, `BLE,mtu,247`, `BLE,subscribed`. The app gets `status` and `seg` messages, never `evt` |

## Troubleshooting

A fault stops the `HLT` lines, and an `FLT` line repeats once per second instead. The app
shows `imu_ok:false`.

- **IMU faults (F1, F2) recover by themselves.** The firmware retries every 0.5 s. Fix the
  wire or the power, and about 1.5–2 s later `HLT` lines return with `imu_recoveries` one
  higher. The app gets `imu_ok:true` again. `L` lines resume with `ok` 0 for the 2 s settle
  time, then 1.
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
