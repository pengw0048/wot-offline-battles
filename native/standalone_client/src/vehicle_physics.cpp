#include "wot/vehicle_physics.hpp"

namespace wot {

VehiclePhysics::VehiclePhysics() : entity_transform_(vehicle_identity_matrix()) {
    body_.transform = vehicle_identity_matrix();
    body_.inverse_transform = vehicle_identity_matrix();
}

void VehiclePhysics::clear_damper_springs() {
    // #1513 0x00f4a82f..0x00f4a838 clears both vectors without rebuilding
    // the vehicle or evaluating a suspension law.
    damper_springs_[0].clear();
    damper_springs_[1].clear();
}

void VehiclePhysics::add_damper_spring(VehicleDamperSpring spring) {
    // #1513 0x00f49cdd..0x00f49d47: preserve the supplied record and append
    // it to the isLeft-selected vector. There is no native length clamp here.
    damper_springs_[spring.left ? 0 : 1].push_back(spring);
}

const std::vector<VehicleDamperSpring>& VehiclePhysics::damper_springs(bool left) const {
    return damper_springs_[left ? 0 : 1];
}

bool VehiclePhysics::set_damper_springs_lengths(const std::vector<float>& left,
                                               const std::vector<float>& right) {
    // #1513 0x00f4acad..0x00f4ad9a validates both counts before any writes.
    if (left.size() != damper_springs_[0].size() ||
        right.size() != damper_springs_[1].size()) return false;
    for (std::size_t index = 0; index < left.size(); ++index)
        damper_springs_[0][index].carrying_length = left[index];
    for (std::size_t index = 0; index < right.size(); ++index)
        damper_springs_[1][index].carrying_length = right[index];
    return true;
}

void VehiclePhysics::apply_impulse_to_com(Vector3 impulse) {
    apply_vehicle_impulse_to_com(body_, impulse);
}

void VehiclePhysics::apply_impulse(Vector3 point, Vector3 impulse) {
    apply_vehicle_impulse(body_, point, impulse);
}

Vector3 VehiclePhysics::point_velocity(Vector3 point) const {
    const Vector3 origin{body_.transform.rows[3][0], body_.transform.rows[3][1],
                         body_.transform.rows[3][2]};
    return wot::point_velocity(point, origin, body_.angular_velocity,
                              body_.linear_velocity);
}

void VehiclePhysics::publish_entity_transform() {
    // D3DXVec3TransformNormal(-centerOfMass, bodyPose), followed by adding
    // its three components to the copied body's translation row. Keep the
    // CoM pose separate: using it directly as the entity pose shifts every
    // model, filter provider and collision shape by the suspension offset.
    entity_transform_ = body_.transform;
    const Vector3 negative{-center_of_mass_.x, -center_of_mass_.y,
                           -center_of_mass_.z};
    const auto& matrix = body_.transform.rows;
    const Vector3 offset{
        (negative.x * matrix[0][0] + negative.y * matrix[1][0]) +
            negative.z * matrix[2][0],
        (negative.x * matrix[0][1] + negative.y * matrix[1][1]) +
            negative.z * matrix[2][1],
        (negative.x * matrix[0][2] + negative.y * matrix[1][2]) +
            negative.z * matrix[2][2]};
    entity_transform_.rows[3][0] += offset.x;
    entity_transform_.rows[3][1] += offset.y;
    entity_transform_.rows[3][2] += offset.z;
}

} // namespace wot
