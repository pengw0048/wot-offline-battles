#include "wot/combat_rules.hpp"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>

namespace {

using wot::ArmorCollision;
using wot::ArmorContact;
using wot::ArmorLayer;
using wot::ArmorMaterial;
using wot::CombatShot;
using wot::PenetrationResult;
using wot::ShellKind;
constexpr double pi = 3.14159265358979323846;

double cosine(double degrees) { return std::cos(degrees * (pi / 180.0)); }
bool close(double a, double b) {
    return std::abs(a - b) <= 1e-10 * std::max({1.0, std::abs(a), std::abs(b)});
}
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

CombatShot shot(ShellKind kind = ShellKind::ap, double caliber = 90.0,
               double p100 = 160.0, double p500 = 120.0) {
    CombatShot value;
    value.shell.kind = kind;
    value.shell.caliber = caliber;
    value.shell.damage = 240.0;
    value.piercing_power = {p100, p500};
    value.max_distance = 720.0;
    return value;
}
ArmorMaterial material(double armor, double vdf = 1.0) {
    ArmorMaterial value;
    value.armor = armor;
    value.vehicle_damage_factor = vdf;
    return value;
}
ArmorCollision collision(double distance, const ArmorMaterial* mat,
                         double angle = 1.0, std::string component = "vehicleHull") {
    return {distance, angle, mat, std::move(component)};
}
auto penetrate(const CombatShot& value, double armor, double angle,
               const ArmorMaterial* mat = nullptr) {
    return wot::penetration(value, 50.0, armor, angle, 0.0, 1.0, mat);
}

void sampling_and_range() {
    auto value = shot(ShellKind::ap, 90.0, 200.0, 100.0);
    value.max_distance = 900.0;
    require(wot::range_piercing(value, -1) == 200.0 &&
        wot::range_piercing(value, 100) == 200.0 &&
        wot::range_piercing(value, 300) == 150.0 &&
        wot::range_piercing(value, 500) == 100.0 &&
        wot::range_piercing(value, 700) == 50.0 &&
        wot::range_piercing(value, 899) == .25 &&
        wot::range_piercing(value, 900) == 0.0, "P100/P500 slope and lifetime");
    value.max_distance = 0;
    require(wot::range_piercing(value, 100) == 200.0 &&
        wot::range_piercing(value, 100.001) == 0.0, "near range precedes lifetime check");
    value.max_distance = 350;
    require(wot::range_piercing(value, 300) == 150.0 &&
        close(wot::range_piercing(value, 349.999), 137.50025) &&
        wot::range_piercing(value, 350) == 0.0, "short projectile lifetime");
    require(wot::sampled_piercing(value, 300, .8, 20, .75) == 70.0 &&
        wot::nominal_piercing_after_loss(value, 300, 200) == 0.0,
        "factor and ricochet multiplier precede obstacle loss");
    for (auto kind : {ShellKind::ap, ShellKind::apcr}) {
        require(wot::first_ricochet_penetration_multiplier(kind) == .75, "AP bounce retention");
    }
    require(wot::first_ricochet_penetration_multiplier(ShellKind::heat) == 1.0 &&
        !wot::first_ricochet_penetration_multiplier(ShellKind::he) &&
        !wot::first_ricochet_penetration_multiplier(ShellKind::aphe), "other bounce retention");
    int draws = 0;
    const auto sampled = wot::sample_shell_value(100, [&](double mean, double sigma) {
        require(mean == 100 && close(sigma, 100.0 / 12.0), "three sigma distribution");
        return ++draws == 1 ? 126.0 : 75.0;
    });
    require(draws == 2 && sampled == 75.0, "outliers are resampled, inclusive low boundary");
    require(wot::sample_shell_value(100, [](double, double) { return 125.0; }) == 125,
        "inclusive high boundary");
    require(wot::sample_shell_value(-1) == 0 && wot::sample_shell_value(0) == 0,
        "nonpositive shell means");
    bool rejected = false;
    try {
        wot::sample_shell_value(1, [](double, double) {
            return std::numeric_limits<double>::infinity();
        });
    } catch (const std::invalid_argument&) { rejected = true; }
    require(rejected, "nonfinite injected roll is rejected");
}

void armor_laws() {
    // Expected armor values are direct outputs from the retained Python law.
    struct Case { ShellKind kind; double caliber; double angle; double expected; };
    const Case cases[] = {
        {ShellKind::ap, 120, 60, 104.60680773726587},
        {ShellKind::ap, 120.001, 60, 99.69827376746385},
        {ShellKind::apcr, 120, 60, 113.22479488799146},
        {ShellKind::apcr, 120.001, 60, 110.76066588250576},
        {ShellKind::ap, 180.001, 75, 139.36893235637467},
        {ShellKind::apcr, 180.001, 75, 182.44455747785318},
        {ShellKind::aphe, 180, 75, 231.8221983093764},
        {ShellKind::heat, 1000, 84.999, 688.285487364676},
        {ShellKind::he, 100, 75, 231.8221983093764},
    };
    for (const auto& test : cases) {
        const auto result = penetrate(shot(test.kind, test.caliber, 1000, 1000),
            60, cosine(test.angle));
        require(result.result == PenetrationResult::penetrated &&
            close(result.effective_armor, test.expected) && result.piercing == 1000,
            "normalization and shell-kind reference cases");
    }
    for (auto kind : {ShellKind::ap, ShellKind::apcr}) {
        const auto value = shot(kind, 180, 1000, 1000);
        require(penetrate(value, 60, cosine(75)).result == PenetrationResult::ricochet,
            "three-caliber exemption is strictly greater");
        require(penetrate(value, 60, cosine(69.999)).result != PenetrationResult::ricochet &&
            penetrate(value, 60, cosine(70)).result == PenetrationResult::ricochet,
            "AP 70 degree ricochet boundary");
    }
    require(penetrate(shot(ShellKind::heat, 1000, 2000, 2000), 60, cosine(85)).result ==
        PenetrationResult::ricochet, "HEAT 85 degree boundary ignores overmatch");
    auto mat = material(60);
    mat.use_hit_angle = false;
    require(penetrate(shot(ShellKind::ap, 90, 100, 100), 60, cosine(89), &mat).effective_armor == 60,
        "material disables hit angle");
    mat.use_hit_angle = true;
    mat.may_ricochet = false;
    require(penetrate(shot(ShellKind::ap, 90, 1000, 1000), 60, cosine(75), &mat).result ==
        PenetrationResult::penetrated, "material disables bounce");
    mat.may_ricochet = true;
    mat.check_caliber_for_ricochet = false;
    require(penetrate(shot(ShellKind::ap, 200, 1000, 1000), 60, cosine(75), &mat).result ==
        PenetrationResult::ricochet, "material disables caliber bounce exemption");
    mat.check_caliber_for_hit_angle_norm = false;
    require(close(penetrate(shot(ShellKind::ap, 150, 1000, 1000), 60, cosine(60), &mat).effective_armor,
        104.60680773726587), "material disables caliber normalization increase");
    require(close(penetrate(shot(ShellKind::aphe), 60, -.5).effective_armor, 6000000),
        "negative cosine is not reflected");
    require(penetrate(shot(ShellKind::heat, 90, 100, 100), 100, 1).result ==
        PenetrationResult::penetrated &&
        penetrate(shot(ShellKind::heat, 90, 0, 0), 0, 1).result == PenetrationResult::stopped,
        "penetration equality and zero-power boundary");
    require(wot::shell_kind("HIGH_EXPLOSIVE") == ShellKind::he &&
        wot::shell_kind("HOLLOW_CHARGE") == ShellKind::heat &&
        wot::shell_kind("ARMOR_PIERCING_HE") == ShellKind::aphe &&
        wot::shell_kind("unrecognized") == ShellKind::other, "descriptor kind mapping");
}

void ordered_contacts() {
    auto screen = material(20, 0), hull = material(100);
    const auto heat = shot(ShellKind::heat, 90, 400, 400);
    auto contact = wot::resolve_hull_hit(heat, 50,
        {collision(5.2, &hull), collision(5, &screen)}, 0, 1);
    require(contact && contact->result == PenetrationResult::penetrated &&
        close(contact->accumulated_loss, 54.2) && close(contact->piercing, 345.8) &&
        contact->collision_index == 0, "HEAT screen, nominal plate exit and sorted hull contact");
    screen.collide_once_only = true;
    screen.kind = 7;
    auto screen_exit = screen;
    hull.armor = 80;
    contact = wot::resolve_armor_contact(shot(ShellKind::heat, 90, 200, 200), 50,
        {collision(5, &screen, 1, "vehicleChassis"),
         collision(5.5, &screen_exit, 1, "vehicleChassis"), collision(6, &hull)}, 0, 1);
    require(contact && contact->result == PenetrationResult::stopped &&
        close(contact->accumulated_loss, 130.232) && close(contact->piercing, 69.768),
        "HEAT charges duplicate gap before collide-once deduplication");
    contact = wot::resolve_armor_contact(shot(ShellKind::ap, 90, 200, 200), 50,
        {collision(5, &screen), collision(5.5, &screen_exit), collision(6, &hull)}, 0, 1);
    require(contact && contact->piercing == 180, "same component/material kind is crossed once");
    contact = wot::resolve_armor_contact(shot(ShellKind::ap, 90, 200, 200), 50,
        {collision(5, &screen), collision(5.5, &screen_exit, 1, "vehicleGun"),
         collision(6, &hull)}, 0, 1);
    require(contact && contact->piercing == 160, "collide-once key includes component");
    screen.kind.reset();
    screen_exit.kind.reset();
    contact = wot::resolve_armor_contact(shot(ShellKind::ap, 90, 200, 200), 50,
        {collision(5, &screen), collision(5.5, &screen), collision(6, &hull)}, 0, 1);
    require(contact && contact->piercing == 180, "missing kind uses material identity");
    contact = wot::resolve_armor_contact(shot(ShellKind::ap, 90, 200, 200), 50,
        {collision(5, &screen), collision(5.5, &screen_exit), collision(6, &hull)}, 0, 1);
    require(contact && contact->piercing == 160, "distinct untyped materials remain distinct");
    const auto he = shot(ShellKind::he, 90, 400, 400);
    contact = wot::resolve_armor_contact(he, 50,
        {collision(5, &screen), collision(5.2, &hull)}, 0, 1);
    require(contact && contact->layer == ArmorLayer::external &&
        contact->result == PenetrationResult::stopped &&
        !wot::resolve_hull_hit(he, 50, {collision(5, &screen), collision(5.2, &hull)}, 0, 1),
        "HE detonates at external plate even when penetrating it");
    contact = wot::resolve_armor_contact(shot(ShellKind::ap, 90, 20, 20), 50,
        {collision(5, &screen), collision(5.2, &hull)}, 0, 1);
    require(contact && contact->layer == ArmorLayer::external &&
        contact->result == PenetrationResult::stopped && contact->accumulated_loss == 0,
        "screen equality exhausts power and returns original contact ledger");
    hull.armor = 110;
    contact = wot::resolve_armor_contact(shot(ShellKind::heat, 90, 200, 200), 50,
        {collision(5, nullptr), collision(6, &hull)}, 0, 1);
    require(contact && contact->piercing == 100 && contact->result == PenetrationResult::stopped,
        "empty HEAT material starts jet gap");
    int draws = 0;
    contact = wot::resolve_hull_hit(shot(ShellKind::ap, 90, 200, 200), 50,
        {collision(5, &screen), collision(6, &hull)}, 10, std::nullopt, .75,
        [&](double, double) { ++draws; return .8; });
    require(contact && draws == 1 && close(contact->piercing, 90) &&
        contact->accumulated_loss == 30, "one shot-owned draw across all plates and prior loss");
    const auto original = contact->material.armor;
    hull.armor = 999;
    require(contact->material.armor == original, "returned contact owns material snapshot");
    require(!wot::resolve_armor_contact(heat, 50, {}, 0, std::nullopt, 1,
        [](double, double) -> double { throw std::runtime_error("unexpected roll"); }),
        "empty collisions do not sample");
}

void explosive_damage() {
    auto he = shot(ShellKind::he, 100);
    he.shell.damage = 400;
    he.shell.explosion_radius = 10;
    const wot::GaussianSampler identity = [](double mean, double) { return mean; };
    require(wot::is_he(he) && !wot::is_he(shot(ShellKind::heat)) && wot::he_radius(he) == 10,
        "HE identity and descriptor radius");
    auto no_radius = he;
    no_radius.shell.explosion_radius = 0;
    require(close(wot::he_radius(no_radius), 10000.0 / 5555), "caliber-squared radius fallback");
    require(wot::he_splash_damage(he, 20, 0, 1, identity) == 174 &&
        wot::he_splash_damage(he, 20, .5, 1, identity) == 104 &&
        wot::he_splash_damage(he, 20, 1, 1, identity) == 34 &&
        wot::he_splash_damage(he, 20, 2, 1, identity) == 34,
        "linear HE center, middle, edge and clamped distance");
    require(wot::damage(he, PenetrationResult::penetrated, 200, 321.9) == 321 &&
        wot::damage(he, PenetrationResult::stopped, 20, 321.9) == 134 &&
        wot::he_damage(he, 321.9, 20) == 134 &&
        wot::damage(shot(), PenetrationResult::stopped, 20, 321.9) == 0,
        "direct damage truncation, frozen roll and non-HE stop");
    require(wot::he_damage(he, 400, 50, 0, 1.5) == 102 &&
        wot::he_damage(he, 400, 50, 0, .5) == 135 &&
        wot::he_damage(he, 400, 50, 0, std::numeric_limits<double>::infinity()) == 135,
        "spall absorbs armor and invalid coefficients use one");
    he.shell.explosion_damage_factor = .6;
    he.shell.explosion_absorption_factor = 1;
    he.shell.explosion_edge_damage_factor = .2;
    require(wot::he_damage(he, 400, 20, .5) == 140 &&
        wot::he_factors(he).edge_damage == .2, "shell-specific explosion factors");
    he.shell.explosion_damage_factor = -1;
    he.shell.explosion_edge_damage_factor = 2;
    require(wot::he_factors(he).damage == .5 && wot::he_factors(he).edge_damage == .15,
        "invalid descriptor factors use common defaults");
    require(wot::he_factors(he, {.7, 2.0}).damage == .7 &&
        wot::he_factors(he, {.7, 2.0}).absorption == 2,
        "explicit tuning overlays descriptor factors");
    require(wot::shell_nominal_damage(he) == 400 &&
        wot::shell_damage_roll(he, [](double mean, double) { return mean * .75; }) == 300,
        "nominal and random damage");
    he.shell.damage.reset();
    require(wot::shell_nominal_damage(he) == 0, "missing damage descriptor");
}

void blast_contacts() {
    auto he = shot(ShellKind::he);
    he.shell.explosion_radius = 10;
    auto first = material(100), weaker = material(10), screen = material(20, 0);
    const wot::BallisticVector3 zero{0, 0, 0}, end{10, 0, 0};
    auto blast = wot::he_blast_contact(he, zero, zero, end,
        {collision(2, &weaker), collision(1, &first)}, 400);
    require(blast && blast->collision_index == 1 && blast->damage == 56 &&
        blast->point.x == 1 && blast->direction.x == 1,
        "blast uses first real structural plate, not thinnest backside");
    first.armor = 0;
    blast = wot::he_blast_contact(he, zero, zero, end,
        {collision(5, &first), collision(.001, &screen)}, 400);
    require(blast && blast->damage == 130 && blast->distance == 5 &&
        blast->through_collisions == std::vector<std::size_t>({1, 0}),
        "blast structural point distance and traversed evidence order");
    blast = wot::he_blast_contact(he, {5, 0, 0}, zero, end,
        {collision(4.9995, &first)}, 400);
    require(blast && close(blast->distance, .0005), "burst tolerance backstep");
    require(!wot::he_blast_contact(he, zero, zero, end, {collision(11, &first)}, 400) &&
        !wot::he_blast_contact(he, {5, 0, 0}, zero, end, {collision(4.998, &first)}, 400) &&
        !wot::he_blast_contact(he, zero, zero, zero, {collision(0, &first)}, 400),
        "blast evidence must be on nonempty segment after burst");
    first.armor.reset();
    require(!wot::he_blast_contact(he, zero, zero, end, {collision(0, &first)}, 400),
        "missing nominal armor is not zero armor");
    first.armor = 0;
    first.vehicle_damage_factor.reset();
    require(!wot::he_blast_contact(he, zero, zero, end, {collision(0, &first)}, 400),
        "blast requires explicit positive structural factor");
    first.vehicle_damage_factor = 1;
    he.shell.explosion_radius = 4;
    require(!wot::he_blast_contact(he, zero, zero, end, {collision(4.001, &first)}, 400),
        "structural contact outside sphere blocks blast");
    first.armor = 50;
    first.use_antifragmentation_lining = false;
    blast = wot::he_blast_contact(he, zero, zero, end, {collision(0, &first)}, 400, 1.5);
    require(blast && blast->damage == 135, "material opts out of spall liner");
    require(wot::he_nominal_armor({collision(0, &screen), collision(2, &weaker),
        collision(1, &first)}) == 50 && !wot::he_nominal_armor({collision(0, &screen)}),
        "nominal armor skips external layers without descriptor fallback");
}

// A compact stream mode lets the retained Python implementation exercise the
// public C++ API over broad input matrices without embedding a Python runtime.
int probe() {
    std::cout << std::setprecision(17);
    for (std::string line; std::getline(std::cin, line);) {
        std::istringstream input(line);
        std::string kind;
        double caliber = 0, p100 = 0, p500 = 0, maximum = 0, distance = 0;
        double armor = 0, ac = 0, loss = 0, factor = 0, multiplier = 0;
        int use_angle = 0, bounce = 0, norm = 0, overmatch = 0, allow_bounce = 0;
        input >> kind >> caliber >> p100 >> p500 >> maximum >> distance >> armor >> ac >> loss >>
            factor >> multiplier >> use_angle >> bounce >> norm >> overmatch >> allow_bounce;
        if (input.fail()) return 1;
        auto value = shot(wot::shell_kind(kind), caliber, p100, p500);
        value.max_distance = maximum;
        auto mat = material(armor);
        mat.use_hit_angle = use_angle != 0;
        mat.may_ricochet = bounce != 0;
        mat.check_caliber_for_hit_angle_norm = norm != 0;
        mat.check_caliber_for_ricochet = overmatch != 0;
        const auto result = wot::penetration(value, distance, armor, ac, loss, factor,
            &mat, allow_bounce != 0, multiplier);
        std::cout << '[' << static_cast<int>(result.result) << ',' << result.effective_armor << ',' <<
            result.piercing << "]\n";
    }
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--probe") return probe();
    try {
        sampling_and_range();
        armor_laws();
        ordered_contacts();
        explosive_damage();
        blast_contacts();
    } catch (const std::exception& error) {
        std::cerr << "combat rules regression failed: " << error.what() << '\n';
        return 1;
    }
    std::cout << "combat rules regressions passed\n";
    return 0;
}
