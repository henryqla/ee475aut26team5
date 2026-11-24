/*
 * File:        hello_world_models.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-24
 * Version:     0.1.0
 * Change:      0.1.0 (training-live-inference-handoff.md, request D): new file.
 * Description: The two TensorFlow Lite Micro example models libcheck runs,
 *              both the sine model of TFLM's hello_world example:
 *              - int8: the library's own example, from espressif/esp-tflite-micro
 *                v1.3.5 (hello_world_int8_model.cc);
 *              - float32: from tensorflow/tflite-micro
 *                (hello_world_float_model.cc), because our model is float32 (P2)
 *                and ESP-NN speeds up int8 kernels only.
 *              Both arrays are Apache-2.0 (LICENSE-tflite-micro.txt); each .cc
 *              file names its source and what was changed.
 */

#ifndef HELLO_WORLD_MODELS_H
#define HELLO_WORLD_MODELS_H

// ============================================================================
// Globals
// ============================================================================

// TFLite flatbuffers, 16-byte aligned, with their lengths in bytes. Declared
// extern here so the const definitions in the .cc files get external linkage.
extern const unsigned char g_hello_world_int8_model[];
extern const int g_hello_world_int8_model_len;
extern const unsigned char g_hello_world_float_model[];
extern const int g_hello_world_float_model_len;

#endif  // HELLO_WORLD_MODELS_H
