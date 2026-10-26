#include "wot/vehicle_authority.hpp"

#include <cmath>
#include <iostream>
#include <string>
#include <vector>

using namespace wot::offline_vehicle_control;

namespace {

bool close(double a, double b) { return std::abs(a - b) < 1.0e-10; }

AuthorityActor make_actor(std::uint32_t id, AuthorityActorKind kind) {
    AuthorityActor actor;
    actor.id = id;
    actor.kind = kind;
    actor.slot = id;
    actor.team = 1;
    actor.parameters = {34000, 500000, 16, 5, 0.6, {1, 1.2, 1.8}, 1.1, 10, 1.4, 1};
    actor.state.speed = 5;
    actor.command.throttle = 1;
    return actor;
}

// This fixture observes orchestration and supplies explicit query outcomes;
// it proves no native terrain, destruction or rendering behavior.
class Services final : public AuthorityServices {
public:
    AuthorityMotionStatus primary = AuthorityMotionStatus::clear;
    bool clear_glance = false;
    bool barrier = false;
    bool fail_publish = false;
    bool contacts_saw_settled = true;
    bool aim_saw_unsettled = true;
    std::optional<double> burst;
    std::vector<std::string> phases;
    std::vector<AuthorityMotionQuery> queries;
    std::vector<double> elapsed_slices;
    std::vector<bool> refreshes;
    std::vector<std::int64_t> publications;
    std::vector<std::vector<AuthorityMotionState>> frozen_publications;
    std::size_t invalidations = 0;

    double human_drive_pitch(const AuthorityActor&) override { return 0; }
    AuthorityMotionStatus motion(const AuthorityActor&, const AuthorityMotionQuery& q) override {
        queries.push_back(q);
        const double signed_heading = q.travel_yaw -
            (q.speed < 0 ? 3.14159265358979323846 : 0);
        if (std::abs(signed_heading - q.hull_yaw) < 1.0e-9) return primary;
        return clear_glance ? AuthorityMotionStatus::clear : AuthorityMotionStatus::hard;
    }
    AuthorityRotationStatus rotation(const AuthorityActor&, const AuthorityPose&, double) override {
        return AuthorityRotationStatus::clear;
    }
    AuthorityBotAdmission bot_admission(const AuthorityActor&, double, double, bool) override {
        return {true, false, false, true, 0};
    }
    void prepare_bot(AuthorityActor& a, double dt, double, bool refresh) override {
        elapsed_slices.push_back(dt);
        refreshes.push_back(refresh);
        phases.push_back("prepare" + std::to_string(a.id));
    }
    void aim_and_fire(AuthorityActor& a, double dt, std::int64_t, std::int64_t) override {
        phases.push_back("aim" + std::to_string(a.id));
        aim_saw_unsettled = aim_saw_unsettled && a.state.pose.position[1] == 0;
        if (burst) {
            *burst -= dt;
            if (*burst <= 1.0e-9) { burst.reset(); barrier = true; }
        }
    }
    void ground_step(AuthorityActor& a, const AuthorityPose&, double) override {
        phases.push_back("ground" + std::to_string(a.id));
        a.state.pose.position[1] = 10;
    }
    void tank_contacts(std::vector<AuthorityActor*>& actors, double, double) override {
        phases.push_back("contacts");
        for (const auto* a : actors)
            contacts_saw_settled = contacts_saw_settled && a->state.pose.position[1] == 10;
    }
    void finish_pose(AuthorityActor& a, const AuthorityMotionState&, const AuthorityMotionState&, double) override {
        phases.push_back("finish" + std::to_string(a.id));
    }
    void invalidate_motion(AuthorityActor&, double) override { ++invalidations; }
    std::optional<double> next_burst_time_left() const override { return burst; }
    bool has_pending_barrier() const override { return barrier; }
    bool publish(const std::vector<AuthorityActor*>& actors, std::int64_t time) override {
        publications.push_back(time);
        std::vector<AuthorityMotionState> copy;
        for (const auto* value : actors) copy.push_back(value->state);
        frozen_publications.push_back(std::move(copy));
        phases.push_back("publish");
        if (!fail_publish) barrier = false;
        return !fail_publish;
    }
};

bool distinct_actor_order() {
    auto human = make_actor(1, AuthorityActorKind::human);
    auto bot = make_actor(2, AuthorityActorKind::bot);
    human.command.turn = bot.command.turn = 1;
    Services human_services, bot_services;
    advance_authority_drivetrain(human, 0.1, 0.1, true, human_services);
    advance_authority_drivetrain(bot, 0.1, 0.1, true, bot_services);
    return human.state.pose.position[0] == 0 && bot.state.pose.position[0] > 0 &&
        human.state.pose.yaw > 0 && bot.state.pose.yaw > 0 &&
        human_services.queries.front().hull_yaw == 0 &&
        bot_services.queries.front().hull_yaw > 0;
}

bool five_outcomes_and_passive_commit() {
    for (const auto kind : {AuthorityActorKind::human, AuthorityActorKind::bot}) {
        for (const auto status : {AuthorityMotionStatus::clear, AuthorityMotionStatus::crushed,
                AuthorityMotionStatus::soft, AuthorityMotionStatus::cap_crushed,
                AuthorityMotionStatus::hard}) {
            auto a = make_actor(1, kind);
            Services s;
            s.primary = status;
            advance_authority_drivetrain(a, 0.1, 0.1, true, s);
            if (status == AuthorityMotionStatus::clear || status == AuthorityMotionStatus::crushed) {
                if (!(a.state.pose.position[2] > 0 && a.state.speed > 5)) return false;
            } else if (status == AuthorityMotionStatus::cap_crushed) {
                if (a.state.speed != 5 || a.state.pose.position[2] != 0) return false;
            } else if (status == AuthorityMotionStatus::soft) {
                if (!(a.state.speed > 5) || a.state.pose.position[2] != 0) return false;
            } else if (a.state.pose.position[2] != 0 || !(a.state.speed < 5)) return false;
        }
    }
    auto bot = make_actor(1, AuthorityActorKind::bot);
    Services s;
    s.primary = AuthorityMotionStatus::hard;
    s.clear_glance = true;
    advance_authority_drivetrain(bot, 0.1, 0.1, true, s);
    return s.queries.size() == 3 && !s.queries[1].commit && s.queries[2].commit &&
        !s.queries[1].allow_crush_drive && s.queries[1].hull_yaw == 0 &&
        s.queries[1].travel_yaw == 0.55 && bot.state.pose.position[0] > 0 &&
        s.invalidations == 1;
}

bool tracked_brake_and_reverse_heading() {
    auto dead = make_actor(1, AuthorityActorKind::human);
    auto tracked = dead;
    dead.condition.engine_destroyed = true;
    tracked.condition.track_destroyed = true;
    Services sd, st;
    advance_authority_drivetrain(dead, 0.1, 0.1, true, sd);
    advance_authority_drivetrain(tracked, 0.1, 0.1, true, st);
    if (!(dead.state.speed > tracked.state.speed)) return false;
    auto reverse = make_actor(2, AuthorityActorKind::human);
    reverse.state.speed = -3;
    reverse.command.throttle = -1;
    Services sr;
    advance_authority_drivetrain(reverse, 0.1, 0.1, true, sr);
    return sr.queries.front().travel_yaw > 3.14 && reverse.state.pose.position[2] < 0;
}

bool elapsed_and_roster_barriers() {
    AuthorityWorld human_world;
    auto human = make_actor(1, AuthorityActorKind::human);
    human_world.add_actor(human);
    Services hs;
    human_world.advance_human(1, human.command, human.condition, 0.55, 0.55, hs);
    std::size_t ground_count = 0;
    for (const auto& phase : hs.phases) if (phase == "ground1") ++ground_count;
    if (ground_count != 6 || hs.publications.size() != 1) return false;

    AuthorityWorld world;
    auto first = make_actor(1, AuthorityActorKind::bot);
    auto second = make_actor(2, AuthorityActorKind::bot);
    first.slot = 2;
    second.slot = 1;
    world.add_actor(first);
    world.add_actor(second);
    Services services;
    world.advance_bots(0.05, 0.05, services);
    if (world.last_bot_slices() != 0 || !close(world.bot_time_debt(), 0.05)) return false;
    world.advance_bots(0.50, 0.55, services);
    if (world.last_bot_slices() != 3 || world.bot_sample_time_us() != 550000 ||
        services.publications.size() != 2 || !services.contacts_saw_settled) return false;
    const std::vector<std::string> prefix{
        "prepare1", "aim1", "prepare2", "aim2", "ground2", "ground1",
        "contacts", "finish2", "finish1", "publish"};
    if (!std::equal(prefix.begin(), prefix.end(), services.phases.begin())) return false;
    return services.refreshes == std::vector<bool>{true, true, false, false, false, false};
}

bool burst_and_failed_publication_freeze() {
    AuthorityWorld world;
    world.add_actor(make_actor(1, AuthorityActorKind::bot));
    Services s;
    s.burst = 0.25;
    world.advance_bots(0.45, 0.45, s);
    if (world.last_bot_slices() != 3 || s.publications !=
        std::vector<std::int64_t>{200000, 250000, 450000}) return false;
    const auto frozen = s.frozen_publications[1][0].pose;
    if (!(s.frozen_publications.back()[0].pose.position[2] > frozen.position[2])) return false;
    // The queue fixture copies each publication before acknowledging it;
    // later simulation cannot rebind that contact/launch checkpoint.
    return frozen.position[2] == s.frozen_publications[1][0].pose.position[2];
}

SuspensionParameters suspension_parameters() {
    SuspensionParameters p;
    p.mass = 10000;
    p.pitch_inertia = 30000;
    p.roll_inertia = 10000;
    p.fixed_step = 1.0 / 60.0;
    p.constraint_iterations = 10;
    for (int pair = 0; pair != 5; ++pair) {
        for (int side = 0; side != 2; ++side) {
            SuspensionSpring spring;
            spring.x = side == 0 ? -1 : 1;
            spring.z = pair - 2;
            spring.side = side == 0 ? ContactSide::left : ContactSide::right;
            spring.static_compression = 0.2;
            spring.max_compression = 0.4;
            spring.stiffness = p.mass * 9.81 * 1.25 / (10 * 0.2);
            spring.damping = 2000;
            spring.max_force = 100000;
            p.springs.push_back(spring);
        }
    }
    return p;
}

bool suspension_commit_cliff_and_landing() {
    auto a = make_actor(1, AuthorityActorKind::human);
    a.condition.maximum_health = 400;
    a.suspension = suspension_parameters();
    a.state.pose.position = {0, 100, 0};
    AuthorityGroundSnapshot ground;
    ground.springs.resize(10);
    const auto pending = settle_authority_suspension(a, a.state.pose, ground, 0.1, {});
    if (pending.outcome != AuthorityGroundOutcome::pending_spawn ||
        a.state.pose.position[1] != 100 || a.state.airborne) return false;
    prepare_authority_suspension(a, 0);
    ground.springs.assign(10, 0);
    const auto level = settle_authority_suspension(a, a.state.pose, ground, 0.1, {});
    if (level.outcome != AuthorityGroundOutcome::accepted || !a.state.grounded_once ||
        a.state.airborne || !a.state.ground_plane ||
        std::abs(a.state.pose.position[1]) > 1.0e-12) return false;
    ground.springs.assign(10, std::nullopt);
    a.state.turn_speed = 1;
    const auto cliff = settle_authority_suspension(a, a.state.pose, ground, 0.1, {});
    if (cliff.outcome != AuthorityGroundOutcome::accepted || !a.state.airborne ||
        a.state.vertical_speed >= 0 || a.state.turn_speed != 0) return false;
    a.state.pose.position[1] = 0.2;
    a.state.vertical_speed = -15;
    a.state.air_lateral = {3, 4};
    ground.springs.assign(10, 0);
    const auto landed = settle_authority_suspension(a, a.state.pose, ground, 0.1, {});
    return landed.landing_impact && landed.proposed_fall_damage > 0 &&
        !a.state.airborne && a.state.pending_landing_impacts.size() == 1 &&
        a.state.air_lateral == std::array<double, 2>{0, 0} && a.state.slide_speed == 5;
}

bool raised_layer_and_actual_path_proof() {
    for (const auto kind : {AuthorityActorKind::human, AuthorityActorKind::bot}) {
        auto a = make_actor(1, kind);
        a.suspension = suspension_parameters();
        a.state.pose.position = {0, 0, 0};
        a.state.grounded_once = true;
        AuthorityGroundSnapshot snapshot;
        snapshot.springs.assign(10, 0);
        settle_authority_suspension(a, a.state.pose, snapshot, 0, {});
        const auto before = a.state.pose;
        snapshot.springs.assign(10, 3);
        const auto rejected = settle_authority_suspension(a, before, snapshot, 0.1, {});
        if (rejected.outcome != AuthorityGroundOutcome::rejected_support ||
            !a.state.support_rise_blocked || a.state.pose.position[1] != before.position[1] ||
            a.state.vertical_speed != 0 ||
            (kind == AuthorityActorKind::bot && a.state.speed != 0)) return false;
    }
    auto ramp = make_actor(1, AuthorityActorKind::human);
    ramp.suspension = suspension_parameters();
    ramp.state.pose.position = {0, 0, 5};
    ramp.state.grounded_once = true;
    GroundPlane old;
    old.center_x = old.center_z = 0;
    old.gradient_z = 0.4;
    old.normal = *ground_normal(0, 0.4);
    ramp.state.ground_plane = old;
    AuthorityGroundSnapshot snapshot;
    snapshot.motion_pose = AuthorityPose{};
    for (const auto& spring : ramp.suspension->springs)
        snapshot.springs.push_back(0.4 * (5 + spring.z));
    auto rejected = ramp;
    const auto missing = settle_authority_suspension(rejected, AuthorityPose{}, snapshot, 0.1, {});
    std::size_t probes = 0;
    const auto accepted = settle_authority_suspension(ramp, AuthorityPose{}, snapshot, 0.1,
        [&probes](double, double z, double, double) -> std::optional<double> {
            ++probes;
            return 0.4 * z;
        });
    return probes > 0 && missing.outcome == AuthorityGroundOutcome::rejected_support &&
        accepted.outcome == AuthorityGroundOutcome::accepted && ramp.state.pose.position[1] > 0.62;
}

} // namespace

int main() {
    const std::pair<const char*, bool (*)()> cases[] = {
        {"actor order", distinct_actor_order},
        {"five outcomes", five_outcomes_and_passive_commit},
        {"critical brake", tracked_brake_and_reverse_heading},
        {"elapsed and roster", elapsed_and_roster_barriers},
        {"burst barriers", burst_and_failed_publication_freeze},
        {"suspension lifecycle", suspension_commit_cliff_and_landing},
        {"raised layers", raised_layer_and_actual_path_proof}};
    for (const auto& test : cases) {
        if (!test.second()) {
            std::cerr << "vehicle authority regression failed: " << test.first << '\n';
            return 1;
        }
    }
    std::cout << "vehicle authority regressions passed\n";
}
