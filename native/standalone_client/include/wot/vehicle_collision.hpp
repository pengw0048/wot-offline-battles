#pragma once

#include "wot/vehicle_dynamics.hpp"

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace wot {

class VehicleCollisionError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

struct VehicleCollisionTriangle {
    Vector3 vertices[3];
    // In authored bsp2 these are material-table indices, NOT collision flags.
    std::uint16_t material_index = 0;
    std::uint16_t padding = 0;
};

struct VehicleCollisionMesh {
    Vector3 bounds_min{};
    Vector3 bounds_max{};
    std::vector<VehicleCollisionTriangle> triangles;
    std::vector<std::string> materials;
};

// Decode the #1513 BSPv2 triangle array (16/40/40 ABI), preserving every
// triangle, material index and padding. The caller supplies bsp2_materials in
// its original order. This does not validate or traverse the BSP node topology.
// Optional native key/length/payload user-data records are accepted.
VehicleCollisionMesh parse_vehicle_collision_bsp2(
    const std::vector<std::uint8_t>& bytes,
    const std::vector<std::string>& materials);

struct VehicleCollisionComponent {
    std::string name;
    const VehicleCollisionMesh* mesh = nullptr;
    // Current component-local -> world row-vector transform, including its
    // parent hull/turret/gun pose. Never substitute the vehicle's spawn pose.
    VehicleMatrix transform{};
};

struct VehicleCollisionHit {
    double distance = 0;
    double fraction = 0;
    Vector3 point{};
    Vector3 authored_normal{};
    // #1513 WGBspCollisionModel faces its returned normal against the ray,
    // and flips a negative incidence cosine along with that normal.
    Vector3 normal{};
    double hit_angle_cos = 0;
    std::size_t component_index = 0;
    std::size_t triangle_index = 0;
    std::uint16_t material_index = 0;
};

// Two-sided, with a strict positive distance and an exclusive maximum endpoint,
// matching native WorldTriangle::intersects. Coplanar/degenerate triangles do
// not hit. Results are ordered by distance, component index, triangle index.
// Within one component, distance differences below float(0.0004), equal raw
// local normals and equal materials identify one native seam contact. Different
// materials, opposite normals and different components remain separate.
// The nearest deterministic representative is retained; BSP traversal order
// and final-bit parity with the original x86 implementation are not promised.
// Inputs must be finite; component transforms must be nonsingular affine poses.
std::vector<VehicleCollisionHit> collide_vehicle_segment(
    const std::vector<VehicleCollisionComponent>& components,
    Vector3 start, Vector3 end);

// Direction need not be normalized. Distance is always measured in world units.
std::vector<VehicleCollisionHit> collide_vehicle_ray(
    const std::vector<VehicleCollisionComponent>& components,
    Vector3 origin, Vector3 direction, double maximum_distance);

} // namespace wot
