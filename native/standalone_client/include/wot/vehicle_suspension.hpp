#pragma once

#include "wot/vehicle_control.hpp"

#include <array>
#include <cstddef>
#include <optional>
#include <vector>

namespace wot::offline_vehicle_control {

using SuspensionVector = std::array<double, 3>;
using GroundHeights = std::vector<std::optional<double>>;
enum class ContactSide { none, left, right };

struct SuspensionPoint {
    double x = 0;
    double y = 0;
    double z = 0;
    ContactSide side = ContactSide::none;
};
struct SuspensionSpring : SuspensionPoint {
    double static_compression = 0;
    double max_compression = 0;
    double stiffness = 0;
    double damping = 0;
    double max_force = 0;
};
struct SuspensionPseudoContact : SuspensionPoint {
    double penetration = 0.01;
};
struct SuspensionParameters {
    double mass = 0;
    // Geometry/query metadata published by the same derive_suspension_params
    // producer. The numeric step consumes the inertia and contact records.
    double width = 0;
    double length = 0;
    double clearance = 0;
    double rest_length = 0;
    double static_compression = 0;
    double hard_ratio = 0;
    double contact_memory_distance = 0;
    double pitch_inertia = 0;
    double roll_inertia = 0;
    double fixed_step = 0;
    std::size_t constraint_iterations = 0;
    std::vector<SuspensionSpring> springs;
    std::vector<SuspensionPseudoContact> pseudo_contacts;
};
struct SuspensionState {
    double height = 0;
    double vertical_velocity = 0;
    double pitch = 0;
    double pitch_velocity = 0;
    double roll = 0;
    double roll_velocity = 0;
};
struct SuspensionResult {
    SuspensionState state;
    std::size_t contact_count = 0;
    bool airborne = true;
    bool left_flying = true;
    bool right_flying = true;
    bool contacted_this_step = false;
    std::size_t touched_contact_count = 0;
    std::optional<double> impact_speed;
    double max_compression = 0;
    double max_limit_excess = 0;
};

SuspensionVector suspension_point_offset(const SuspensionPoint& point,
    double pitch = 0, double roll = 0);
std::vector<std::array<double, 2>> suspension_world_points(
    const SuspensionParameters& parameters, SuspensionVector position,
    double yaw, double pitch = 0, double roll = 0);
std::vector<std::array<double, 2>> suspension_pseudo_world_points(
    const SuspensionParameters& parameters, SuspensionVector position,
    double yaw, double pitch = 0, double roll = 0);
SuspensionResult damper_suspension_step(const SuspensionParameters& parameters,
    SuspensionState state, const GroundHeights& ground_heights, double elapsed,
    std::optional<GroundHeights> pseudo_ground_heights = {},
    double support_vertical_velocity = 0, const Tuning& tuning = {});
double suspension_limit_excess(const SuspensionParameters& parameters,
    const SuspensionState& state, const GroundHeights& ground_heights);

struct SuspensionLocalPlane {
    double center_y;
    double x_gradient;
    double z_gradient;
    double max_residual;
    std::size_t contact_count;
};
struct GroundPlane {
    double center_y = 0;
    double gradient_x = 0;
    double gradient_z = 0;
    SuspensionVector normal{};
    // The five-sample fit does not publish a world-space origin. Absence
    // remains explicit until the query owner supplies its actual position.
    std::optional<double> center_x;
    std::optional<double> center_z;
    std::optional<double> pitch;
    std::optional<double> roll;
    std::optional<double> slope_tangent;
    std::optional<double> up_cosine;
    std::optional<SuspensionVector> downhill;
    std::optional<double> residual;
    std::optional<double> max_residual;
    std::optional<std::size_t> contact_count;
};
std::optional<SuspensionVector> ground_normal(double gradient_x,double gradient_z);
std::optional<GroundPlane> sampled_ground_plane(std::optional<double> front_y,
    std::optional<double> rear_y,std::optional<double> right_y,
    std::optional<double> left_y,std::optional<double> center_y,double yaw,
    double length,double width,double maximum_residual);
std::optional<SuspensionLocalPlane> suspension_ground_plane(
    const SuspensionParameters& parameters,const GroundHeights& ground,
    std::optional<double> maximum_residual = {},
    std::optional<std::vector<std::array<double,2>>> sample_points = {});
std::optional<GroundPlane> suspension_world_ground_plane(
    const SuspensionParameters& parameters,const GroundHeights& ground,
    SuspensionVector position,double yaw,std::optional<double> maximum_residual = {},
    double pitch = 0,double roll = 0);
std::optional<double> suspension_plane_height(std::optional<GroundPlane> plane,
    double x,double z);
std::optional<double> suspension_support_vertical_velocity(
    std::optional<GroundPlane> plane,double velocity_x,double velocity_z);
std::optional<double> suspension_drive_pitch(std::optional<GroundPlane> plane,double yaw);
bool suspension_ground_planes_continuous(std::optional<GroundPlane> previous,
    std::optional<GroundPlane> current,SuspensionVector start_position,
    SuspensionVector end_position,double height_tolerance,double gradient_tolerance);
std::vector<double> suspension_path_probe_fractions(double distance);

struct RetainedGroundContact {
    double origin_x = 0;
    double origin_z = 0;
    double height = 0;
    double last_x = 0;
    double last_z = 0;
    double travelled = 0;
    bool last_query_missed = false;
};
struct RetainedGroundResult {
    std::optional<double> height;
    std::optional<RetainedGroundContact> memory;
};
RetainedGroundResult retained_ground_contact(std::array<double, 2> point,
    std::optional<double> ground, std::optional<RetainedGroundContact> memory,
    double maximum_distance,
    std::optional<std::array<double, 2>> support_gradient = {});
bool suspension_support_allowed(double height, double normal_y,
    std::optional<double> flat_maximum_y = {});
double suspension_vertical_sweep_drop(double vertical_speed, double elapsed,
    const Tuning& tuning = {});
double landing_impact_speed(SuspensionVector world_velocity,
    std::optional<SuspensionVector> support_normal = {});
double ground_follow_gap(double speed, double slope_pitch, double elapsed,
    const Tuning& tuning = {});
double launch_vertical_speed(double speed, double slope_pitch);
double slope_slide_speed(double current, double slope_tangent, double elapsed,
    const Tuning& tuning = {});
double suspension_slope_slide_speed(double current, double slope_tangent,
    double elapsed, const Tuning& tuning = {});
int fall_damage(int maximum_health, double impact_speed, const Tuning& tuning = {});
int overturn_level_from_up_cosine(double up_cosine,
    std::optional<double> warning_cosine = {}, std::optional<double> danger_cosine = {});
int overturn_level(double pitch, double roll,
    std::optional<double> warning_cosine = {}, std::optional<double> danger_cosine = {});

std::array<double, 4> hard_contact_candidate_yaws(double yaw);
struct HardContactResult { double speed; double dx; double dz; };
HardContactResult hard_contact_step(double speed, double elapsed,
    bool grinding = false, std::optional<double> slide_yaw = {});
std::array<double, 2> contact_push_deceleration(const Parameters& parameters,
    bool rolling, std::size_t terrain_index = 0, double normal_y = 1,
    const Tuning& tuning = {});
std::array<double, 2> contact_push_step(const Parameters& parameters,
    double push_x, double push_z, double yaw, double elapsed,
    bool rolling = false, std::size_t terrain_index = 0, double normal_y = 1,
    const Tuning& tuning = {});
bool contact_push_is_held(const Parameters& parameters, double push_x,
    double push_z, double yaw, double elapsed, bool rolling = false,
    std::size_t terrain_index = 0, double normal_y = 1, const Tuning& tuning = {});

} // namespace wot::offline_vehicle_control
