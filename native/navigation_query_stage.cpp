#include "navigation_query_stage.h"
#include <algorithm>
#include <cmath>

namespace {
double distance(const double *a, const double *b) {
    const double dx = a[0] - b[0], dz = a[2] - b[2];
    return std::sqrt(dx * dx + dz * dz);
}
int ground(const NavigationQuerySnapshot &s, const NavigationQueryEngine &e,
            double x, double z, double hint, double &height) {
    double top = hint + 8.0;
    const double bottom = hint - 18.0;
    for (unsigned layer = 0; layer < 3; ++layer) {
        const double start[] = {x, top, z}, end[] = {x, bottom, z};
        double normal = 0.0;
        if (!e.ray(e.context, start, end, true, height, normal)) return -1;
        if (height <= hint + 4.5 && normal > 0.0) {
            if (e.water(e.context, x, height, z) > s.water_limit) return 0;
            return 1;
        }
        const double next = height - 0.05;
        if (!(bottom < next && next < top)) return -1;
        top = next;
    }
    return -1;
}
bool obstacle(const NavigationQueryEngine &e, const double *start, const double *end) {
    const double dx = end[0] - start[0], dz = end[2] - start[2];
    const double length = std::sqrt(dx * dx + dz * dz);
    if (length < 0.1) return false;
    const double lx = dz / length, lz = -dx / length;
    for (double offset : {-2.15, 0.0, 2.15}) {
        for (double height : {0.9, 1.6}) {
            const double a[] = {start[0] + lx * offset, start[1] + height, start[2] + lz * offset};
            const double b[] = {end[0] + lx * offset, end[1] + height, end[2] + lz * offset};
            double ignored_height = 0.0, ignored_normal = 0.0;
            if (e.ray(e.context, a, b, false, ignored_height, ignored_normal)) return true;
        }
    }
    return false;
}
}
int navigation_query_run(const NavigationQuerySnapshot &s, const NavigationQueryEngine &e) {
    const double length = distance(s.start, s.end);
    if (length > s.cell_size * 12.0) return 0;
    const unsigned steps = std::max(1U, static_cast<unsigned>(std::ceil(length / (s.cell_size * 0.42))));
    double first[3] = {}, previous[3] = {};
    for (unsigned index = 0; index <= steps; ++index) {
        const double fraction = static_cast<double>(index) / steps;
        double point[] = {s.start[0] + (s.end[0] - s.start[0]) * fraction, 0.0,
                          s.start[2] + (s.end[2] - s.start[2]) * fraction};
        const double hint = index ? previous[1] : s.start[1];
        const int support = ground(s, e, point[0], point[2], hint, point[1]);
        if (support != 1) return support;
        if (!std::isfinite(point[1])) return -1;
        if (index && std::abs(point[1] - previous[1]) >
                     distance(point, previous) * std::min(s.grade_up, s.grade_down)) return 0;
        if (!index) std::copy(point, point + 3, first);
        std::copy(point, point + 3, previous);
    }
    return obstacle(e, first, previous) ? 0 : 1;
}
