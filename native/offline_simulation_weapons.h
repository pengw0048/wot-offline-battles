#ifndef WOT_OFFLINE_SIMULATION_WEAPONS_H
#define WOT_OFFLINE_SIMULATION_WEAPONS_H

#include "offline_simulation_types.h"
#include <deque>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace offline_simulation { namespace weapons {

// All coefficients are installed by the existing exact-client readers.
// Zero initialization is storage initialization, never a physical fallback.
struct GunConfig {
    double fully_aimed_dispersion=0., after_shot=0., after_shot_in_burst=0.;
    double turret_dispersion_factor=0., aiming_time=0.;
    double movement_dispersion_factor=0., rotation_dispersion_factor=0.;
    double reload_full=0., reload_intra=0.;
    int clip_size=0, shell_count=0, burst_count=0;
    double burst_interval=0.;
    void validate() const;
    bool operator==(const GunConfig &) const;
};

struct GunState {
    GunConfig config;
    int clip=0, burst_remaining=0;
    double elapsed=0., reload_duration=0., reload_factor=1.;
    bool intra=false;
    double current_dispersion_factor=1., aiming_start_factor=1.;
    double aiming_elapsed=0., dispersion=0., motion_dispersion_squared=0.;
    explicit GunState(const GunConfig &);
    bool adopt(const GunConfig &);
    void restore(std::int64_t fire_seq, double dispersion_factor,
                 bool has_reload, double remaining, double duration,
                 double factor, bool has_clip, int restored_clip, int restored_size);
    void tick(double dt);
    void tick_dispersion(double dt, double move, double rotation, double turret,
                         double factor, double aim_factor);
    void bloom(double factor, bool final_round);
    double duration(double factor) const;
    double remaining(double factor) const;
    bool ready(double factor) const;
    bool rescale(double factor);
    int complete(double factor, int available=-1);
    void require_full();
    bool begin(int count, double factor);
    bool consume(bool final_round);
    bool cancel();
};

struct AmmoState {
    std::vector<int> categories, remaining;
    int loaded=0, next=0;
    bool reload_pending=false, plan_pending=true;
    void validate(int shell_count) const;
    int fallback() const;
    int available(int requested) const;
    bool stage(int requested, bool ready, bool full);
    bool can_fire(bool continuing=false) const;
    bool consume(bool continuing=false);
    int planned_rounds() const;
    bool requires_full() const;
};

struct BurstEdge {
    std::int64_t shot_seq=0, group_seq=0;
    int index=0, count=0, shell=0;
    bool final_round=false;
    double due_offset=0.;
};
struct BurstState {
    bool active=false;
    std::int64_t group_seq=0;
    int count=0, next_index=0, shell=0;
    double interval=0., time_left=0.;
    bool start(std::int64_t first, int count, double interval, int shell);
    std::vector<BurstEdge> advance(double dt);
    bool cancel(int launched=-1);
    void validate(std::int64_t fire_seq) const;
};

using Curve = std::vector<std::array<float, 2>>;
struct AimConfig {
    Curve minimum, maximum;
    double turret_speed=0., gun_speed=0.;
    bool fixed_pitch=false;
    std::array<double, 2> fixed_limits={};
    bool has_static_pitch=false;
    double static_pitch=0.;
    void validate() const;
};
struct AimState {
    double turret_yaw=0., gun_pitch=0., desired_gun_pitch=0.;
    bool aligned=false;
    bool limits_valid=false;
    std::array<double, 2> limits={};
};
struct AimInput {
    double raw_yaw=0., raw_pitch=0., dt=0.;
    bool target=false, limited_yaw=false, valid_pitch=true;
    double minimum_yaw=0., maximum_yaw=0.;
    double crew_factor=0., turret_factor=0., gun_factor=0.;
    // Effective hydraulic/siege limits can override the installed curves.
    bool override_pitch=false;
    std::array<double, 2> pitch_limits={};
};
std::array<double, 2> pitch_limits(double yaw, const AimConfig &);
double gun_pitch_step(double current, double desired, bool has_static,
                      double static_pitch, double speed, double elapsed,
                      double turret_time, std::array<double, 2> limits);
void advance_aim(AimState &, const AimConfig &, const AimInput &);
std::array<double, 2> scatter(const Point &base, double radius, double azimuth,
                            bool normalize_base=true);

struct BallisticSolution {
    bool valid=false;
    Point aim={};
    double pitch=0., time=0.;
};
std::vector<std::array<double, 2>> ballistic_solutions(
    const Point &, const Point &, double speed, double gravity,
    double minimum, double maximum);
BallisticSolution ballistic_intercept(
    const Point &, const Point &, const Point &, double speed, double gravity,
    double minimum, double maximum, bool high, double max_lead);
Point ballistic_position(const Point &, double yaw, double pitch,
                         double speed, double gravity, double time);

// Publication fields and the exact artillery trajectory key are frozen once.
// Proof scalars follow the reviewed _launch_key schema, including target kind.
struct LaunchRecord {
    ActorKey actor;
    BurstEdge edge;
    std::int64_t launch_time_us=0;
    Pose pose;
    double yaw=0., pitch=0.;
    bool has_origin=false, artillery=false, has_shells_before=false;
    Point origin={}, velocity={};
    double gravity=0., maximum_distance=0.;
    std::int64_t maximum_time_ms=0;
    int shells_before=0;
    std::string class_tag;
    std::vector<double> proof;
    bool operator==(const LaunchRecord &) const;
};

struct ActorState {
    GunState gun;
    AmmoState ammo;
    BurstState burst;
    AimConfig aim_config;
    AimState aim;
    bool has_aim=false;
    std::int64_t fire_seq=0;
    explicit ActorState(const GunConfig &config):gun(config) {}
};

class Store {
public:
    void install(ActorKey, const GunConfig &, const AmmoState &,
                 std::int64_t fire_seq, double dispersion_factor);
    bool remove(ActorKey);
    ActorState &at(ActorKey);
    const ActorState &at(ActorKey) const;
    void prepare(ActorKey, double dt, double reload_factor, int requested_shell);
    std::vector<BurstEdge> after_motion(ActorKey, const AimInput &,
        double movement, double rotation, double actual_turret_speed,
        double dispersion_factor, double aim_factor, bool apply_aim);
    bool begin(ActorKey, int count, double interval, double reload_factor);
    bool cancel(ActorKey);
    bool commit(ActorKey, const LaunchRecord &, double dispersion_factor);
    bool enqueue(const LaunchRecord &);
    bool ack(ActorKey, std::int64_t fire_seq);
    const std::deque<LaunchRecord> &pending() const { return launches_; }
private:
    std::map<ActorKey, ActorState> actors_;
    std::deque<LaunchRecord> launches_;
};

} } // namespace offline_simulation::weapons
#endif
