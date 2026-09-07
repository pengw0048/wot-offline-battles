#ifndef OFFLINE_ASTAR_CORE_H
#define OFFLINE_ASTAR_CORE_H

/* Experimental, synchronous C ABI. The caller owns the complete buffer.
 * Commands and their layouts are defined alongside the Python adapter.
 * No Python object or BigWorld object crosses this boundary. */
#ifdef __cplusplus
extern "C" {
#endif
int offline_astar_dispatch(double *buffer, int count);
#ifdef __cplusplus
}
#endif
#endif
