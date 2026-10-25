#include "wot/combat_rules.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <utility>

namespace wot {
namespace {

constexpr double pi = 3.14159265358979323846;
constexpr double min_angle_cos = 1.0e-5;
double radians(double degrees) { return degrees * (pi / 180.0); }

double normal_roll(double mean, double sigma) {
    static thread_local std::mt19937_64 engine(std::random_device{}());
    return std::normal_distribution<double>(mean, sigma)(engine);
}

std::vector<std::size_t> collision_order(
    const std::vector<ArmorCollision>& collisions, bool finite_only = false) {
    std::vector<std::size_t> order;
    order.reserve(collisions.size());
    for (std::size_t index = 0; index < collisions.size(); ++index) {
        if (!finite_only || std::isfinite(collisions[index].distance)) {
            order.push_back(index);
        }
    }
    std::stable_sort(order.begin(), order.end(), [&](auto a, auto b) {
        return collisions[a].distance < collisions[b].distance;
    });
    return order;
}

double positive_factor(std::optional<double> value, double fallback,
                       double maximum = std::numeric_limits<double>::infinity()) {
    if (!value || !std::isfinite(*value) || *value <= 0.0 || *value > maximum) {
        return fallback;
    }
    return *value;
}

double spall_factor(double value) {
    return std::isfinite(value) && value >= 1.0 ? value : 1.0;
}

int integer_damage(double value) {
    if (!std::isfinite(value) ||
        value > static_cast<double>(std::numeric_limits<int>::max()) ||
        value < static_cast<double>(std::numeric_limits<int>::min())) {
        throw std::overflow_error("damage does not fit integer hit points");
    }
    return static_cast<int>(value);
}

bool finite(BallisticVector3 value) {
    return std::isfinite(value.x) && std::isfinite(value.y) && std::isfinite(value.z);
}
BallisticVector3 subtract(BallisticVector3 a, BallisticVector3 b) {
    return {a.x - b.x, a.y - b.y, a.z - b.z};
}
double dot(BallisticVector3 a, BallisticVector3 b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

} // namespace

ShellKind shell_kind(std::string_view kind) {
    if (kind == "ARMOR_PIERCING") return ShellKind::ap;
    if (kind == "ARMOR_PIERCING_CR") return ShellKind::apcr;
    if (kind == "HOLLOW_CHARGE") return ShellKind::heat;
    if (kind == "HIGH_EXPLOSIVE") return ShellKind::he;
    if (kind == "ARMOR_PIERCING_HE") return ShellKind::aphe;
    return ShellKind::other;
}

double sample_shell_value(double mean, const GaussianSampler& sampler) {
    if (!std::isfinite(mean)) throw std::invalid_argument("shell roll mean must be finite");
    if (mean <= 0.0) return 0.0;
    const double minimum = mean * 0.75, maximum = mean * 1.25;
    const double sigma = mean * 0.25 / 3.0;
    for (;;) {
        const double value = sampler ? sampler(mean, sigma) : normal_roll(mean, sigma);
        if (!std::isfinite(value)) throw std::invalid_argument("shell roll must be finite");
        if (minimum <= value && value <= maximum) return value;
    }
}

double sample_penetration_factor(const GaussianSampler& sampler) {
    return sample_shell_value(1.0, sampler);
}

double range_piercing(const CombatShot& shot, double distance) {
    if (distance <= 100.0) return shot.piercing_power[0];
    if (shot.max_distance <= 0.0 || distance >= shot.max_distance) return 0.0;
    const double t = (distance - 100.0) / 400.0;
    return std::max(0.0, shot.piercing_power[0] +
        (shot.piercing_power[1] - shot.piercing_power[0]) * t);
}

double sampled_piercing(const CombatShot& shot, double distance, double factor,
    double pierce_loss, double multiplier) {
    return std::max(0.0, range_piercing(shot, distance) * multiplier * factor - pierce_loss);
}

double nominal_piercing_after_loss(const CombatShot& shot, double distance,
    double pierce_loss) {
    return std::max(0.0, range_piercing(shot, distance) - pierce_loss);
}

std::optional<double> first_ricochet_penetration_multiplier(ShellKind kind) {
    if (kind == ShellKind::ap || kind == ShellKind::apcr) return 0.75;
    if (kind == ShellKind::heat) return 1.0;
    return std::nullopt;
}

Penetration penetration(const CombatShot& shot, double distance, double armor,
    double angle_cos, double pierce_loss, std::optional<double> factor,
    const ArmorMaterial* material, bool allow_ricochet, double multiplier,
    const GaussianSampler& sampler) {
    const auto kind = shot.shell.kind;
    const bool normalizing_ap = kind == ShellKind::ap || kind == ShellKind::apcr;
    const double pierce = sampled_piercing(shot, distance,
        factor ? *factor : sample_penetration_factor(sampler), pierce_loss, multiplier);
    armor = std::max(0.0, armor);
    const bool use_angle = !material || material->use_hit_angle;
    const double ac = use_angle ? angle_cos : 1.0;
    const double caliber = shot.shell.caliber != 0.0 ? shot.shell.caliber : 100.0;
    double norm = kind == ShellKind::apcr ? radians(2.0) :
        (kind == ShellKind::ap ? radians(5.0) : 0.0);
    if (use_angle && normalizing_ap &&
        (!material || material->check_caliber_for_hit_angle_norm) &&
        caliber > armor * 2.0 && armor * 2.0 > 0.0) {
        norm *= 1.4 * caliber / (armor * 2.0);
    }
    const bool may_ricochet = allow_ricochet && use_angle &&
        (normalizing_ap || kind == ShellKind::heat) &&
        (!material || material->may_ricochet);
    const bool no_ap_ricochet = normalizing_ap &&
        (!material || material->check_caliber_for_ricochet) && caliber > armor * 3.0;
    if (may_ricochet && ((normalizing_ap && !no_ap_ricochet && ac <= std::cos(radians(70.0))) ||
        (kind == ShellKind::heat && ac <= std::cos(radians(85.0))))) {
        return {PenetrationResult::ricochet, armor / std::max(min_angle_cos, ac), pierce};
    }
    double effective_cos = ac;
    if (use_angle && norm > 0.0 && effective_cos < 1.0) {
        if (effective_cos < -1.0) throw std::domain_error("hit angle cosine is below -1");
        double angle = std::acos(effective_cos) - norm;
        if (angle < 0.0) effective_cos = 1.0;
        else {
            angle = std::min(angle, pi / 2.0 - min_angle_cos);
            effective_cos = std::cos(angle);
        }
    }
    effective_cos = std::max(effective_cos, min_angle_cos);
    const double effective_armor = armor / effective_cos;
    return {pierce > 0.0 && pierce >= effective_armor ?
        PenetrationResult::penetrated : PenetrationResult::stopped, effective_armor, pierce};
}

std::optional<ArmorContact> resolve_armor_contact(const CombatShot& shot,
    double distance, const std::vector<ArmorCollision>& collisions,
    double pierce_loss, std::optional<double> factor, double multiplier,
    const GaussianSampler& sampler) {
    const bool heat = shot.shell.kind == ShellKind::heat;
    double spaced = pierce_loss;
    std::optional<double> base_pierce, heat_last_plate;
    std::vector<std::size_t> seen_once;
    for (auto index : collision_order(collisions)) {
        const auto& collision = collisions[index];
        const auto* mat = collision.material;
        if (!factor) factor = sample_penetration_factor(sampler);
        if (!base_pierce) base_pierce = range_piercing(shot, distance) * multiplier * *factor;
        if (heat && heat_last_plate) {
            const double gap = std::max(0.0, collision.distance - *heat_last_plate);
            spaced += std::max(0.0, *base_pierce - spaced) * std::min(1.0, 0.5 * gap);
        }
        if (!mat) {
            if (*base_pierce - spaced <= 0.0) break;
            if (heat) heat_last_plate = collision.distance;
            continue;
        }
        if (mat->collide_once_only) {
            const bool seen = std::any_of(seen_once.begin(), seen_once.end(), [&](auto previous) {
                const auto& other = collisions[previous];
                return collision.component == other.component &&
                    (mat->kind ? other.material->kind == mat->kind : other.material == mat);
            });
            if (seen) continue;
        }
        const double vdf = mat->vehicle_damage_factor.value_or(1.0);
        const double armor = mat->armor.value_or(0.0);
        if (armor < 0.0) continue;
        const auto pen = penetration(shot, distance, armor, collision.angle_cos,
            spaced, factor, mat, !(heat && heat_last_plate), multiplier, sampler);
        ArmorContact contact{pen.result, vdf != 0.0 ? ArmorLayer::structural : ArmorLayer::external,
            collision.distance, *mat, spaced, pen.effective_armor, pen.piercing,
            collision.angle_cos, collision.component, index};
        if (vdf != 0.0) return contact;
        if (shot.shell.kind == ShellKind::he) {
            contact.result = PenetrationResult::stopped;
            return contact;
        }
        if (pen.result != PenetrationResult::penetrated) return contact;
        spaced += pen.effective_armor;
        if (*base_pierce - spaced <= 0.0) {
            contact.result = PenetrationResult::stopped;
            return contact;
        }
        if (heat) heat_last_plate = collision.distance + armor * 0.001;
        if (mat->collide_once_only) seen_once.push_back(index);
    }
    return std::nullopt;
}

std::optional<ArmorContact> resolve_hull_hit(const CombatShot& shot,
    double distance, const std::vector<ArmorCollision>& collisions,
    double pierce_loss, std::optional<double> factor, double multiplier,
    const GaussianSampler& sampler) {
    auto contact = resolve_armor_contact(shot, distance, collisions,
        pierce_loss, factor, multiplier, sampler);
    if (contact && contact->layer == ArmorLayer::structural) return contact;
    return std::nullopt;
}

bool is_he(const CombatShot& shot) { return shot.shell.kind == ShellKind::he; }

double he_radius(const CombatShot& shot) {
    if (shot.shell.explosion_radius > 0.0) return shot.shell.explosion_radius;
    const double caliber = shot.shell.caliber;
    return caliber > 0.0 ? caliber * caliber / 5555.0 : 0.0;
}

HeFactors he_factors(const CombatShot& shot, const HeTuning& tuning) {
    HeFactors result{positive_factor(shot.shell.explosion_damage_factor, 0.5),
        positive_factor(shot.shell.explosion_absorption_factor, 1.3),
        positive_factor(shot.shell.explosion_edge_damage_factor, 0.15, 1.0)};
    if (tuning.splash_fraction && std::isfinite(*tuning.splash_fraction)) {
        result.damage = *tuning.splash_fraction;
    }
    if (tuning.armor_factor && std::isfinite(*tuning.armor_factor)) {
        result.absorption = *tuning.armor_factor;
    }
    return result;
}

std::optional<double> he_nominal_armor(const std::vector<ArmorCollision>& collisions) {
    for (auto index : collision_order(collisions)) {
        const auto* mat = collisions[index].material;
        if (!mat || mat->vehicle_damage_factor.value_or(1.0) == 0.0) continue;
        const double armor = mat->armor.value_or(0.0);
        if (armor > 0.0) return armor;
    }
    return std::nullopt;
}

double shell_damage_roll(const CombatShot& shot, const GaussianSampler& sampler) {
    return shot.shell.damage ? sample_shell_value(*shot.shell.damage, sampler) : 0.0;
}
double shell_nominal_damage(const CombatShot& shot) {
    return shell_damage_roll(shot, [](double mean, double) { return mean; });
}

int he_damage(const CombatShot& shot, double base_damage, double nominal_armor,
    double distance_fraction, double spall_coefficient, const HeTuning& tuning) {
    const auto factors = he_factors(shot, tuning);
    distance_fraction = std::max(0.0, std::min(1.0, distance_fraction));
    const double blast_factor = factors.damage +
        (factors.edge_damage - factors.damage) * distance_fraction;
    const double value = base_damage * blast_factor -
        factors.absorption * nominal_armor * spall_factor(spall_coefficient);
    return value > 0.0 ? integer_damage(value) : 0;
}

int damage(const CombatShot& shot, PenetrationResult result, double nominal_armor,
    std::optional<double> rolled_damage, double spall_coefficient,
    const GaussianSampler& sampler, const HeTuning& tuning) {
    const int rolled = integer_damage(rolled_damage ? *rolled_damage : shell_damage_roll(shot, sampler));
    if (result == PenetrationResult::penetrated) return rolled;
    if (is_he(shot)) return he_damage(shot, rolled, nominal_armor, 0.0, spall_coefficient, tuning);
    return 0;
}

int he_splash_damage(const CombatShot& shot, double nominal_armor,
    double distance_fraction, double spall_coefficient,
    const GaussianSampler& sampler, const HeTuning& tuning) {
    return he_damage(shot, shell_damage_roll(shot, sampler), nominal_armor,
        distance_fraction, spall_coefficient, tuning);
}

std::optional<HeBlastContact> he_blast_contact(const CombatShot& shot,
    BallisticVector3 burst, BallisticVector3 start, BallisticVector3 end,
    const std::vector<ArmorCollision>& collisions, double rolled_damage,
    double spall_coefficient, const HeTuning& tuning) {
    const double radius = he_radius(shot);
    if (!is_he(shot) || !finite(burst) || !finite(start) || !finite(end) ||
        radius <= 0.0 || !std::isfinite(rolled_damage)) return std::nullopt;
    const auto delta = subtract(end, start);
    const double length_squared = dot(delta, delta);
    if (length_squared <= 0.0) return std::nullopt;
    const double length = std::sqrt(length_squared);
    const BallisticVector3 direction{delta.x / length, delta.y / length, delta.z / length};
    const double burst_along = dot(subtract(burst, start), direction);
    std::vector<std::size_t> through;
    for (auto index : collision_order(collisions, true)) {
        const auto& collision = collisions[index];
        const double along = collision.distance;
        if (along < 0.0 || along > length || along < burst_along - 1.0e-3) continue;
        const BallisticVector3 point{start.x + direction.x * along,
            start.y + direction.y * along, start.z + direction.z * along};
        const auto offset = subtract(point, burst);
        const double distance = std::sqrt(dot(offset, offset));
        const auto* mat = collision.material;
        const bool structural = mat && mat->vehicle_damage_factor &&
            std::isfinite(*mat->vehicle_damage_factor) && *mat->vehicle_damage_factor > 0.0;
        if (distance > radius) {
            if (structural) return std::nullopt;
            continue;
        }
        through.push_back(index);
        if (!structural) continue;
        if (!mat->armor || !std::isfinite(*mat->armor) || *mat->armor < 0.0) return std::nullopt;
        const double spall = mat->use_antifragmentation_lining ? spall_coefficient : 1.0;
        return HeBlastContact{he_damage(shot, rolled_damage, *mat->armor,
            distance / radius, spall, tuning), *mat->armor, distance, point,
            direction, index, std::move(through)};
    }
    return std::nullopt;
}

} // namespace wot
