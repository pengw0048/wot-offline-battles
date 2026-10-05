#ifndef WOT_OFFLINE_MATH_BATCH_H
#define WOT_OFFLINE_MATH_BATCH_H
#include <stdint.h>
#include <array>
#include <vector>

namespace offline_math {
struct Vec { double x, z; };
struct Body {
    int64_t id;
    bool has_y;
    double x, y, z, yaw, pitch, roll;
    std::array<double, 4> shape;
};

// Values are copied from the caller's live Python objects once per operation.
// Input roster order is meaningful. No engine or Python pointer is retained.
double translation(const Body &owner, Vec movement, const std::vector<Body> &peers);
Vec slide(Body owner, Vec movement, const std::vector<Body> &peers,
          bool has_first_fraction, double first_fraction);
double rotation(const Body &owner, double candidate, double pivot, Vec movement,
                const std::vector<Body> &peers);
} // namespace offline_math
#endif
