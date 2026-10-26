#include "wot/vehicle_drive.hpp"
#include "wot/vehicle_physics.hpp"

#include <cmath>
#include <iostream>
#include <limits>

namespace {

bool close(float actual, float expected, float tolerance = 1.0e-5f) {
    return std::fabs(actual - expected) <= tolerance;
}

wot::TrackContactMotion motion(float side_speed, float forward_speed) {
    return {{0, 0, 0}, {side_speed, 0, forward_speed}, {0, 0, 0},
            {0, 0, 0}, {0, 0, 1}, {1, 0, 0}};
}

bool slip_and_constant_friction() {
    const wot::TrackGroundFriction ground{2, 4, 0.5f, 0};
    auto result = wot::track_contact_friction(ground, motion(0, 0),
                                             false, false, 1, 1);
    if (result.forward != 4 || result.side != 2) return false;
    result = wot::track_contact_friction(ground, motion(0, -4),
                                         false, false, 1, 1);
    if (!close(result.forward, 4) || result.side != 0) return false;
    result = wot::track_contact_friction(ground, motion(-3, 0),
                                         false, false, 1, 1);
    if (result.forward != 0 || !close(result.side, 2)) return false;
    result = wot::track_contact_friction(ground, motion(3, 4),
                                         false, false, 1, 1);
    const float ellipse = (result.forward / 4) * (result.forward / 4) +
                          (result.side / 2) * (result.side / 2);
    if (!close(ellipse, 1) || !close(result.side / result.forward, 0.75f))
        return false;
    result = wot::track_contact_friction(ground, motion(4, 3),
                                         false, false, 1, 1);
    if (!close(result.forward / result.side, 0.75f)) return false;
    result = wot::track_contact_friction(ground, motion(0, 0.01f),
                                         false, false, 1, 1);
    if (result.forward != 4 || result.side != 2) return false;
    result = wot::track_contact_friction(ground, motion(0, 0.0101f),
                                         false, false, 1, 1);
    if (!close(result.forward, 4) || result.side != 0) return false;
    result = wot::track_contact_friction(ground, motion(100, 200),
                                         true, true, 0.5f, 0.25f);
    return result.forward == 2 && result.side == 0.625f;
}

bool current_contact_velocity_and_blend() {
    const wot::TrackGroundFriction ground{2, 4, 0.5f, 0.25f};
    auto m = motion(0, 0);
    m.angular_velocity = {0, 1, 0};
    m.center_of_mass = {10, 20, 30};
    m.contact_point = {11, 20, 30};
    auto result = wot::track_contact_friction(ground, m, false, false, 1, 1);
    if (!close(result.forward, 4) || !close(result.side, 0.5f)) return false;
    m.center_of_mass = m.contact_point;
    result = wot::track_contact_friction(ground, m, false, false, 1, 1);
    if (result.forward != 4 || result.side != 2) return false;
    result = wot::track_contact_friction(ground, motion(0, 4),
                                         false, true, 0.5f, 0.25f);
    return close(result.forward, 2) && close(result.side, 0.25f);
}

bool normal_reactions_and_track_state() {
    const wot::TrackReactionParameters p{
        10000, 10, 5, 3, 0.8f, 0.2f, 0.4f, 0.6f};
    wot::TrackContactReactions contact{3000, 17000, false, false, false, true};
    auto result = wot::track_contact_reaction_limits(p, contact);
    if (!close(result.forward, 4400) || !close(result.side, 4400)) return false;
    contact.couple_paired_reactions = true;
    result = wot::track_contact_reaction_limits(p, contact);
    if (!close(result.forward, 10000) || !close(result.side, 4400)) return false;
    contact.paired_contact_present = false;
    result = wot::track_contact_reaction_limits(p, contact);
    if (!close(result.forward, 4400)) return false;
    contact.normal_reaction = -40000;
    result = wot::track_contact_reaction_limits(p, contact);
    if (!close(result.forward, 26000) || !close(result.side, 26000)) return false;
    contact.normal_reaction = 3000;
    contact.any_track_broken = true;
    contact.this_track_broken = true;
    contact.paired_contact_present = true;
    result = wot::track_contact_reaction_limits(p, contact);
    if (!close(result.forward, 1760) || !close(result.side, 4400)) return false;
    contact.this_track_broken = false;
    result = wot::track_contact_reaction_limits(p, contact);
    return close(result.forward, 2640) && close(result.side, 4400);
}

bool native_min_and_constraint_bounds() {
    const wot::TrackReactionParameters p{10000, 10, 5, 3, 0.8f, 0.2f, 1, 1};
    wot::TrackContactReactions c{
        std::numeric_limits<float>::quiet_NaN(), 0, false, false, false, false};
    auto reaction = wot::track_contact_reaction_limits(p, c);
    if (!close(reaction.forward, 26000)) return false;
    c.couple_paired_reactions = true;
    c.paired_contact_present = true;
    reaction = wot::track_contact_reaction_limits(p, c);
    if (!std::isnan(reaction.forward) || !close(reaction.side, 26000)) return false;
    const auto bounds = wot::track_friction_constraint_limits(
        {2, 3}, {400, 500}, 0.25f, 0.5f);
    return bounds.forward_lower == -200 && bounds.forward_upper == 200 &&
           bounds.side_lower == -750 && bounds.side_upper == 750;
}

bool native_engine_torque_and_body() {
    wot::VehicleEngineConfiguration configuration{};
    configuration.losses = {1, 2};
    configuration.simple_engine_power = 1000;
    configuration.simple_min_rpm = 900;
    configuration.start_rpm = 600;
    const auto settings = wot::build_vehicle_engine_torque_settings(configuration);
    // The loss curve precedes the new RPM assignment in the native reader.
    if (settings.losses[0].angular_velocity != 52.3598785400390625f ||
        settings.losses[1].angular_velocity != 209.43951416015625f ||
        settings.losses[2].torque != 12) return false;
    auto torque = wot::prepare_vehicle_engine_torque(settings, 0, 1, false);
    if (!close(torque.gross, 1000.0f / 31.415928f + 1) ||
        torque.losses != 1) return false;
    torque = wot::prepare_vehicle_engine_torque(settings, 1000, 0.5f, false);
    if (torque.gross != 12.5f || torque.losses != 12) return false;
    torque = wot::prepare_vehicle_engine_torque(settings, 1000, 0.5f, true);
    if (!close(torque.gross, 0.5f * (1000 / (600 * 0.10471976f)) + 12))
        return false;
    torque = wot::prepare_vehicle_engine_torque(settings,
        std::numeric_limits<float>::quiet_NaN(), 1, false);
    if (torque.losses != 12 ||
        !close(torque.gross, 1000.0f / 31.415928f + 12)) return false;

    wot::VehicleRigidBody engine;
    engine.transform = wot::vehicle_identity_matrix();
    engine.loads.torque = {1, 2, 3};
    wot::accumulate_vehicle_engine_torque(engine, {10, 3}, true, 0.5f, 4, 1);
    if (engine.loads.torque.x != 7 || engine.loads.torque.y != 2 ||
        engine.loads.torque.z != 3 || engine.loads.force.x != 0) return false;
    wot::accumulate_vehicle_engine_torque(engine, {10, 3}, false, 0.5f, 4, 1);
    return engine.loads.torque.x == 7 && engine.loads.torque.y == 2 &&
           engine.loads.torque.z == 3;
}

} // namespace

int main() {
    if (!slip_and_constant_friction() || !current_contact_velocity_and_blend() ||
        !normal_reactions_and_track_state() || !native_min_and_constraint_bounds() ||
        !native_engine_torque_and_body()) {
        std::cerr << "vehicle drive contact regressions failed\n";
        return 1;
    }
    std::cout << "vehicle drive contact regressions passed\n";
    return 0;
}
