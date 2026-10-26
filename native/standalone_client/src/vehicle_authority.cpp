#include "wot/vehicle_authority.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <utility>

namespace wot::offline_vehicle_control {
namespace {

constexpr double pi = 3.14159265358979323846264338327950288;

double wrap_angle(double value) {
    while (value > pi) value -= 2 * pi;
    while (value < -pi) value += 2 * pi;
    return value;
}

bool active(const AuthorityActor& actor) {
    return actor.condition.alive && actor.condition.battle_live;
}

bool passable(AuthorityMotionStatus status) {
    return status == AuthorityMotionStatus::clear ||
           status == AuthorityMotionStatus::crushed;
}

void stop(AuthorityActor& actor) {
    actor.state.speed = 0;
    actor.state.turn_speed = 0;
    actor.state.drive_turn = 0;
    actor.state.movement_direction = 0;
    actor.state.rotation_direction = 0;
    actor.command.throttle = 0;
    actor.command.turn = 0;
}

double human_turn(const AuthorityActor& actor, double throttle) {
    const auto& command = actor.command;
    if (command.turn != 0 || throttle != 0 || command.handbrake ||
        !command.autorotation_enabled || !command.turret_yaw_limits)
        return command.turn;
    double relative = std::fmod(command.aim_yaw - actor.state.pose.yaw + pi, 2 * pi);
    if (relative < 0) relative += 2 * pi;
    relative -= pi;
    const auto& limits = *command.turret_yaw_limits;
    // battle_runtime._local_autorotation_turn, installed gun limits only.
    if (relative < limits[0] - 1.0e-5) return -1;
    if (relative > limits[1] + 1.0e-5) return 1;
    return 0;
}

AuthorityMotionStatus query_motion(AuthorityActor& actor,
    AuthorityServices& services, double travel_yaw, double speed,
    double elapsed, double now, bool allow_crush, bool commit) {
    const auto result = services.motion(actor, {actor.state.pose,
        travel_yaw + (speed < 0 ? pi : 0),
        actor.state.pose.yaw, speed, elapsed, now, allow_crush, commit});
    switch (result) {
    case AuthorityMotionStatus::clear:
    case AuthorityMotionStatus::crushed:
    case AuthorityMotionStatus::soft:
    case AuthorityMotionStatus::cap_crushed:
    case AuthorityMotionStatus::hard: return result;
    }
    throw std::runtime_error("Authority motion resolver returned an invalid status");
}

void translate(AuthorityMotionState& state, double yaw, double speed, double elapsed) {
    state.pose.position[0] += std::sin(yaw) * speed * elapsed;
    state.pose.position[2] += std::cos(yaw) * speed * elapsed;
}

void hard_contact(AuthorityActor& actor, double elapsed, double now,
    AuthorityServices& services, bool bot) {
    std::optional<double> slide_yaw;
    for (double candidate : hard_contact_candidate_yaws(actor.state.pose.yaw)) {
        // Bots prove a glancing path without committing a destruction, then
        // commit that same candidate once. Humans use their existing single
        // current-pose motion query. Both use the shared hard-contact law.
        const auto first = query_motion(actor, services, candidate,
            actor.state.speed, elapsed, now, false, !bot);
        if (bot && first == AuthorityMotionStatus::cap_crushed)
            throw std::runtime_error("Bot passive motion resolver returned an invalid status");
        if (!passable(first)) continue;
        if (!bot) slide_yaw = candidate;
        else {
            const auto committed = query_motion(actor, services, candidate,
                actor.state.speed, elapsed, now, false, true);
            if (committed == AuthorityMotionStatus::cap_crushed)
                throw std::runtime_error("Bot passive motion resolver returned an invalid status");
            if (passable(committed)) slide_yaw = candidate;
        }
        break;
    }
    const auto resolved = hard_contact_step(actor.state.speed, elapsed,
        actor.state.grind_ticks > 0, slide_yaw);
    actor.state.speed = resolved.speed;
    actor.state.pose.position[0] += resolved.dx;
    actor.state.pose.position[2] += resolved.dz;
    actor.state.grind_ticks = 4;
}

void human_drivetrain(AuthorityActor& actor, double elapsed, double now,
    AuthorityServices& services, const Tuning& tuning) {
    auto& state = actor.state;
    const auto& condition = actor.condition;
    double slope_pitch = 0;
    if (!state.airborne) {
        const auto supported = actor.suspension
            ? suspension_drive_pitch(state.ground_plane, state.pose.yaw)
            : std::nullopt;
        const double raw = supported ? *supported : services.human_drive_pitch(actor);
        if (!actor.drive_pitch_initialized) {
            actor.drive_pitch_history.fill(raw);
            actor.drive_pitch_initialized = true;
        }
        std::move(actor.drive_pitch_history.begin() + 1,
            actor.drive_pitch_history.end(), actor.drive_pitch_history.begin());
        actor.drive_pitch_history.back() = raw;
        auto ordered = actor.drive_pitch_history;
        std::sort(ordered.begin(), ordered.end());
        actor.smoothed_drive_pitch += (ordered[2] - actor.smoothed_drive_pitch) * 0.5;
        slope_pitch = actor.smoothed_drive_pitch;
    }
    double throttle = condition.siege_switching || condition.overturned
        ? 0 : actor.command.throttle;
    double turn = condition.siege_switching || condition.overturned
        ? 0 : human_turn(actor, throttle);
    if (condition.track_destroyed || condition.engine_destroyed) throttle = 0;
    else if (throttle != 0) throttle *= condition.mobility_factor;
    const bool handbrake = (actor.command.handbrake && !condition.overturned) ||
        condition.track_destroyed || condition.siege_switching;
    const double before_speed = state.speed;
    if (condition.siege_switching) {
        state.speed = 0;
        state.turn_speed = 0;
    } else {
        auto drive = actor.parameters;
        drive.power_w *= condition.engine_power_factor;
        state.speed = longitudinal_step(drive, state.speed, throttle, turn != 0,
            slope_pitch, elapsed, state.airborne, 0, handbrake, tuning);
    }
    if (std::abs(state.speed) > 0.0001 && elapsed > 0) {
        const auto status = query_motion(actor, services, state.pose.yaw,
            state.speed, elapsed, now, throttle * state.speed > 0 && !handbrake, true);
        if (passable(status)) {
            translate(state, state.pose.yaw, state.speed, elapsed);
            state.grind_ticks = std::max(0, state.grind_ticks - 1);
        } else if (!state.airborne) {
            if (status == AuthorityMotionStatus::cap_crushed) {
                state.speed = before_speed;
                state.grind_ticks = 1;
            } else if (status == AuthorityMotionStatus::soft) {
                state.grind_ticks = 1;
            } else hard_contact(actor, elapsed, now, services, false);
        }
    }
    if (condition.siege_switching || condition.overturned ||
        condition.track_destroyed || condition.engine_destroyed) {
        turn = 0;
        state.turn_speed = 0;
    }
    state.drive_turn = turn;
    if (!condition.siege_switching) {
        state.turn_speed = traverse_step(actor.parameters, state.turn_speed,
            turn, state.speed, elapsed, 0, throttle, tuning);
        state.turn_speed *= condition.traverse_factor;
    }
    const double candidate = wrap_angle(state.pose.yaw + state.turn_speed * elapsed);
    if (services.rotation(actor, state.pose, candidate) == AuthorityRotationStatus::clear)
        state.pose.yaw = candidate;
    else state.turn_speed = 0;
}

void bot_drivetrain(AuthorityActor& actor, double elapsed, double now,
    bool refresh_control, AuthorityServices& services, const Tuning& tuning) {
    auto& state = actor.state;
    const auto& condition = actor.condition;
    double throttle = std::max(-1.0, std::min(1.0, actor.command.throttle));
    double turn = std::max(-1.0, std::min(1.0, actor.command.turn));
    if (condition.siege_switching) {
        throttle = turn = 0;
        state.speed = state.turn_speed = 0;
        state.contact_push = {};
    }
    if (condition.overturned || condition.track_destroyed || condition.engine_destroyed)
        throttle = turn = 0;
    else if (std::abs(throttle) > 0.01) throttle *= condition.mobility_factor;
    const auto admission = services.bot_admission(actor, elapsed, now, refresh_control);
    if (admission.pose_frozen) throttle = turn = 0;
    if (!admission.path_clear) throttle = 0;
    state.movement_direction = throttle > 0.01 ? 1 : throttle < -0.01 ? -1 : 0;
    const int steer_direction = std::abs(turn) > 0.01 ? (turn > 0 ? 1 : -1) : 0;
    state.rotation_direction = steer_direction;
    state.drive_turn = turn;
    state.turn_speed = condition.siege_switching ? 0 : traverse_step(
        actor.parameters, state.turn_speed, turn, state.speed, elapsed, 0, throttle, tuning);
    const double candidate = wrap_angle(state.pose.yaw + state.turn_speed * elapsed);
    if (services.rotation(actor, state.pose, candidate) == AuthorityRotationStatus::clear)
        state.pose.yaw = candidate;
    else {
        state.turn_speed = 0;
        state.rotation_direction = 0;
    }
    const double previous_speed = state.speed;
    const auto supported = suspension_drive_pitch(state.ground_plane, state.pose.yaw);
    state.speed = condition.siege_switching ? 0 : longitudinal_step(
        actor.parameters, previous_speed, throttle, steer_direction != 0,
        supported ? *supported : admission.drive_pitch,
        elapsed, state.airborne, 0, false, tuning);
    bool path_clear = admission.path_clear;
    bool hard = !path_clear && admission.generic_collision &&
        !admission.exact_motion_owns_collision;
    if (!path_clear) {
        if (!hard) state.speed *= 0.2;
        state.destructible_contact_speed.reset();
    }
    if (path_clear && !admission.pose_frozen && std::abs(state.speed) > 0.0001) {
        const double contact_speed = state.destructible_contact_speed.value_or(state.speed);
        const auto status = query_motion(actor, services, state.pose.yaw,
            state.speed, elapsed, now,
            !state.airborne && state.movement_direction * state.speed > 0, true);
        if (!passable(status)) {
            path_clear = false;
            if (status == AuthorityMotionStatus::cap_crushed) {
                state.speed = previous_speed;
                state.destructible_contact_speed.reset();
                state.grind_ticks = 1;
            } else if (status == AuthorityMotionStatus::soft) {
                const double retained = std::min(std::abs(contact_speed), std::abs(state.speed));
                state.speed = state.speed < 0 ? -retained : retained;
                state.destructible_contact_speed = state.speed;
                state.grind_ticks = 1;
            } else {
                hard = true;
                state.destructible_contact_speed.reset();
                services.invalidate_motion(actor, state.pose.yaw +
                    (state.speed < 0 ? pi : 0));
            }
        } else state.destructible_contact_speed.reset();
    }
    bool deflected = false;
    if (hard && !state.airborne) {
        hard_contact(actor, elapsed, now, services, true);
        deflected = true;
    }
    if (condition.siege_speed_limit) {
        const double limit = *condition.siege_speed_limit;
        state.speed = std::max(-limit, std::min(limit, state.speed));
    }
    if (!deflected && path_clear && !admission.pose_frozen) {
        translate(state, state.pose.yaw, state.speed, elapsed);
        if (std::abs(state.speed) > 0.0001)
            state.grind_ticks = std::max(0, state.grind_ticks - 1);
    }
}

} // namespace

void advance_authority_drivetrain(AuthorityActor& actor, double elapsed,
    double now, bool refresh_control, AuthorityServices& services, const Tuning& tuning) {
    if (!active(actor)) { stop(actor); return; }
    if (actor.kind == AuthorityActorKind::human)
        human_drivetrain(actor, std::max(0.0, std::min(0.1, elapsed)), now, services, tuning);
    else bot_drivetrain(actor, elapsed, now, refresh_control, services, tuning);
}

void prepare_authority_suspension(AuthorityActor& actor,
    std::optional<double> initial_ground) {
    auto& state = actor.state;
    if (state.grounded_once || !initial_ground) return;
    if (!std::isfinite(*initial_ground))
        throw std::runtime_error("Initial suspension support is non-finite");
    state.pose.position[1] = *initial_ground;
    state.vertical_speed = 0;
    state.pose.pitch = state.pose.roll = 0;
    state.pitch_velocity = state.roll_velocity = 0;
    state.support_vertical_speed = 0;
    state.support_gradient.reset();
    if (actor.kind == AuthorityActorKind::bot) {
        state.terrain_pitch = 0;
        state.ground_plane.reset();
        state.spring_ground_memory.clear();
        state.pseudo_ground_memory.clear();
    }
}

AuthorityGroundResult settle_authority_suspension(AuthorityActor& actor,
    const AuthorityPose& tick_pose, const AuthorityGroundSnapshot& snapshot,
    double elapsed, const AuthoritySupportProbe& support_probe, const Tuning& tuning) {
    if (!actor.suspension || actor.state.suspension_disabled)
        throw std::runtime_error("Authority suspension is unavailable");
    if (!std::isfinite(snapshot.support_height_delta) ||
        !std::isfinite(snapshot.support_speed_delta))
        throw std::invalid_argument("Suspension support adjustment is non-finite");
    const auto& params = *actor.suspension;
    auto next = actor.state;
    const auto previous = next;
    const bool human = actor.kind == AuthorityActorKind::human;
    const auto& position = previous.pose.position;
    const auto motion = snapshot.motion_pose.value_or(tick_pose);
    const auto previous_plane = human || previous.grounded_once
        ? previous.ground_plane : std::nullopt;
    auto gradient = previous.support_gradient;
    if (previous_plane)
        gradient = std::array<double, 2>{previous_plane->gradient_x, previous_plane->gradient_z};
    const auto missing = [](const GroundHeights& values) {
        return std::all_of(values.begin(), values.end(),
            [](const auto& value) { return !value; });
    };
    const bool no_support = missing(snapshot.springs) && missing(snapshot.pseudo_contacts);
    next.support_rise_blocked = false;
    if (!previous.grounded_once && no_support) {
        next.vertical_speed = 0;
        next.airborne = false;
        next.support_vertical_speed = 0;
        next.support_gradient.reset();
        if (!human) next.ground_plane.reset();
        actor.state = std::move(next);
        AuthorityGroundResult result{AuthorityGroundOutcome::pending_spawn, {}, {}, 0};
        result.suspension.state = {actor.state.pose.position[1], 0,
            actor.state.pose.pitch, actor.state.pitch_velocity,
            actor.state.pose.roll, actor.state.roll_velocity};
        result.suspension.airborne = false;
        return result;
    }
    const double body_pitch = human ? previous.pose.pitch :
        previous.terrain_pitch.value_or(previous.pose.pitch);
    const auto current_plane = suspension_world_ground_plane(params, snapshot.springs,
        position, previous.pose.yaw, 0.35, body_pitch, previous.pose.roll);
    double height_delta = human ? snapshot.support_height_delta : 0;
    const double speed_delta = human ? snapshot.support_speed_delta : 0;
    if (!human && elapsed <= 0 && gradient)
        height_delta = (*gradient)[0] * (position[0] - motion.position[0]) +
                       (*gradient)[1] * (position[2] - motion.position[2]);
    double support_speed = previous.support_vertical_speed;
    if (elapsed > 0) {
        const double vx = (position[0] - motion.position[0]) / elapsed;
        const double vz = (position[2] - motion.position[2]) / elapsed;
        auto measured = suspension_support_vertical_velocity(current_plane, vx, vz);
        gradient.reset();
        if (measured && current_plane)
            gradient = std::array<double, 2>{current_plane->gradient_x, current_plane->gradient_z};
        if (!gradient && !previous.airborne) {
            gradient = previous.support_gradient;
            if (gradient) measured = (*gradient)[0] * vx + (*gradient)[1] * vz;
        }
        support_speed = measured.value_or(0);
        next.support_gradient = gradient;
    }
    support_speed += speed_delta;
    next.support_vertical_speed = support_speed;
    const double before_vv = previous.vertical_speed + speed_delta;
    auto solved = damper_suspension_step(params,
        {position[1] + height_delta, before_vv, body_pitch, previous.pitch_velocity,
            previous.pose.roll, previous.roll_velocity},
        snapshot.springs, elapsed, snapshot.pseudo_contacts, support_speed, tuning);
    const auto& proposal = solved.state;
    for (double value : {proposal.height, proposal.vertical_velocity, proposal.pitch,
            proposal.pitch_velocity, proposal.roll, proposal.roll_velocity})
        if (!std::isfinite(value))
            throw std::runtime_error("Authority suspension produced a non-finite pose");
    const bool invalid_pose = std::abs(proposal.height - position[1]) > 5 ||
        std::abs(proposal.pitch - body_pitch) > 1.2 ||
        std::abs(proposal.roll - previous.pose.roll) > 1.2;
    const auto continuous_rise = [&]() {
        if (!previous_plane || !current_plane ||
            !suspension_ground_planes_continuous(previous_plane, current_plane,
                motion.position, position, 0.35, 0.35)) return false;
        const auto start_y = suspension_plane_height(previous_plane,
            motion.position[0], motion.position[2]);
        const auto end_y = suspension_plane_height(current_plane, position[0], position[2]);
        if (!start_y || !end_y) return false;
        if (proposal.height - position[1] > 0.6 + std::max(0.0, *end_y - *start_y) + 0.02)
            return false;
        const double dx = position[0] - motion.position[0];
        const double dz = position[2] - motion.position[2];
        for (double fraction : suspension_path_probe_fractions(std::sqrt(dx * dx + dz * dz))) {
            const double x = motion.position[0] + dx * fraction;
            const double z = motion.position[2] + dz * fraction;
            const auto expected = human
                ? std::optional<double>(*start_y + (*end_y - *start_y) * fraction)
                : suspension_plane_height(previous_plane, x, z);
            if (!expected || !support_probe) return false;
            const auto actual = support_probe(x, z, *expected, 0.35);
            if (!actual || !std::isfinite(*actual) || std::abs(*actual - *expected) > 0.35)
                return false;
        }
        return true;
    };
    const bool raised = previous.grounded_once && solved.contact_count &&
        proposal.height - position[1] > 0.6 + 0.02 && !continuous_rise();
    bool hidden_raised = false;
    if (!human && previous.grounded_once && no_support && snapshot.hidden_center_support) {
        if (!std::isfinite(*snapshot.hidden_center_support))
            throw std::runtime_error("Bot centre support is non-finite");
        hidden_raised = *snapshot.hidden_center_support - position[1] > 0.6 + 0.02;
    }
    if (invalid_pose || raised || hidden_raised) {
        next = previous;
        next.pose.position = tick_pose.position;
        next.vertical_speed = next.pitch_velocity = next.roll_velocity = 0;
        next.support_vertical_speed = 0;
        next.support_gradient.reset();
        next.spring_ground_memory.clear();
        next.pseudo_ground_memory.clear();
        next.airborne = false;
        next.support_rise_blocked = true;
        if (!human) {
            next.speed = next.turn_speed = 0;
            next.movement_direction = next.rotation_direction = 0;
            next.contact_push = {};
        }
        actor.state = std::move(next);
        return {AuthorityGroundOutcome::rejected_support, std::move(solved), {}, 0};
    }
    next.pose.position[1] = proposal.height;
    next.vertical_speed = proposal.vertical_velocity;
    next.pose.pitch = human ? proposal.pitch : proposal.pitch + previous.hydraulic_pitch;
    if (!human) next.terrain_pitch = proposal.pitch;
    next.pitch_velocity = proposal.pitch_velocity;
    next.pose.roll = proposal.roll;
    next.roll_velocity = proposal.roll_velocity;
    next.airborne = solved.airborne;
    next.left_flying = solved.left_flying;
    next.right_flying = solved.right_flying;
    if (solved.contact_count) next.grounded_once = true;
    if (next.airborne) {
        next.support_vertical_speed = 0;
        next.support_gradient.reset();
        next.spring_ground_memory.clear();
        next.pseudo_ground_memory.clear();
    }
    if (human) {
        const bool spring_contact = std::any_of(snapshot.springs.begin(), snapshot.springs.end(),
            [](const auto& value) { return value.has_value(); });
        if (!spring_contact || next.airborne) {
            next.ground_plane.reset();
            next.surface_up_cosine.reset();
            next.downhill = {};
            next.slope_tangent = 0;
        } else {
            next.surface_up_cosine = std::max(-1.0, std::min(1.0,
                std::cos(proposal.pitch) * std::cos(proposal.roll)));
            next.ground_plane = current_plane;
            next.downhill = {};
            next.slope_tangent = 0;
            if (current_plane) {
                const double gx = current_plane->gradient_x, gz = current_plane->gradient_z;
                const double sine = std::sin(next.pose.yaw), cosine = std::cos(next.pose.yaw);
                const double forward = gx * sine + gz * cosine;
                const double right = gx * cosine - gz * sine;
                next.slope_tangent = std::sqrt(gx * gx + gz * gz);
                if (next.slope_tangent > 0.001)
                    next.downhill = {-gx / next.slope_tangent, 0, -gz / next.slope_tangent};
                next.ground_plane->pitch = -std::atan2(forward, 1.0);
                next.ground_plane->roll = std::atan2(right, std::sqrt(1 + forward * forward));
                next.ground_plane->slope_tangent = next.slope_tangent;
                next.ground_plane->up_cosine = 1 / std::sqrt(1 + next.slope_tangent * next.slope_tangent);
                next.ground_plane->downhill = next.downhill;
            }
        }
    } else {
        next.ground_plane = solved.contact_count && !next.airborne ? current_plane : std::nullopt;
        double sum = 0;
        std::size_t count = 0;
        if (solved.contact_count) {
            for (const auto* values : {&snapshot.springs, &snapshot.pseudo_contacts})
                for (const auto& value : *values) if (value) { sum += *value; ++count; }
            if (count) next.ground_height = sum / static_cast<double>(count);
        }
    }
    AuthorityGroundResult result{AuthorityGroundOutcome::accepted, solved, {}, 0};
    if (previous.airborne && !next.airborne && before_vv < 0) {
        const double impact_vertical = solved.impact_speed.value_or(before_vv);
        const std::array<double, 3> velocity{
            elapsed > 0 ? (position[0] - motion.position[0]) / elapsed : 0,
            impact_vertical,
            elapsed > 0 ? (position[2] - motion.position[2]) / elapsed : 0};
        const double impact = elapsed > 0 ? landing_impact_speed(velocity,
            current_plane ? std::optional<SuspensionVector>(current_plane->normal) : std::nullopt)
            : std::max(0.0, -impact_vertical);
        const double lateral = std::sqrt(next.air_lateral[0] * next.air_lateral[0] +
            next.air_lateral[1] * next.air_lateral[1]);
        if (lateral > 0.01) next.slide_speed = std::max(next.slide_speed, lateral);
        next.air_lateral = {};
        result.landing_impact = impact;
        result.proposed_fall_damage = fall_damage(std::max(1, actor.condition.maximum_health), impact, tuning);
        if (human && result.proposed_fall_damage > 0) {
            if (next.pending_landing_impacts.size() >= 32)
                throw std::runtime_error("Landing observation queue exceeded limit");
            next.pending_landing_impacts.push_back(std::min(200.0, std::abs(impact)));
        }
    } else if (!previous.airborne && next.airborne) {
        next.turn_speed = 0;
        if (human) next.drive_turn = 0;
        else next.rotation_direction = 0;
    }
    actor.state = std::move(next);
    return result;
}

void AuthorityWorld::add_actor(AuthorityActor value) {
    for (const auto& present : actors_)
        if (present.id == value.id) throw std::invalid_argument("Duplicate authority actor");
    actors_.push_back(std::move(value));
}

AuthorityActor& AuthorityWorld::actor(std::uint32_t id) {
    for (auto& value : actors_) if (value.id == id) return value;
    throw std::out_of_range("Authority actor is unavailable");
}

const AuthorityActor& AuthorityWorld::actor(std::uint32_t id) const {
    for (const auto& value : actors_) if (value.id == id) return value;
    throw std::out_of_range("Authority actor is unavailable");
}

std::vector<AuthorityActor*> AuthorityWorld::ordered_bots() {
    std::vector<AuthorityActor*> result;
    for (auto& value : actors_)
        if (value.kind == AuthorityActorKind::bot) result.push_back(&value);
    std::stable_sort(result.begin(), result.end(), [](const auto* a, const auto* b) {
        return a->slot < b->slot || (a->slot == b->slot && a->team < b->team);
    });
    return result;
}

void AuthorityWorld::advance_human(std::uint32_t id, AuthorityCommand command,
    AuthorityCondition condition, double elapsed, double now, AuthorityServices& services) {
    auto& value = actor(id);
    if (value.kind != AuthorityActorKind::human)
        throw std::invalid_argument("Human authority update requires a human actor");
    value.command = command;
    value.condition = condition;
    const double complete_elapsed = std::max(0.0, elapsed);
    double remaining = complete_elapsed;
    std::vector<AuthorityActor*> cohort{&value};
    bool zero_step = remaining <= 0;
    bool stopped = false;
    while (zero_step || remaining > 0.0000001) {
        zero_step = false;
        const double step = std::min(0.1, remaining);
        const auto before = value.state;
        advance_authority_drivetrain(value, step, now, true,
            services, tuning_);
        if (!active(value)) { stopped = true; break; }
        services.ground_step(value, before.pose, step);
        const auto settled = value.state;
        services.tank_contacts(cohort, step, now);
        services.finish_pose(value, before, settled, step);
        remaining = std::max(0.0, remaining - step);
    }
    if (stopped) {
        if (services.has_pending_barrier())
            services.publish(cohort, static_cast<std::int64_t>(std::llround(now * 1000000)));
        return;
    }
    value.input_publication_accumulator += complete_elapsed;
    if (services.has_pending_barrier()) {
        if (services.publish(cohort, static_cast<std::int64_t>(std::llround(now * 1000000))))
            value.input_publication_accumulator = std::fmod(
                value.input_publication_accumulator, 1.0 / 30.0);
    } else if (value.input_publication_accumulator >= 1.0 / 30.0) {
        value.input_publication_accumulator = std::fmod(
            value.input_publication_accumulator, 1.0 / 30.0);
        services.publish(cohort, static_cast<std::int64_t>(std::llround(now * 1000000)));
    }
}

void AuthorityWorld::advance_bots(double elapsed, double now, AuthorityServices& services) {
    last_bot_slices_ = 0;
    bot_accumulator_ += std::max(0.0, elapsed);
    if (bot_accumulator_ + 1.0e-9 < 0.10) return;
    double remaining = bot_accumulator_;
    bot_accumulator_ = 0;
    bool refresh_control = true;
    while (remaining > 1.0e-12) {
        double step = std::min(remaining, 0.20);
        if (const auto due = services.next_burst_time_left())
            step = std::min(step, *due <= 1.0e-9 ? 1.0e-9 : *due);
        remaining = std::max(0.0, remaining - step);
        const double step_now = now - remaining;
        const auto start_us = bot_sample_time_us_;
        const auto end_us = start_us + std::max<std::int64_t>(1,
            static_cast<std::int64_t>(std::llround(step * 1000000)));
        ++last_bot_slices_;
        std::vector<std::pair<AuthorityActor*, AuthorityMotionState>> before;
        std::vector<std::pair<AuthorityActor*, AuthorityPose>> locked;
        for (auto& value : actors_) {
            if (value.kind != AuthorityActorKind::bot) continue;
            const auto held_command = value.command;
            services.prepare_bot(value, step, step_now, refresh_control);
            if (!refresh_control) value.command = held_command;
            if (!active(value)) { stop(value); continue; }
            before.push_back({&value, value.state});
            if (value.condition.siege_switching)
                locked.push_back({&value, value.state.pose});
            advance_authority_drivetrain(value, step, step_now, refresh_control,
                services, tuning_);
            services.aim_and_fire(value, step, start_us, end_us);
        }
        auto ordered = ordered_bots();
        std::vector<std::pair<AuthorityActor*, AuthorityMotionState>> settled;
        for (auto* value : ordered) {
            const auto old = std::find_if(before.begin(), before.end(),
                [value](const auto& entry) { return entry.first == value; });
            if (old == before.end() || !active(*value)) continue;
            services.ground_step(*value, old->second.pose, step);
            settled.push_back({value, value->state});
        }
        services.tank_contacts(ordered, step, step_now);
        for (const auto& entry : locked) {
            auto& value = *entry.first;
            value.state.pose.position[0] = entry.second.position[0];
            value.state.pose.position[2] = entry.second.position[2];
            value.state.pose.yaw = entry.second.yaw;
            value.state.speed = value.state.turn_speed = 0;
            value.state.movement_direction = value.state.rotation_direction = 0;
            value.state.contact_push = {};
        }
        for (const auto& entry : settled) {
            const auto old = std::find_if(before.begin(), before.end(),
                [&entry](const auto& item) { return item.first == entry.first; });
            services.finish_pose(*entry.first, old->second, entry.second, step);
        }
        bot_sample_time_us_ = end_us;
        if (refresh_control || remaining <= 1.0e-12 || services.has_pending_barrier())
            services.publish(ordered, end_us);
        refresh_control = false;
    }
}

} // namespace wot::offline_vehicle_control
