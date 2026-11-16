/*
 * File:        board_pins.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-03
 * Version:     0.2.0-draft
 * Change:      Target is now the team PCB (pin-map.md v0.1). The GPIOs are
 *              the same ones the breadboard used, so no pin moved.
 * Description: GPIO number for every signal the firmware uses. No pin number
 *              appears in any other file. One pin set: the team's custom PCB
 *              with an ESP32-S3-WROOM-1-N16R8 and an LSM6DSO32X on SPI.
 *
 *              Any pin change is a PCB or wiring change: Henry approves it.
 *
 *              On the PCB but not used by this firmware, so never configured
 *              here (pin-map.md v0.1):
 *              - GPIO47: IMU INT2. The IMU drives it; leave it an input.
 *              - GPIO39, 40, 41 (SD D0, CLK, CMD), 38, 2, 42 (SD D1-D3),
 *                48 (card detect): microSD, wired, not used yet.
 *              - GPIO15, 16: LED1, LED2.
 *              - GPIO17, 18: J3 header.
 *              Never usable: GPIO35-37 (octal PSRAM), GPIO19 and 20 (native
 *              USB, carries Serial), GPIO0, 3, 45, 46 (strapping).
 */

#ifndef BOARD_PINS_H
#define BOARD_PINS_H

// ============================================================================
// Macros
// ============================================================================

// Select exactly one board.
#define BOARD_ROADQ_PCB

#if defined(BOARD_ROADQ_PCB)

#define PIN_SPI_SCK   14  // SPI SCK -> IMU SPC
#define PIN_SPI_MISO  12  // SPI MISO <- IMU SDO
#define PIN_SPI_MOSI  13  // SPI MOSI -> IMU SDI
#define PIN_IMU_CS    21  // IMU CS, active low. The library drives it as a GPIO
#define PIN_IMU_INT1  11  // IMU INT1: accel data-ready, active high, push-pull

#else
#error "board_pins.h: no board selected"
#endif

#endif  // BOARD_PINS_H
