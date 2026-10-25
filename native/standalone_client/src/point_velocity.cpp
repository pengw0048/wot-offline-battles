#include "wot/point_velocity.hpp"

#include <limits>

namespace wot {

static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559,
              "The recovered kernel requires IEEE 754 binary32");

Vector3 point_velocity(Vector3 point, Vector3 origin,
                       Vector3 angular_velocity, Vector3 linear_velocity) {
    // #1513 VA 0x00f4a481..0x00f4a532: scalar SUBSS/MULSS/ADDSS.
    // Preserve the rounding after each operation; compile without FMA
    // contraction or fast-math. Do not replace this with a double-precision
    // cross product or reassociate the final additions.
    const float dx = point.x - origin.x;
    const float dy = point.y - origin.y;
    const float dz = point.z - origin.z;
    const float x_first = angular_velocity.y * dz;
    const float x_second = angular_velocity.z * dy;
    const float y_first = angular_velocity.z * dx;
    const float y_second = angular_velocity.x * dz;
    const float z_first = angular_velocity.x * dy;
    const float z_second = angular_velocity.y * dx;
    return {linear_velocity.x + (x_first - x_second),
            linear_velocity.y + (y_first - y_second),
            linear_velocity.z + (z_first - z_second)};
}

} // namespace wot
