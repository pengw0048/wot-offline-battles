#include "wot/ballistics.hpp"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

namespace {

bool close(double actual, double expected) {
    return std::abs(actual - expected) <=
        1e-9 * std::max({1.0, std::abs(actual), std::abs(expected)});
}

bool close(wot::BallisticVector3 actual, wot::BallisticVector3 expected) {
    return close(actual.x, expected.x) && close(actual.y, expected.y) &&
           close(actual.z, expected.z);
}

void require(bool condition, const char* message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void regressions() {
    const wot::BallisticVector3 start{0, 0, 0};
    const wot::BallisticVector3 target{100, 0, 0};
    const auto roots = wot::ballistic_solutions(start, target, 80, 10);
    require(roots.size() == 2, "two reachable arcs");
    require(roots[0].pitch > roots[1].pitch && roots[0].pitch < 0 &&
            roots[0].flight_time < roots[1].flight_time, "arc order and pitch sign");
    for (const auto root : roots) {
        require(close(wot::ballistic_position(start, wot::ballistic_half_pi,
            root.pitch, 80, 10, root.flight_time), target), "arc hits target");
    }
    const auto negative_g = wot::ballistic_solutions(start, target, 80, -10);
    require(close(roots[0].pitch, negative_g[0].pitch) &&
            close(roots[1].flight_time, negative_g[1].flight_time), "gravity sign");
    require(wot::ballistic_solutions(start, {1000, 0, 0}, 80, 10).empty(),
            "unreachable target");
    require(wot::ballistic_solutions(start, target, 1, 10).empty() &&
            wot::ballistic_solutions(start, target, 80, .01).empty() &&
            wot::ballistic_solutions(start, {.1, 0, 0}, 80, 10).empty(),
            "solver thresholds");
    require(wot::ballistic_solutions(start, {640, 0, 0}, 80, 10).size() == 1,
            "coincident roots deduplicate");
    const auto fast = wot::ballistic_solutions(start, target, 1000, 10);
    require(fast.size() == 1 && fast[0].flight_time < 20, "flight time ceiling");
    require(wot::ballistic_solutions(start, target, 80, 10, -.2, 0).size() == 1,
            "pitch limits select low arc");
    require(wot::ballistic_solutions(start, target, 80, 10,
            roots[0].pitch + .00005, 0).size() == 1 &&
            wot::ballistic_solutions(start, target, 80, 10,
            roots[0].pitch + .0002, 0).empty(), "pitch tolerance");

    const auto high = wot::ballistic_intercept(start, target, start, 80, 10,
        -wot::ballistic_half_pi, wot::ballistic_half_pi, true);
    require(high && close(high->pitch, roots[1].pitch), "prefer high arc");
    const wot::BallisticVector3 velocity{30, 3, 5};
    const auto moving = wot::ballistic_intercept(start, target, velocity, 80, 10);
    require(moving.has_value(), "moving intercept");
    auto aim = target;
    for (int index = 0; index < 4; ++index) {
        const double time = wot::ballistic_solutions(start, aim, 80, 10)[0].flight_time;
        aim = {target.x + velocity.x * time, target.y + velocity.y * time,
               target.z + velocity.z * time};
    }
    require(close(moving->aim_point, aim), "exactly four lead iterations");
    const auto final = wot::ballistic_solutions(start, aim, 80, 10)[0];
    require(close(moving->pitch, final.pitch) &&
            close(moving->flight_time, final.flight_time), "final coordinate re-solve");
    require(close(wot::ballistic_position(start, std::atan2(aim.x, aim.z),
            moving->pitch, 80, 10, moving->flight_time), aim),
            "returned trajectory hits returned lead coordinate");
    require(!wot::ballistic_intercept(start, target, start, 80, 10,
            -wot::ballistic_half_pi, wot::ballistic_half_pi, false, .5),
            "maximum lead rejects long flight");
    require(wot::ballistic_intercept(start, target, start, 80, 10,
            -wot::ballistic_half_pi, wot::ballistic_half_pi, false, 0) &&
            wot::ballistic_intercept(start, target, start, 80, 10,
            -wot::ballistic_half_pi, wot::ballistic_half_pi, false, -1),
            "nonpositive lead limit disables limit");
    // The first solve passes this cap; a later iteration must still reject it.
    require(!wot::ballistic_intercept(start, target, velocity, 80, 10,
            -wot::ballistic_half_pi, wot::ballistic_half_pi, false, 1.5),
            "lead cap is checked on later iterations");

    const wot::BallisticVector3 offset{12, 20, -30};
    require(close(wot::ballistic_position(offset, .2, -.3, 80, 10, -1), offset),
            "negative time clamps to zero");
    require(close(wot::ballistic_position(offset, .2, -.3, 80, 10, 2),
                  wot::ballistic_position(offset, .2, -.3, 80, -10, 2)),
            "position gravity sign");
    const auto path = wot::ballistic_path(offset, .2, -.3, 80, 10, 1);
    require(path.size() == 10 && close(path.front(), offset) &&
            close(path.back(), wot::ballistic_position(offset, .2, -.3, 80, 10, 1)),
            "path count and endpoints");
    require(wot::ballistic_path(offset, 0, 0, 80, 10, 1, -1).size() == 51 &&
            wot::ballistic_path(offset, 0, 0, 80, 10, 1, 1).size() == 5,
            "path step clamps");
    const auto stopped = wot::ballistic_path(offset, 0, 0, 80, 10, -1);
    require(stopped.size() == 1 && close(stopped[0], offset), "zero duration path");
}

wot::BallisticVector3 read_vector(std::istream& input) {
    wot::BallisticVector3 value{};
    input >> value.x >> value.y >> value.z;
    return value;
}

void print_vector(wot::BallisticVector3 value) {
    std::cout << '[' << value.x << ',' << value.y << ',' << value.z << ']';
}

// One whitespace-delimited request and one JSON response per line. This mode
// exercises the public functions without a JSON library or Python embedding.
int probe() {
    std::cout << std::setprecision(17);
    for (std::string line; std::getline(std::cin, line);) {
        std::istringstream input(line);
        char operation = 0;
        input >> operation;
        const auto start = read_vector(input);
        if (operation == 's' || operation == 'i') {
            const auto target = read_vector(input);
            wot::BallisticVector3 velocity{};
            if (operation == 'i') {
                velocity = read_vector(input);
            }
            double speed = 0, gravity = 0, minimum = 0, maximum = 0;
            input >> speed >> gravity >> minimum >> maximum;
            if (operation == 's') {
                const auto roots = wot::ballistic_solutions(start, target, speed,
                    gravity, minimum, maximum);
                std::cout << '[';
                for (std::size_t index = 0; index < roots.size(); ++index) {
                    if (index) std::cout << ',';
                    std::cout << '[' << roots[index].pitch << ','
                              << roots[index].flight_time << ']';
                }
                std::cout << ']';
            } else {
                int high = 0;
                double max_lead = 0;
                input >> high >> max_lead;
                const auto result = wot::ballistic_intercept(start, target,
                    velocity, speed, gravity, minimum, maximum, high != 0, max_lead);
                if (result) {
                    std::cout << '[';
                    print_vector(result->aim_point);
                    std::cout << ',' << result->pitch << ',' << result->flight_time << ']';
                } else {
                    std::cout << "null";
                }
            }
        } else if (operation == 'p' || operation == 't') {
            double yaw = 0, pitch = 0, speed = 0, gravity = 0, time = 0;
            input >> yaw >> pitch >> speed >> gravity >> time;
            if (operation == 'p') {
                print_vector(wot::ballistic_position(start, yaw, pitch, speed, gravity, time));
            } else {
                double step = 0;
                input >> step;
                const auto path = wot::ballistic_path(start, yaw, pitch, speed, gravity, time, step);
                std::cout << '[';
                for (std::size_t index = 0; index < path.size(); ++index) {
                    if (index) std::cout << ',';
                    print_vector(path[index]);
                }
                std::cout << ']';
            }
        } else {
            return 1;
        }
        if (input.fail()) return 1;
        std::cout << '\n';
    }
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--probe") return probe();
    try {
        regressions();
    } catch (const std::exception& error) {
        std::cerr << "ballistics regression failed: " << error.what() << '\n';
        return 1;
    }
    std::cout << "ballistics regressions passed\n";
    return 0;
}
