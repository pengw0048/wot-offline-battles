#pragma once

#include "wot/ballistics.hpp"

#include <array>
#include <cstddef>
#include <functional>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace wot {

enum class ShellKind { ap, apcr, heat, he, aphe, other };
ShellKind shell_kind(std::string_view descriptor_kind);

struct CombatShell {
    ShellKind kind = ShellKind::ap;
    double caliber = 100.0;
    std::optional<double> damage;
    double explosion_radius = 0.0;
    std::optional<double> explosion_damage_factor;
    std::optional<double> explosion_absorption_factor;
    std::optional<double> explosion_edge_damage_factor;
};

struct CombatShot {
    CombatShell shell;
    std::array<double, 2> piercing_power{100.0, 100.0};
    double max_distance = 0.0;
};

struct ArmorMaterial {
    std::optional<double> armor = 0.0;
    std::optional<double> vehicle_damage_factor = 1.0;
    std::optional<int> kind;
    bool use_hit_angle = true;
    bool may_ricochet = true;
    bool check_caliber_for_hit_angle_norm = true;
    // Descriptor adapters must read #1513's checkCaliberForRichet field.
    bool check_caliber_for_ricochet = true;
    bool collide_once_only = false;
    bool use_antifragmentation_lining = true;
};

struct ArmorCollision {
    double distance = 0.0;
    double angle_cos = 1.0;
    const ArmorMaterial* material = nullptr;
    std::string component;
};

enum class PenetrationResult { ricochet = 0, stopped = 1, penetrated = 2 };
struct Penetration {
    PenetrationResult result;
    double effective_armor;
    double piercing;
};

enum class ArmorLayer { external, structural };
struct ArmorContact {
    PenetrationResult result;
    ArmorLayer layer;
    double distance;
    ArmorMaterial material;
    double accumulated_loss;
    double effective_armor;
    double piercing;
    double angle_cos;
    std::string component;
    std::size_t collision_index;
};

// A caller can supply its shot-owned RNG. Empty samplers use a local normal
// generator and retain distribution semantics, not Python's seeded sequence.
// Replay existing shots by injecting their frozen rolls or original sampler.
// The distribution is conditionally resampled inside +/-25%.
using GaussianSampler = std::function<double(double mean, double sigma)>;
double sample_shell_value(double mean, const GaussianSampler& sampler = {});
double sample_penetration_factor(const GaussianSampler& sampler = {});
double range_piercing(const CombatShot& shot, double distance);
double sampled_piercing(const CombatShot& shot, double distance, double factor,
    double pierce_loss = 0.0, double base_penetration_multiplier = 1.0);
double nominal_piercing_after_loss(const CombatShot& shot, double distance,
    double pierce_loss = 0.0);
std::optional<double> first_ricochet_penetration_multiplier(ShellKind kind);

Penetration penetration(const CombatShot& shot, double distance, double armor,
    double angle_cos, double pierce_loss = 0.0,
    std::optional<double> penetration_factor = std::nullopt,
    const ArmorMaterial* material = nullptr, bool allow_ricochet = true,
    double base_penetration_multiplier = 1.0,
    const GaussianSampler& sampler = {});

// Collision order is established here. Input materials are borrowed only for
// the call; returned contacts own their material. One penetration roll is reused
// across all collisions in this call;
// pass a frozen factor to retain the same roll across world obstacles as well.
std::optional<ArmorContact> resolve_armor_contact(const CombatShot& shot,
    double distance, const std::vector<ArmorCollision>& collisions,
    double pierce_loss = 0.0,
    std::optional<double> penetration_factor = std::nullopt,
    double base_penetration_multiplier = 1.0,
    const GaussianSampler& sampler = {});
std::optional<ArmorContact> resolve_hull_hit(const CombatShot& shot,
    double distance, const std::vector<ArmorCollision>& collisions,
    double pierce_loss = 0.0,
    std::optional<double> penetration_factor = std::nullopt,
    double base_penetration_multiplier = 1.0,
    const GaussianSampler& sampler = {});

struct HeFactors {
    double damage = 0.5;
    double absorption = 1.3;
    double edge_damage = 0.15;
};
struct HeTuning {
    std::optional<double> splash_fraction;
    std::optional<double> armor_factor;
};

bool is_he(const CombatShot& shot);
double he_radius(const CombatShot& shot);
HeFactors he_factors(const CombatShot& shot, const HeTuning& tuning = {});
std::optional<double> he_nominal_armor(
    const std::vector<ArmorCollision>& collisions);
double shell_damage_roll(const CombatShot& shot,
    const GaussianSampler& sampler = {});
double shell_nominal_damage(const CombatShot& shot);
int he_damage(const CombatShot& shot, double base_damage, double nominal_armor,
    double distance_fraction = 0.0, double spall_coefficient = 1.0,
    const HeTuning& tuning = {});
int damage(const CombatShot& shot, PenetrationResult result, double nominal_armor,
    std::optional<double> rolled_damage = std::nullopt,
    double spall_coefficient = 1.0, const GaussianSampler& sampler = {},
    const HeTuning& tuning = {});
int he_splash_damage(const CombatShot& shot, double nominal_armor,
    double distance_fraction, double spall_coefficient = 1.0,
    const GaussianSampler& sampler = {}, const HeTuning& tuning = {});

struct HeBlastContact {
    int damage;
    double nominal_armor;
    double distance;
    BallisticVector3 point;
    BallisticVector3 direction;
    std::size_t collision_index;
    std::vector<std::size_t> through_collisions;
};

// Consumes actual collision evidence; never samples a new damage roll or
// substitutes a descriptor-wide armor value when the ray has no structure.
std::optional<HeBlastContact> he_blast_contact(const CombatShot& shot,
    BallisticVector3 burst, BallisticVector3 start, BallisticVector3 end,
    const std::vector<ArmorCollision>& collisions, double rolled_damage,
    double spall_coefficient = 1.0, const HeTuning& tuning = {});

} // namespace wot
