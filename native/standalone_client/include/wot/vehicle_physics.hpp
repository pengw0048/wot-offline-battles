#pragma once

#include "wot/vehicle_dynamics.hpp"

#include <cstdint>
#include <array>
#include <string>
#include <vector>

namespace wot {

class DynamicsSimulator;

// Typed source-side mode fields read by #1513 Vehicle::Settings (0x00f846c0).
// These have no substitute vehicle tunings; the original configuration
// producer must supply each required value before native construction.
struct VehicleBodyConfiguration {
    float gravity;
    float full_mass;
    Vector3 hull_inertia_factors;
    Vector3 hull_size;
    float hull_com_shift_y;
    float hull_box_offset_z;
    Vector3 turret_top_position;
    float turret_top_width;
    float simple_forward_max_speed;
    float simple_backward_max_speed;
    float simple_rotation_speed;
    float angular_velocity_factor;
    float allowed_rpm_excess;
    float overspeed_resistance_factor;
    bool rotation_around_center;
    float center_rotation_forward_speed;
    float movement_revert_speed;
    float com_friction_y_offset;
    float com_side_friction;
    bool stabilization_enabled;
    float gimlet_pushed_rotation;
    float gimlet_pushed_handbrake;
};

// #1513 Tank::Shape::Settings (0x00f8fe40).
struct VehicleShapeConfiguration {
    bool use_complex_form;
    float auxiliary_clearance;
    float tank_board_angle;
    float tank_aft_chamfer_fraction;
    float tank_front_chamfer_fraction;
    float terrain_board_angle;
    float terrain_front_chamfer_fraction;
    float terrain_aft_chamfer_fraction;
};

// #1513 ground record (0x00f830d0), in its native field order.
struct VehicleGroundSurfaceConfiguration {
    float dirt_cumulation_rate;
    float dirt_release_rate;
    float dirt_side_velocity;
    float max_dirt;
    float handbrake_com_side_friction;
    float side_friction;
    float forward_friction;
    float rolling_friction;
    float handbrake_side_friction_addition;
    float rotation_factor;
};

struct VehicleGroundConfiguration {
    std::int32_t material_kind;
    // The original dictionary reads these three named entries in this order.
    VehicleGroundSurfaceConfiguration firm;
    VehicleGroundSurfaceConfiguration medium;
    VehicleGroundSurfaceConfiguration soft;
};

// #1513 Chassis::Settings (0x00f822a0), followed by TrackChassis::Settings
// (0x00f88700). Slope tuples retain their source values until the matching
// native curve builders consume them; they are not guessed angle cutoffs.
struct VehicleChassisConfiguration {
    float chassis_mass;
    float chassis_mass_fraction;
    float clearance;
    float track_gauge_factor;
    bool wheel_detach_on_roll;
    float track_to_be_locked_delay;
    float wheel_radius;
    float wheel_inertia_factor;
    float wheel_forward_inertia_factor;
    float stiffness_zero;
    float stiffness_one;
    float damping;
    float wheel_restitution;
    float wheel_penetration;
    bool wheel_use_pseudo_contacts;
    float brake;
    float rotation_brake;
    float wheel_sinkage_resistance_factor;
    float wheel_dry_friction_factor;
    float side_friction_constant_ratio;
    std::array<float, 5> road_wheel_positions;
    std::array<float, 5> stiffness_factors;
    std::array<float, 3> slope_resistance_terrain;
    std::array<float, 3> slope_resistance_static_object;
    std::array<float, 3> slope_resistance_dynamic_object;
    std::array<float, 4> slope_grip_forward_terrain;
    std::array<float, 4> slope_grip_side_terrain;
    std::array<float, 4> slope_grip_forward_static_object;
    std::array<float, 4> slope_grip_side_static_object;
    std::array<float, 4> slope_grip_forward_dynamic_object;
    std::array<float, 4> slope_grip_side_dynamic_object;
    bool flat_side_friction;
    std::vector<VehicleGroundConfiguration> grounds;
    float pitch_correction_center_z;
    float pitch_correction_speed;
    float pitch_min;
    float pitch_max;
    float pitch_correction_stiffness;
    float pitch_correction_damping;
    float pitch_correction_scale;
    float yaw_gimlet_force;
    float yaw_stiffness;
    float yaw_damping;
    float yaw_precise_restitution;
    float yaw_damping_distance;
    float yaw_precise_distance;
};

// #1513 Engine::Settings (0x00f7ecd0). RPM inputs are retained here in the
// original dictionary's units; the native settings builder converts them.
struct VehicleEngineConfiguration {
    std::array<float, 2> losses;
    float simple_engine_power;
    float engine_inertia;
    float simple_engine_join_ratio;
    float idle_choker;
    float rotation_choker;
    float rotation_by_lock_choker;
    float simple_min_rpm;
    float idle_rpm;
    float start_rpm;
    float maximum_forward_angular_velocity;
    float maximum_backward_angular_velocity;
    float rotation_angular_velocity;
};

struct VehiclePhysicsModeConfiguration {
    // The dictionary key is retained: global helpers explicitly read normal.
    std::string name;
    // Native mode_index=-1 means the current mode when replacing a setting.
    std::int32_t mode_index;
    VehicleBodyConfiguration body;
    VehicleShapeConfiguration shape;
    bool siege_mode_available;
    VehicleChassisConfiguration chassis;
    VehicleEngineConfiguration engine;
};

// #1513 0x00f84e80 reads four equal-length sequences per gear direction.
// Each row keeps those source units; gearbox construction performs conversion.
struct VehicleFakeGearConfiguration {
    float switch_speed;
    float switch_hysteresis;
    float low_rpm;
    float high_rpm;
};

// #1513 0x00f97d30. These settings belong to modes.normal.gimlet rather than
// to the wrapper or a second presentation-side vehicle controller.
struct VehicleGimletConfiguration {
    float pushed_diagonal_weight;
    float pushed_rotation_weight;
    float pushed_handbrake_weight;
    float push_in_contact;
    float push_handbrake;
    float push_stop;
    float on_spot_fixed_period;
    float on_spot_grow_period;
    float on_spot_initial_push;
    float on_spot_final_push;
    float on_move_fixed_period;
    float on_move_grow_period;
    float on_move_initial_push;
    float on_move_final_push;
    float minimum_velocity_scale;
    float maximum_velocity_scale;
};

// Full source-side configure input. All numeric members above are values from
// the original Python producer, with no local fallback coefficients. Native
// configuration supports Tank vehicleType=0 and keeps settings by mode_index.
struct VehiclePhysicsConfiguration {
    std::int32_t vehicle_type;
    std::vector<VehiclePhysicsModeConfiguration> modes;
    float anchor_maximum_reaction_factor;
    float anchor_constant_fraction;
    float anchor_velocity_factor;
    float rail_factor_in_contact;
    VehicleGimletConfiguration gimlet;
    std::vector<VehicleFakeGearConfiguration> forward_gears;
    std::vector<VehicleFakeGearConfiguration> backward_gears;
};

// Original leftTrackScroll/rightTrackScroll outputs (TrackChassis +0x128/12c).
// These are not independently integrated by the presentation/filter owner.
struct VehicleTrackState {
    float left_scroll = 0;
    float right_scroll = 0;
};

struct VehiclePhysicsInputs {
    std::uint32_t movement_signals = 0;
    std::uint32_t cruise_signals = 0;
};

// The original addDamperSpring five-tuple, before native spring construction.
struct VehicleDamperSpring {
    Vector3 mount_point;
    float carrying_length;
    float indent;
    bool left;
    float hard_ratio;
};

// The WGVehiclePhysics state owner. DynamicsSimulator owns the group update;
// a filter consumes this object's published state and never runs a second
// controller. The complete configure/contact/solver path is being recovered;
// this interface deliberately exposes no successful placeholder for it.
class VehiclePhysics {
public:
    VehiclePhysics();

    const VehicleMatrix& entity_transform() const { return entity_transform_; }
    Vector3 linear_velocity() const { return body_.linear_velocity; }
    Vector3 angular_velocity() const { return body_.angular_velocity; }
    const VehicleTrackState& track_state() const { return tracks_; }
    const VehicleRigidBody& rigid_body() const { return body_; }

    // The original centerOfMass setter (0x00f542e0) only stores this offset;
    // the matrix setter and simulation publication consume it separately.
    Vector3 center_of_mass() const { return center_of_mass_; }
    void set_center_of_mass(Vector3 value) { center_of_mass_ = value; }

    const VehiclePhysicsInputs& inputs() const { return inputs_; }
    void set_inputs(VehiclePhysicsInputs value) { inputs_ = value; }

    // Original wrapper fields consumed by the world-step/collision owner.
    // These setters only store values; they do not fabricate a physical step.
    bool static_mode() const { return static_mode_; }
    void set_static_mode(bool value) { static_mode_ = value; }
    std::uint32_t visibility_mask() const { return visibility_mask_; }
    void set_visibility_mask(std::uint32_t value) { visibility_mask_ = value; }
    const std::array<float, 2>& arena_minimum() const { return arena_minimum_; }
    const std::array<float, 2>& arena_maximum() const { return arena_maximum_; }
    void set_arena_bounds(std::array<float, 2> minimum,
                          std::array<float, 2> maximum) {
        arena_minimum_ = minimum;
        arena_maximum_ = maximum;
    }

    void clear_damper_springs();
    void add_damper_spring(VehicleDamperSpring spring);
    const std::vector<VehicleDamperSpring>& damper_springs(bool left) const;
    bool set_damper_springs_lengths(const std::vector<float>& left,
                                   const std::vector<float>& right);

    void apply_impulse_to_com(Vector3 impulse);
    void apply_impulse(Vector3 point, Vector3 impulse);
    Vector3 point_velocity(Vector3 point) const;

private:
    friend class DynamicsSimulator;

    // The entity-transform portion of #1513 0x00f56240. World stepping calls
    // this after body integration; it is not an integration/contact solver.
    void publish_entity_transform();

    VehicleRigidBody body_;
    VehicleMatrix entity_transform_;
    Vector3 center_of_mass_{};
    VehicleTrackState tracks_;
    VehiclePhysicsInputs inputs_;
    bool static_mode_ = false;
    std::uint32_t visibility_mask_ = 0;
    // #1513 constructor 0x00f48c5a..0x00f48c78, before setArenaBounds.
    std::array<float, 2> arena_minimum_{{-1000.0f, -1000.0f}};
    std::array<float, 2> arena_maximum_{{1000.0f, 1000.0f}};
    std::array<std::vector<VehicleDamperSpring>, 2> damper_springs_;
};

} // namespace wot
