#include "wot/point_velocity.hpp"

#include <iostream>

int main() {
    const auto check = [](wot::Vector3 actual, wot::Vector3 expected) {
        return actual.x == expected.x && actual.y == expected.y &&
               actual.z == expected.z;
    };
    // Translation, pure rotation around each axis, and an offset origin.
    if (!check(wot::point_velocity({3, 4, 5}, {3, 4, 5}, {2, -3, 4},
                                  {7, -8, 9}), {7, -8, 9}) ||
        !check(wot::point_velocity({0, 2, 0}, {0, 0, 0}, {3, 0, 0},
                                  {0, 0, 0}), {0, 0, 6}) ||
        !check(wot::point_velocity({0, 0, 2}, {0, 0, 0}, {0, 3, 0},
                                  {0, 0, 0}), {6, 0, 0}) ||
        !check(wot::point_velocity({2, 0, 0}, {0, 0, 0}, {0, 0, -3},
                                  {0, 0, 0}), {0, -6, 0}) ||
        !check(wot::point_velocity({11, -8, 23}, {10, -10, 20}, {4, 5, 6},
                                  {7, 8, 9}), {10, 2, 12})) {
        std::cerr << "point velocity regression failed\n";
        return 1;
    }
    std::cout << "point velocity regressions passed\n";
    return 0;
}
