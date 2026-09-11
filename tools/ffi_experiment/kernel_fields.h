#ifndef OFFLINE_EXPERIMENT_KERNEL_FIELDS_H
#define OFFLINE_EXPERIMENT_KERNEL_FIELDS_H
#include <string>
#include <unordered_map>
#include <vector>

namespace offline_kernel {
// State fields have compile-time slots. Irregular descriptor/ledger mappings
// retain their string keys; configuration-supplied columns bind once at load.
struct Field {
    int slot;
    const char *name;
    size_t length;
    constexpr Field(int slot, const char *name, size_t length = std::string::npos)
        : slot(slot), name(name), length(length) {}
    std::string text() const {
        return length == std::string::npos ? std::string(name) : std::string(name, length);
    }
};
// One list defines slot identities and their serialized names.
#define OFFLINE_KERNEL_STATE_FIELDS(X)                                                             \
    X(_drown_check)                                                                                \
    X(_drown_time)                                                                                 \
    X(_drowned)                                                                                    \
    X(_drowning)                                                                                   \
    X(_kernel)                                                                                     \
    X(_kernel_turn_speed)                                                                          \
    X(_motion_stall_log)                                                                           \
    X(_motion_stall_pending)                                                                       \
    X(_overturn_check)                                                                             \
    X(_overturn_level)                                                                             \
    X(_overturn_time)                                                                              \
    X(_overturned)                                                                                 \
    X(_radio_ground_goal)                                                                          \
    X(_ram_contact_bot_state)                                                                      \
    X(_route_lane_desired)                                                                         \
    X(_route_lane_forward)                                                                         \
    X(_route_lane_gate)                                                                            \
    X(_route_lane_goal)                                                                            \
    X(_route_lane_group)                                                                           \
    X(_route_lane_offset)                                                                          \
    X(_route_lane_origin)                                                                          \
    X(_route_lane_row)                                                                             \
    X(_route_lane_row_desired)                                                                     \
    X(_route_lane_segment)                                                                         \
    X(_siege_intent)                                                                               \
    X(_siege_intent_elapsed)                                                                       \
    X(_siege_time_left)                                                                            \
    X(_siege_transition_total)                                                                     \
    X(_stun_until_equipment_time)                                                                  \
    X(_water_depth)                                                                                \
    X(aim_yaw)                                                                                     \
    X(air_lateral_x)                                                                               \
    X(air_lateral_z)                                                                               \
    X(airborne)                                                                                    \
    X(alive)                                                                                       \
    X(ammo_reload_pending)                                                                         \
    X(ammo_remaining)                                                                              \
    X(burst_active)                                                                                \
    X(burst_count)                                                                                 \
    X(burst_group_seq)                                                                             \
    X(burst_index)                                                                                 \
    X(burst_interval)                                                                              \
    X(burst_next_index)                                                                            \
    X(burst_shell_index)                                                                           \
    X(burst_time_left)                                                                             \
    X(cap)                                                                                         \
    X(class_tag)                                                                                   \
    X(clip)                                                                                        \
    X(clip_size)                                                                                   \
    X(collision_shape)                                                                             \
    X(combat_ack_seq)                                                                              \
    X(combat_base_revision)                                                                        \
    X(combat_fire_elapsed)                                                                         \
    X(combat_fire_timer)                                                                           \
    X(combat_revision)                                                                             \
    X(combat_seq)                                                                                  \
    X(contact_armor)                                                                               \
    X(contacts)                                                                                    \
    X(critical)                                                                                    \
    X(death_reason)                                                                                \
    X(decision_horizon)                                                                            \
    X(destructible_contact_speed)                                                                  \
    X(display_health)                                                                              \
    X(dt)                                                                                          \
    X(effective_params)                                                                            \
    X(equipment_states)                                                                            \
    X(fire_seq)                                                                                    \
    X(grounded_once)                                                                               \
    X(gun_aligned)                                                                                 \
    X(gun_pitch)                                                                                   \
    X(half_length)                                                                                 \
    X(half_width)                                                                                  \
    X(health)                                                                                      \
    X(hull_aiming)                                                                                 \
    X(id)                                                                                          \
    X(kind)                                                                                        \
    X(last_drive_pitch)                                                                            \
    X(mass)                                                                                        \
    X(max_health)                                                                                  \
    X(maximum)                                                                                     \
    X(movement_dir)                                                                                \
    X(name)                                                                                        \
    X(navigation_stop_at_target)                                                                   \
    X(neighbours)                                                                                  \
    X(network_id)                                                                                  \
    X(next_shell_index)                                                                            \
    X(no_fire_repair)                                                                              \
    X(now)                                                                                         \
    X(pitch)                                                                                       \
    X(pose_sample)                                                                                 \
    X(position)                                                                                    \
    X(profile)                                                                                     \
    X(push_x)                                                                                      \
    X(push_z)                                                                                      \
    X(ram_contact)                                                                                 \
    X(ram_contact_resolved_seq)                                                                    \
    X(ram_contacts)                                                                                \
    X(ram_profile)                                                                                 \
    X(ram_vy)                                                                                      \
    X(reload_duration)                                                                             \
    X(reload_time)                                                                                 \
    X(roll)                                                                                        \
    X(rotation_dir)                                                                                \
    X(route)                                                                                       \
    X(route_anchor)                                                                                \
    X(route_index)                                                                                 \
    X(route_join)                                                                                  \
    X(seconds)                                                                                     \
    X(shell_index)                                                                                 \
    X(shells_before_shot)                                                                          \
    X(shot_origin)                                                                                 \
    X(shot_pitch)                                                                                  \
    X(shot_proof_key)                                                                              \
    X(shot_yaw)                                                                                    \
    X(siege_state)                                                                                 \
    X(siege_time_left_ms)                                                                          \
    X(siege_transition_total_ms)                                                                   \
    X(skill_rating)                                                                                \
    X(slide_speed)                                                                                 \
    X(slot)                                                                                        \
    X(speed)                                                                                       \
    X(stopping_distance)                                                                           \
    X(stun_end_server_time_ms)                                                                     \
    X(suspension_pitch)                                                                            \
    X(target_id)                                                                                   \
    X(target_kind)                                                                                 \
    X(team)                                                                                        \
    X(terrain_pitch)                                                                               \
    X(turret_yaw)                                                                                  \
    X(vehicle)                                                                                     \
    X(velocity)                                                                                    \
    X(vertical_speed)                                                                              \
    X(view_range)                                                                                  \
    X(visible)                                                                                     \
    X(world_pose)                                                                                  \
    X(x)                                                                                           \
    X(y)                                                                                           \
    X(yaw)                                                                                         \
    X(z)
namespace sf {
#define OFFLINE_FIELD_INDEX(name) index_##name,
enum Index { OFFLINE_KERNEL_STATE_FIELDS(OFFLINE_FIELD_INDEX) count };
#undef OFFLINE_FIELD_INDEX
#define OFFLINE_FIELD_VALUE(name) static constexpr Field name = {index_##name, #name};
OFFLINE_KERNEL_STATE_FIELDS(OFFLINE_FIELD_VALUE)
#undef OFFLINE_FIELD_VALUE
} // namespace sf
inline const char *field_name(int slot) {
#define OFFLINE_FIELD_NAME(name) #name,
    static const char *const names[] = {OFFLINE_KERNEL_STATE_FIELDS(OFFLINE_FIELD_NAME)};
#undef OFFLINE_FIELD_NAME
    return names[slot];
}
#undef OFFLINE_KERNEL_STATE_FIELDS
inline const std::string &field_string(int slot) {
    static const std::vector<std::string> names = [] {
        std::vector<std::string> result;
        for (int i = 0; i < sf::count; ++i)
            result.emplace_back(field_name(i));
        return result;
    }();
    return names[slot];
}
inline int field_slot(const std::string &name) {
    static const std::unordered_map<std::string, int> slots = [] {
        std::unordered_map<std::string, int> result;
        for (int i = 0; i < sf::count; ++i)
            result.emplace(field_name(i), i);
        return result;
    }();
    auto at = slots.find(name);
    return at == slots.end() ? -1 : at->second;
}
} // namespace offline_kernel
#endif
