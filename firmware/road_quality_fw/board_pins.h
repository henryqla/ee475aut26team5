/*
 * File:        board_pins.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-02
 * Version:     0.1.0-draft (first draft)
 * Description: GPIO number for every signal the firmware uses. No pin number
 *              appears in any other file. Phase 1 has one pin set: the
 *              breadboarded ESP32-S3-DevKitC-1 N8R8, wired with the same GPIOs
 *              as the PCB pin map so the code moves to the PCB unchanged. A PCB
 *              set gets added later behind its own BOARD_ define.
 *
 *              DRAFT wiring (devkit-phase1-context.md v0.4, "Breadboard
 *              wiring"). Any pin change is a wiring change: Henry approves it.
 *              Never use GPIO35-37: the module's octal PSRAM uses them.
 */

#ifndef BOARD_PINS_H
#define BOARD_PINS_H

// ============================================================================
// Macros
// ============================================================================

// Select exactly one board.
#define BOARD_DEVKITC1_BREADBOARD

#if defined(BOARD_DEVKITC1_BREADBOARD)

#define PIN_SPI_SCK   14  // IMU SCL / SPC
#define PIN_SPI_MISO  12  // IMU SDO / DO
#define PIN_SPI_MOSI  13  // IMU SDA / SDI
#define PIN_IMU_CS    21  // IMU CS, active low. The library drives it as a GPIO
#define PIN_IMU_INT1  11  // IMU INT1: accel data-ready, active high, push-pull

#else
#error "board_pins.h: no board selected"
#endif

#endif  // BOARD_PINS_H
