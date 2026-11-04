/*
 * File:        ble_link.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-03
 * Version:     0.2.0-draft (new file)
 * Description: ble_link task: sole owner of BLE and the only code that
 *              formats JSON for the app (docs/data-contract.md sections 3-5).
 */

#ifndef BLE_LINK_H
#define BLE_LINK_H

// ============================================================================
// Prototypes
// ============================================================================

/**
 * ble_link task entry (core 0). Starts NimBLE, advertises, sends status on
 * every subscribe, and turns each segment and event from ble_q into one JSON
 * notification while the app is subscribed.
 * @param param  Pointer to the app_ctx_t from setup(). Must outlive the task.
 * @return Never returns.
 */
void ble_link_task(void *param);

#endif  // BLE_LINK_H
