#ifndef WOT_NATIVE_VISIBILITY_CORE_H
#define WOT_NATIVE_VISIBILITY_CORE_H
#include <array>
#include <cstddef>
#include <cstdint>
#include <map>
#include <set>
#include <utility>
#include <vector>

// Pure owned numeric data. Neither this core nor its background caller may
// retain a borrowed Python object or invoke BigWorld/Python callbacks.
namespace native_visibility {
using Vec3 = std::array<double, 3>;
using Cell = std::pair<int, int>;
struct Checkpoints {
    bool available = false;  // Six exact descriptor-local points are present.
    std::array<Vec3, 6> points{};
    bool turret_mount_available = false;
    Vec3 turret_mount{};     // chassis.hullPosition + hull.turretPositions[0].
    bool static_turret_yaw_present = false;
    double static_turret_yaw = 0.0;
};
struct Pose {
    Vec3 position{};
    double yaw = 0.0, pitch = 0.0, roll = 0.0;
    // Python owner resolves turret_yaw, or aim_yaw-yaw, or zero before submit.
    double relative_turret_yaw = 0.0;
};
struct DetectionInputs {
    double distance = 0.0;   // Existing horizontal BotRuntime._distance.
    double view_range = 0.0;
    std::array<double, 2> base_camouflage{};  // moving, still.
    bool moving = false, fired_recently = false;
    double additive = 0.0, multiplier = 1.0, shot_factor = 1.0;
};
struct PairInput {
    Checkpoints observer_checkpoints, target_checkpoints;
    Pose observer, target;
    std::uint64_t observer_phase = 0;  // floor(max(0,server_time_ms)/2000).
    DetectionInputs detection;
};
struct Volume {
    bool dynamic = false;
    // Static source row: x,y_min,z,y_max, inv_u_x,inv_u_z,inv_v_x,inv_v_z,
    // strength, radius. Dynamic rows use center/half_axes/strength/radius.
    std::array<double, 10> static_row{};
    Vec3 center{};
    std::array<Vec3, 3> half_axes{};
    double strength = 0.0, radius = 0.0;
};
struct FoliageSnapshot {
    bool enabled = true;
    double cell_size = 32.0;
    std::vector<Volume> instances;
    std::map<Cell, std::vector<std::size_t>> cells;
    std::set<std::size_t> inactive_instances;
    // Frozen for the job. Update fallen rows/memberships/inactive standing
    // IDs together before publishing the next snapshot to the existing owner.
};
struct Ray {
    Vec3 start{}, end{};
    double foliage_bonus = 0.0;
};
struct PreparedVisibility {
    std::vector<Ray> rays;  // Ordered target checkpoints; normal count 6.
    DetectionInputs detection;
};
struct VisibilityResult {
    bool line_of_sight = false, detected = false;
    double foliage_bonus = 0.0, camouflage = 0.0, detection_distance = 50.0;
};
std::vector<Vec3> vehicle_check_points(const Checkpoints&, const Pose&,
                                     bool observer, std::uint64_t phase = 0);
double foliage_camouflage_bonus(const FoliageSnapshot&, const Vec3& observer,
                                 const Vec3& target, bool fired_recently,
                                 const Vec3& start, const Vec3& end);
PreparedVisibility prepare_visibility(const PairInput&, const FoliageSnapshot&);
// Main thread queries the existing sight path in order and can stop after a
// clear zero-cover ray. Flags contain only its actually queried prefix.
bool should_stop(const PreparedVisibility&, std::size_t ray_index, bool clear);
VisibilityResult reduce_visibility(const PreparedVisibility&,
                                   const std::vector<std::uint8_t>& clear_prefix);
}  // namespace native_visibility
#endif
