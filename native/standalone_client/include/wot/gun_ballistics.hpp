#pragma once

#include "wot/point_velocity.hpp"

#include <array>
#include <vector>

namespace wot {

using GunPitchCurve = std::vector<std::array<float, 2>>;

// #1513 0x00f58e70. Curves retain descriptor order; yaw is translated by one
// positive revolution only when negative, rather than generally normalized.
std::array<float, 2> gun_pitch_limits(float turret_yaw,
    const GunPitchCurve& minimum, const GunPitchCurve& maximum);

// Geometry prepared by the native D3DX inverse/transform operations used by
// wg_getShotAngles. This keeps engine Matrix semantics out of the pure solver.
struct GunAimGeometry {
    Vector3 target;
    Vector3 gravity;
    float gun_lateral;
    float gun_forward;
    float world_height_delta;
    float world_horizontal_distance;
};

// #1513 0x00f45f40 and its three original iterative solver branches. Returned
// angles are BigWorld yaw/pitch, including negative pitch for upward firing.
std::array<float, 2> gun_shot_angles(const GunAimGeometry& geometry,
    float speed, float gravity, float current_yaw, float current_pitch,
    bool adjust = true);

// #1513 wg_computeProjectileTrajectory's native adaptive chord producer.
std::vector<Vector3> gun_projectile_trajectory(Vector3 begin, Vector3 velocity,
    Vector3 gravity, float time, float epsilon);

} // namespace wot
