#pragma once

namespace wot {

struct Vector3 {
    float x;
    float y;
    float z;
};

// The arithmetic kernel exposed by #1513 WGVehiclePhysics.getPointVelocity.
// All inputs must share the original frame. This function neither acquires
// vehicle state nor integrates motion, contacts, suspension, or damage.
// Bitwise comparison assumes default IEEE rounding and finite intermediate
// results. NaN payload/sign propagation is platform-dependent.
Vector3 point_velocity(Vector3 point, Vector3 origin,
                       Vector3 angular_velocity, Vector3 linear_velocity);

} // namespace wot
