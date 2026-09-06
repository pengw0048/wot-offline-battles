/*
 * Portable arithmetic core for the world-collision sweep preparation.
 *
 * This file performs no Python, Win32 or BigWorld call.  It reads one
 * caller-owned array of doubles and writes its results back into the same
 * array, so the same object code can be built into the exact-build #1513
 * extension and into a host library for differential tests.
 *
 * The layout matches
 * src/res/scripts/client/gui/mods/offline_lan_0922/world_collision_prep.py.
 * Both implementations evaluate only + - * / and comparisons, in the same
 * order, on IEEE-754 doubles, so the results must agree exactly.
 */

#ifndef OFFLINE_COMPUTE_CORE_H
#define OFFLINE_COMPUTE_CORE_H

#define OFFLINE_COMPUTE_INPUT_VALUES 17
#define OFFLINE_COMPUTE_HEADER_VALUES 11
#define OFFLINE_COMPUTE_LANE_VALUES 24
#define OFFLINE_COMPUTE_MAXIMUM_LANES 5
#define OFFLINE_COMPUTE_BUFFER_VALUES \
	(OFFLINE_COMPUTE_INPUT_VALUES + OFFLINE_COMPUTE_HEADER_VALUES + \
	 OFFLINE_COMPUTE_MAXIMUM_LANES * OFFLINE_COMPUTE_LANE_VALUES)

#define OFFLINE_COMPUTE_OK 0
#define OFFLINE_COMPUTE_BUFFER_TOO_SMALL 1
#define OFFLINE_COMPUTE_INPUT_NOT_FINITE 2
#define OFFLINE_COMPUTE_LANE_OVERFLOW 3

/* Argument-reader statuses, reported by the exact-build module only. */
#define OFFLINE_COMPUTE_HOST_UNVALIDATED 11
#define OFFLINE_COMPUTE_ARGUMENT_COUNT 12
#define OFFLINE_COMPUTE_ARGUMENT_TYPE 13
#define OFFLINE_COMPUTE_ADDRESS_UNREADABLE 14
#define OFFLINE_COMPUTE_LAYOUT_UNPROVEN 15

/* Returns OFFLINE_COMPUTE_OK, or a status leaving the buffer unchanged. */
int offline_compute_prepare_sweep(double *values, int count);

#endif
