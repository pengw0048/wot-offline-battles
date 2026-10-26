#pragma once

#include "wot/vehicle_control.hpp"
#include "wot/vehicle_suspension.hpp"

#include <array>
#include <cstdint>
#include <functional>
#include <optional>
#include <vector>

namespace wot::offline_vehicle_control {

enum class AuthorityActorKind { human, bot };
enum class AuthorityMotionStatus { clear, crushed, soft, cap_crushed, hard };
enum class AuthorityRotationStatus { clear, arena, obstacle };

struct AuthorityPose {
    std::array<double, 3> position{};
    double yaw = 0;
    double pitch = 0;
    double roll = 0;
};

struct AuthorityMotionState {
    AuthorityPose pose;
    double speed = 0;
    double turn_speed = 0;
    double drive_turn = 0;
    double vertical_speed = 0;
    double pitch_velocity = 0;
    double roll_velocity = 0;
    double slide_speed = 0;
    std::array<double, 2> air_lateral{};
    std::array<double, 2> contact_push{};
    std::optional<double> destructible_contact_speed;
    std::optional<GroundPlane> ground_plane;
    std::optional<double> terrain_pitch;
    double hydraulic_pitch = 0;
    double support_vertical_speed = 0;
    std::optional<std::array<double, 2>> support_gradient;
    std::vector<std::optional<RetainedGroundContact>> spring_ground_memory;
    std::vector<std::optional<RetainedGroundContact>> pseudo_ground_memory;
    std::array<double, 3> downhill{};
    double slope_tangent = 0;
    std::optional<double> surface_up_cosine;
    std::optional<double> ground_height;
    bool left_flying = false;
    bool right_flying = false;
    bool support_rise_blocked = false;
    bool suspension_disabled = false;
    std::vector<double> pending_landing_impacts;
    int grind_ticks = 0;
    int movement_direction = 0;
    int rotation_direction = 0;
    bool airborne = false;
    bool grounded_once = false;
};

struct AuthorityCommand {
    double throttle = 0;
    double turn = 0;
    bool handbrake = false;
    bool autorotation_enabled = false;
    double aim_yaw = 0;
    std::optional<std::array<double, 2>> turret_yaw_limits;
};

struct AuthorityCondition {
    bool alive = true;
    bool battle_live = true;
    bool siege_switching = false;
    bool overturned = false;
    bool track_destroyed = false;
    bool engine_destroyed = false;
    double mobility_factor = 1;
    double traverse_factor = 1;
    double engine_power_factor = 1;
    std::optional<double> siege_speed_limit;
    int maximum_health = 1;
};

struct AuthorityBotAdmission {
    bool path_clear;
    bool pose_frozen;
    bool generic_collision;
    bool exact_motion_owns_collision;
    double drive_pitch;
};

struct AuthorityMotionQuery {
    AuthorityPose start;
    double travel_yaw; // Actual world travel, including reverse direction.
    double hull_yaw;
    double speed;
    double elapsed;
    double now;
    bool allow_crush_drive;
    bool commit;
};

struct AuthorityActor {
    std::uint32_t id;
    AuthorityActorKind kind;
    std::uint32_t slot;
    std::uint32_t team;
    Parameters parameters;
    AuthorityMotionState state;
    AuthorityCommand command;
    AuthorityCondition condition;
    std::optional<SuspensionParameters> suspension;
    std::array<double, 5> drive_pitch_history{};
    bool drive_pitch_initialized = false;
    double smoothed_drive_pitch = 0;
    double input_publication_accumulator = 0;
};

// Actual scene queries and the existing mod's other owners meet here. No
// implementation is supplied that pretends a missing query/service succeeded.
// Ground/contact/aim callbacks operate on this same actor state, not another
// hidden physics owner. Their source order is enforced by AuthorityWorld.
class AuthorityServices {
public:
    virtual ~AuthorityServices() = default;
    virtual double human_drive_pitch(const AuthorityActor& actor) = 0;
    virtual AuthorityMotionStatus motion(const AuthorityActor& actor,
                                          const AuthorityMotionQuery& query) = 0;
    virtual AuthorityRotationStatus rotation(const AuthorityActor& actor,
        const AuthorityPose& start, double target_yaw) = 0;
    virtual AuthorityBotAdmission bot_admission(const AuthorityActor& actor,
        double elapsed, double now, bool refresh_control) = 0;
    virtual void prepare_bot(AuthorityActor& actor, double elapsed, double now,
                              bool refresh_control) = 0;
    virtual void aim_and_fire(AuthorityActor& actor, double elapsed,
                               std::int64_t start_us, std::int64_t end_us) = 0;
    virtual void ground_step(AuthorityActor& actor, const AuthorityPose& before,
                               double elapsed) = 0;
    virtual void tank_contacts(std::vector<AuthorityActor*>& actors,
                                double elapsed, double now) = 0;
    virtual void finish_pose(AuthorityActor& actor, const AuthorityMotionState& before,
                              const AuthorityMotionState& settled, double elapsed) = 0;
    virtual void invalidate_motion(AuthorityActor& actor, double travel_yaw) = 0;
    virtual std::optional<double> next_burst_time_left() const = 0;
    // The queue owner retains every unacknowledged launch/ram/destructible
    // event. A pending one-shot forces this exact slice's pose publication.
    virtual bool has_pending_barrier() const = 0;
    // Freeze this call's poses and pending events before attempting transport.
    // A false transport result must retain that immutable publication for
    // retry; a later call must never rebind an admitted event to a newer pose.
    virtual bool publish(const std::vector<AuthorityActor*>& actors,
                          std::int64_t sample_time_us) = 0;
};

// Full horizontal actor evolution from the current mod, preserving distinct
// human/Bot order and all five motion statuses. Vertical/contact services are
// explicit integration boundaries, never a replacement ground model.
void advance_authority_drivetrain(AuthorityActor& actor, double elapsed,
    double now, bool refresh_control, AuthorityServices& services,
    const Tuning& tuning = {});

// Call once before querying this tick's spring/pseudo columns. Only a real
// initial centre hit may move an unarmed streamed spawn into its first layer.
void prepare_authority_suspension(AuthorityActor& actor,
    std::optional<double> initial_ground);

struct AuthorityGroundSnapshot {
    GroundHeights springs;
    GroundHeights pseudo_contacts;
    std::optional<AuthorityPose> motion_pose;
    std::optional<double> hidden_center_support;
    double support_height_delta = 0;
    double support_speed_delta = 0;
};

enum class AuthorityGroundOutcome { accepted, pending_spawn, rejected_support };
struct AuthorityGroundResult {
    AuthorityGroundOutcome outcome;
    SuspensionResult suspension;
    std::optional<double> landing_impact;
    int proposed_fall_damage = 0;
};

// A large support rise requires the original bounded centre-line geometry
// proof. The caller queries the actual scene at x/z around expected_height;
// returning None rejects that layer. No default successful probe is supplied.
using AuthoritySupportProbe = std::function<std::optional<double>(
    double x, double z, double expected_height, double tolerance)>;

// Source battle_runtime._update_suspension_vertical_motion and BotRuntime's
// same-named method, including their distinct rejected-layer responses.
// Frozen samples are consumed by the original copied damper law exactly once.
AuthorityGroundResult settle_authority_suspension(AuthorityActor& actor,
    const AuthorityPose& tick_pose, const AuthorityGroundSnapshot& snapshot,
    double elapsed, const AuthoritySupportProbe& support_probe,
    const Tuning& tuning = {});

class AuthorityWorld {
public:
    explicit AuthorityWorld(Tuning tuning = {}) : tuning_(tuning) {}
    void add_actor(AuthorityActor actor);
    AuthorityActor& actor(std::uint32_t id);
    const AuthorityActor& actor(std::uint32_t id) const;

    // Human input is frozen once for the complete render callback. No
    // elapsed suffix is dropped; source slices are at most 100 ms.
    void advance_human(std::uint32_t id, AuthorityCommand command,
        AuthorityCondition condition, double elapsed, double now,
        AuthorityServices& services);

    // Original worker cadence: bank until 100 ms, consume all banked time in
    // <=200 ms slices, shorten at armed burst edges, refresh control once.
    void advance_bots(double elapsed, double now, AuthorityServices& services);

    double bot_time_debt() const { return bot_accumulator_; }
    std::int64_t bot_sample_time_us() const { return bot_sample_time_us_; }
    std::size_t last_bot_slices() const { return last_bot_slices_; }

private:
    std::vector<AuthorityActor*> ordered_bots();
    std::vector<AuthorityActor> actors_;
    Tuning tuning_;
    double bot_accumulator_ = 0;
    std::int64_t bot_sample_time_us_ = 0;
    std::size_t last_bot_slices_ = 0;
};

} // namespace wot::offline_vehicle_control
