# libcheck: TensorFlow Lite Micro check (request D)

A throwaway sketch that proves TensorFlow Lite Micro (TFLM) builds and runs on
the ESP32-S3 with esp32 core 3.3.10, before `train.py` (request E) and live
inference in `firmware-bench` (request F) depend on it. Part of
`training-live-inference-handoff.md`. Nothing in `firmware-bench/` or
`road_quality_fw/` is touched.

## The library: already inside core 3.3.10

Core 3.3.10 already ships Espressif's **esp-tflite-micro 1.3.5** with
**ESP-NN 1.2.3**, precompiled for the S3, in
`%LOCALAPPDATA%\Arduino15\packages\esp32\tools\esp32s3-libs\3.3.10`. Every S3
sketch already has its headers and its library, so **nothing is installed**
(owner decision D1, 2026-11-24). Anyone with core 3.3.10 can build this.

- The core passes `-DTF_LITE_STATIC_MEMORY` to sketches, so the sketch sees the
  same struct layout the library was built with.
- ESP-NN speeds up int8 kernels only. Our model is float32 (P2), so it runs on
  TFLM's plain reference kernels.
- TFLM's own error messages are built into the core's library and go to the
  board's system console: the DevKit's **UART** connector, and also **USB**, in
  between the sketch's lines (for example `Failed to allocate tail memory...`
  just before each `SHORT,...,pass` line). They can't be redirected.
- The sketch refuses to build on any other core version, because a different
  core carries a different TFLM.

## Files

| File | What it is |
| --- | --- |
| `libcheck.ino` | The sketch |
| `hello_world_models.h` | Declares the two model arrays |
| `hello_world_int8_model.cc` | The library's own example model (int8 sine model), from espressif/esp-tflite-micro tag v1.3.5. Bytes unchanged; only the names, include and alignment were changed |
| `hello_world_float_model.cc` | The float32 version of the same sine model, from tensorflow/tflite-micro commit 8efacb0. Bytes unchanged |
| `LICENSE-tflite-micro.txt` | Apache License 2.0, which covers both model files and the code adapted from the examples |

## What the sketch checks

At boot, and again whenever you send any key from the Serial Monitor:

1. **Ops (LIB-3).** Registers all ten ops on request E's list (`CONV_2D`,
   `MAX_POOL_2D`, `AVERAGE_POOL_2D`, `MEAN`, `FULLY_CONNECTED`, `SOFTMAX`,
   `RESHAPE`, `EXPAND_DIMS`, `SUB`, `MUL`) and prints `OPS,<op>,ok` for each.
2. **Each model (LIB-2).** Runs the int8 and the float32 sine model on an 8 KB
   arena, with only `FULLY_CONNECTED` registered:
   - `MODEL`: input and output types and the arena bytes actually used;
   - `GOLD`: the four test inputs from TFLM's own `hello_world_test.cc` must
     give |sin(x) − y| ≤ 0.05;
   - `TIME`: 100 runs of `Invoke()`, with mean, min and max µs per run;
   - `XY` and `SWEEP`: the example's 20-point sweep over 0 to 2π (y should
     follow sin x).
3. **Arena too small (LIB-4).** Repeats each model on an arena short by 1 KB or
   by half the bytes it used, whichever is smaller (owner, option b), and
   expects `SHORT,<model>,...,alloc=failed,pass`.

Then `DONE,run=1,ops=10/10,int8=pass,float=pass`. After that a `SUM` line
repeats every 5 s; while those keep coming, the board has not crashed or
rebooted. `SUM` also shows the free stack (bytes) and the lowest free heap.

The report printed at boot is usually lost before the Serial Monitor opens the
port (on the first run only the `SUM` lines arrived), so always send any key to
run the checks again and read that report.

## Build and run

Board settings are the same as `firmware-bench`: Arduino IDE 2.3.10, esp32 core
3.3.10, **ESP32S3 Dev Module**, Flash Size **8MB**, PSRAM **Disabled**, USB CDC
On Boot **Enabled**, USB Mode **Hardware CDC and JTAG**. No libraries.

1. **LIB-1 and LIB-3, build.** Open `libcheck.ino`, set File > Preferences >
   Compiler warnings to **All**, and click Verify. Copy the whole output.
2. **Flash and RAM that TFLM adds.** Change `#define LIBCHECK_BASELINE   0` to
   `1` near the top of `libcheck.ino`, Verify again, copy the last two lines,
   then set it back to `0`. The baseline builds the same sketch without TFLM.
3. **LIB-2 and LIB-4, run.** Plug in the DevKit's **USB** connector, Upload,
   open the Serial Monitor, and send any key (type a letter and press Enter).
   Wait for `DONE` and two `SUM` lines, then copy everything.

Optional: the same builds from a terminal, with the arduino-cli inside the
Arduino IDE. Run them from this folder.

Command Prompt:

```
"%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe" compile -b esp32:esp32:esp32s3:FlashSize=8M,PSRAM=disabled,CDCOnBoot=cdc,USBMode=hwcdc,UploadMode=default --warnings all .
"%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe" compile -b esp32:esp32:esp32s3:FlashSize=8M,PSRAM=disabled,CDCOnBoot=cdc,USBMode=hwcdc,UploadMode=default --warnings all --build-property "compiler.cpp.extra_flags=-DLIBCHECK_BASELINE=1" --build-path "%TEMP%\libcheck-baseline" .
```

PowerShell:

```
& "$env:LOCALAPPDATA\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe" compile -b esp32:esp32:esp32s3:FlashSize=8M,PSRAM=disabled,CDCOnBoot=cdc,USBMode=hwcdc,UploadMode=default --warnings all .
& "$env:LOCALAPPDATA\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe" compile -b esp32:esp32:esp32s3:FlashSize=8M,PSRAM=disabled,CDCOnBoot=cdc,USBMode=hwcdc,UploadMode=default --warnings all --build-property "compiler.cpp.extra_flags=-DLIBCHECK_BASELINE=1" --build-path "$env:TEMP\libcheck-baseline" .
```

## Tests

Every test starts NOT TESTED, and only the owner marks PASS.

| ID and type | Procedure and acceptance criterion | Required evidence | Status |
| --- | --- | --- | --- |
| LIB-1 Build | Verify `libcheck.ino` for ESP32S3 Dev Module on core 3.3.10. Pass: builds, with no warnings from the sketch or TFLM files | Full Verify output with board, core version, warnings, flash and RAM use, and the baseline's flash and RAM | NOT TESTED |
| LIB-2 Normal | Upload and run. Pass: every `GOLD` line passes (\|sin(x) − y\| ≤ 0.05, the tolerance of TFLM's `hello_world_test.cc`) for both models; `TIME` and `MODEL` print mean and max µs and arena used | Serial capture | NOT TESTED |
| LIB-3 Boundary | All ten ops registered in one `MicroMutableOpResolver` (in the same build as LIB-1). Pass: builds, and all ten `OPS` lines say `ok` | Verify output and `check_ops()` in `libcheck.ino` | NOT TESTED |
| LIB-4 Fault | Arena short by min(1 KB, half the bytes used). Pass: both `SHORT` lines say `alloc=failed,pass`, and `SUM` lines keep coming (no crash, no reboot loop) | Serial capture | NOT TESTED |
