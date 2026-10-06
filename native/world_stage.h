#ifndef WOT_WORLD_STAGE_H
#define WOT_WORLD_STAGE_H
#include <stdint.h>

// Same-thread transient ABI. No worker, persisted pointer, wire packet or hash.
// Request/response arrays belong to run() and expire when dispatch() returns.
// The dispatcher retains Python engine objects, live filters and hit handles.
struct WorldSnapshot {
  double position[3];
  double yaw, speed, dt, pitch, roll, motion_yaw;
  double bounds[4]; // left, right, back, front
  double heights[2];
  uint32_t passive, airborne, exact_footprint, supplied_departing;
};

enum WorldOpcode {
  WORLD_FILTER = 1,
  WORLD_DEPARTING = 2,
  WORLD_HORIZONTAL = 3,
  WORLD_GROUND = 4,
  WORLD_RESOLVE = 5,
  WORLD_TRACE = 6
};

struct WorldRequest {
  double start[3], end[3];
  double auxiliary[32];
  uint64_t handle;
  uint32_t flags, reason;
};

struct WorldResponse {
  double point[3], normal[3], start[3], end[3];
  uint64_t handle; // opaque main-dispatcher-owned original hit
  int32_t status; // rays: 0 miss, 1 hit; resolve: 0 blocked, 1 clear, 2 kinetic
};

// count <= 7, identical opcode per frontier. Zero means exception; do not retry
// Python after an effect. Batches execute in order. Ground batches stop at the
// first miss when flags&1, matching the original early-missing profile law.
typedef int (*WorldDispatch)(void *, uint32_t, const WorldRequest *, uint32_t,
                             WorldResponse *);

// Result: 0 clear, 1 hard, 2 kinetic, -1 dispatcher exception.
extern "C" int wot_world_run(const WorldSnapshot *, WorldDispatch, void *);
#endif
