#include "wot/vehicle_dynamics.hpp"

#include <cmath>

namespace wot {
namespace {

Vector3 cross(Vector3 a, Vector3 b) {
    return {a.y * b.z - a.z * b.y,
            a.z * b.x - a.x * b.z,
            a.x * b.y - a.y * b.x};
}

float squared_length(Vector3 v) {
    return (v.x * v.x + v.y * v.y) + v.z * v.z;
}

Vector3 normalize(Vector3 v) {
    const float length = std::sqrt(squared_length(v));
    if (length == 0) return {};
    return {v.x / length, v.y / length, v.z / length};
}

Vector3 row_vector(const VehicleMatrix& matrix, int row) {
    return {matrix.rows[row][0], matrix.rows[row][1], matrix.rows[row][2]};
}

void set_row(VehicleMatrix& matrix, int row, Vector3 value) {
    matrix.rows[row][0] = value.x;
    matrix.rows[row][1] = value.y;
    matrix.rows[row][2] = value.z;
}

Vector3 transform_normal(Vector3 vector, const VehicleMatrix& matrix) {
    return {(vector.x * matrix.rows[0][0] + vector.y * matrix.rows[1][0]) +
                vector.z * matrix.rows[2][0],
            (vector.x * matrix.rows[0][1] + vector.y * matrix.rows[1][1]) +
                vector.z * matrix.rows[2][1],
            (vector.x * matrix.rows[0][2] + vector.y * matrix.rows[1][2]) +
                vector.z * matrix.rows[2][2]};
}

// #1513 0x00f705c0 writes only rows 0..2 and preserves the destination's
// translation. Copy inputs because the original routine supports aliasing.
void multiply_rotation_rows(VehicleMatrix& destination,
                            const VehicleMatrix& left,
                            const VehicleMatrix& right) {
    const VehicleMatrix a = left;
    const VehicleMatrix b = right;
    for (int row = 0; row != 3; ++row) {
        for (int column = 0; column != 4; ++column) {
            const float first = a.rows[row][1] * b.rows[1][column];
            const float second = a.rows[row][0] * b.rows[0][column];
            const float third = a.rows[row][2] * b.rows[2][column];
            destination.rows[row][column] = (first + second) + third;
        }
    }
}

VehicleMatrix inverse_orthonormal(const VehicleMatrix& matrix) {
    // #1513 0x011bb940: transpose rotation and negate translated dot products.
    VehicleMatrix inverse{};
    for (int row = 0; row != 3; ++row) {
        for (int column = 0; column != 3; ++column)
            inverse.rows[row][column] = matrix.rows[column][row];
    }
    const Vector3 t = row_vector(matrix, 3);
    inverse.rows[3][0] = -((t.x * inverse.rows[0][0] +
                           t.y * inverse.rows[1][0]) +
                          inverse.rows[2][0] * t.z);
    inverse.rows[3][1] = -((t.y * inverse.rows[1][1] +
                           t.x * inverse.rows[0][1]) +
                          inverse.rows[2][1] * t.z);
    inverse.rows[3][2] = -((inverse.rows[1][2] * t.y +
                           t.x * inverse.rows[0][2]) +
                          inverse.rows[2][2] * t.z);
    inverse.rows[3][3] = 1;
    return inverse;
}

VehicleMatrix rotation_axis(Vector3 axis, float angle) {
    // The native step calls D3DXQuaternionRotationAxis, then
    // D3DXMatrixRotationQuaternion, rather than an Euler-angle update.
    axis = normalize(axis);
    const float half_angle = angle * 0.5f;
    const float sine = std::sin(half_angle);
    const float x = axis.x * sine;
    const float y = axis.y * sine;
    const float z = axis.z * sine;
    const float w = std::cos(half_angle);
    const float xx = x * x;
    const float yy = y * y;
    const float zz = z * z;
    const float xy = x * y;
    const float xz = x * z;
    const float yz = y * z;
    const float xw = x * w;
    const float yw = y * w;
    const float zw = z * w;
    VehicleMatrix result = vehicle_identity_matrix();
    result.rows[0][0] = 1.0f - 2.0f * (yy + zz);
    result.rows[0][1] = 2.0f * (xy + zw);
    result.rows[0][2] = 2.0f * (xz - yw);
    result.rows[1][0] = 2.0f * (xy - zw);
    result.rows[1][1] = 1.0f - 2.0f * (xx + zz);
    result.rows[1][2] = 2.0f * (yz + xw);
    result.rows[2][0] = 2.0f * (xz + yw);
    result.rows[2][1] = 2.0f * (yz - xw);
    result.rows[2][2] = 1.0f - 2.0f * (xx + yy);
    return result;
}

void record_snapshot(VehicleRigidBody& body) {
    if (!body.record_history) return;
    body.history[body.history_next] = {
        body.transform, body.linear_velocity, body.angular_velocity};
    body.history_next = (body.history_next + 1) % body.history.size();
}

} // namespace

VehicleMatrix vehicle_identity_matrix() {
    VehicleMatrix result{};
    for (int i = 0; i != 4; ++i) result.rows[i][i] = 1;
    return result;
}

void clear_vehicle_loads(VehicleRigidBody& body) {
    body.loads = {};
}

void apply_vehicle_impulse_to_com(VehicleRigidBody& body, Vector3 impulse) {
    // #1513 0x00f4a021..0x00f4a082.
    body.linear_velocity.x += body.inverse_mass * impulse.x;
    body.linear_velocity.y += body.inverse_mass * impulse.y;
    body.linear_velocity.z += body.inverse_mass * impulse.z;
}

void apply_vehicle_impulse(VehicleRigidBody& body, Vector3 point,
                           Vector3 impulse) {
    // #1513 0x00f49e52..0x00f49f73. The torque arm uses the current CoM.
    apply_vehicle_impulse_to_com(body, impulse);
    const Vector3 com = row_vector(body.transform, 3);
    const Vector3 arm{point.x - com.x, point.y - com.y, point.z - com.z};
    const Vector3 delta = transform_normal(cross(arm, impulse),
                                           body.inverse_world_inertia);
    body.angular_velocity.x += delta.x;
    body.angular_velocity.y += delta.y;
    body.angular_velocity.z += delta.z;
}

bool step_vehicle_rigid_body(VehicleRigidBody& body, float elapsed) {
    // #1513 0x00f70010..0x00f70377. Preserve separate force, acceleration,
    // and pose-only correction terms; folding them changes both semantics
    // and binary32 rounding.
    record_snapshot(body);
    body.last_step = elapsed;
    const float force_scale = body.inverse_mass * elapsed;
    body.linear_velocity.x = (body.linear_velocity.x +
                              body.loads.force.x * force_scale) +
                             body.loads.linear_acceleration.x * elapsed;
    body.linear_velocity.y = (body.linear_velocity.y +
                              body.loads.force.y * force_scale) +
                             body.loads.linear_acceleration.y * elapsed;
    body.linear_velocity.z = (body.linear_velocity.z +
                              body.loads.force.z * force_scale) +
                             body.loads.linear_acceleration.z * elapsed;
    const Vector3 angular = transform_normal(body.loads.torque,
                                             body.inverse_world_inertia);
    body.angular_velocity.x = (angular.x * elapsed + body.angular_velocity.x) +
                              body.loads.angular_acceleration.x * elapsed;
    body.angular_velocity.y = (body.angular_velocity.y + angular.y * elapsed) +
                              body.loads.angular_acceleration.y * elapsed;
    body.angular_velocity.z = (body.angular_velocity.z + angular.z * elapsed) +
                              body.loads.angular_acceleration.z * elapsed;
    body.motion_linear_velocity = {
        body.linear_motion_acceleration.x * elapsed + body.linear_velocity.x,
        body.linear_velocity.y + body.linear_motion_acceleration.y * elapsed,
        body.linear_velocity.z + body.linear_motion_acceleration.z * elapsed};
    body.motion_angular_velocity = {
        body.angular_velocity.x + body.angular_motion_acceleration.x * elapsed,
        body.angular_velocity.y + body.angular_motion_acceleration.y * elapsed,
        body.angular_velocity.z + body.angular_motion_acceleration.z * elapsed};
    const float angular_speed = std::sqrt(squared_length(
        body.motion_angular_velocity));
    const float linear_speed_squared = squared_length(body.motion_linear_velocity);

    // Exact strict COMISS/SETA bounds: equality and unordered values fail.
    // These are native numerical-failure limits, not gameplay speed limits.
    if (!(linear_speed_squared < 1.0e10f && angular_speed < 10000.0f)) {
        body.angular_velocity = {};
        body.linear_velocity = {};
        clear_vehicle_loads(body);
        body.linear_motion_acceleration = {};
        body.angular_motion_acceleration = {};
        record_snapshot(body);
        return false;
    }

    const Vector3 old_position = row_vector(body.transform, 3);
    const Vector3 position{
        elapsed * body.motion_linear_velocity.x + old_position.x,
        old_position.y + body.motion_linear_velocity.y * elapsed,
        old_position.z + body.motion_linear_velocity.z * elapsed};
    if (angular_speed > 1.0e-6f) {
        const float reciprocal_speed = 1.0f / angular_speed;
        const Vector3 axis{
            reciprocal_speed * body.motion_angular_velocity.x,
            body.motion_angular_velocity.y * reciprocal_speed,
            body.motion_angular_velocity.z * reciprocal_speed};
        const VehicleMatrix rotation = rotation_axis(axis, elapsed * angular_speed);
        set_row(body.transform, 3, {});
        body.transform.rows[3][3] = 1;
        multiply_rotation_rows(body.transform, body.transform, rotation);
    }
    set_row(body.transform, 3, position);
    body.transform.rows[3][3] = 1;

    // #1513 0x00f70475..0x00f70564: reset to 200, then decrement on the
    // following calls, so orthonormalization occurs every 201 successful steps.
    if (body.orthonormalization_countdown == 0) {
        body.orthonormalization_countdown = 200;
        const Vector3 forward = normalize(row_vector(body.transform, 2));
        const Vector3 right = normalize(cross(row_vector(body.transform, 1), forward));
        set_row(body.transform, 2, forward);
        set_row(body.transform, 0, right);
        set_row(body.transform, 1, cross(forward, right));
    } else {
        --body.orthonormalization_countdown;
    }
    body.linear_motion_acceleration = {};
    body.angular_motion_acceleration = {};

    // #1513 0x00f6ffc7..0x00f6fff2: world inverse inertia = R^-1 I^-1 R.
    body.inverse_transform = inverse_orthonormal(body.transform);
    multiply_rotation_rows(body.inverse_world_inertia, body.inverse_transform,
                            body.inverse_body_inertia);
    multiply_rotation_rows(body.inverse_world_inertia, body.inverse_world_inertia,
                            body.transform);
    return true;
}

} // namespace wot
