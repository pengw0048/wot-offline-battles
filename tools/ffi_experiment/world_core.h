#ifndef OFFLINE_WORLD_CORE_H
#define OFFLINE_WORLD_CORE_H
namespace offline_world {
struct Point {
    double x, y, z;
    Point(double a = 0, double b = 0, double c = 0) : x(a), y(b), z(c) {}
};
struct Input {
    Point pos;
    double yaw, speed, hw, back, front, dt, motion, pitch, roll;
    bool airborne, has_motion;
};
// Compose the reviewed sweep on the current borrowed callback stack.
int sweep(const Input &input, bool batch_independent = false);
} // namespace offline_world
int offline_world_dispatch(double *buffer, int count);
void offline_world_reset();
#endif
