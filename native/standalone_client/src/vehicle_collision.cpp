#include "wot/vehicle_collision.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <tuple>

namespace wot {
namespace {

struct DVec { double x, y, z; };
DVec convert(Vector3 v) { return {v.x, v.y, v.z}; }
Vector3 convert(DVec v) {
    return {static_cast<float>(v.x), static_cast<float>(v.y),
            static_cast<float>(v.z)};
}
DVec sub(DVec a, DVec b) { return {a.x-b.x, a.y-b.y, a.z-b.z}; }
DVec mul(DVec a, double n) { return {a.x*n, a.y*n, a.z*n}; }
DVec add(DVec a, DVec b) { return {a.x+b.x, a.y+b.y, a.z+b.z}; }
double dot(DVec a, DVec b) { return a.x*b.x + a.y*b.y + a.z*b.z; }
DVec cross(DVec a, DVec b) {
    return {a.y*b.z-a.z*b.y, a.z*b.x-a.x*b.z, a.x*b.y-a.y*b.x};
}
bool finite(DVec a) {
    return std::isfinite(a.x) && std::isfinite(a.y) && std::isfinite(a.z);
}
DVec transform(Vector3 v, const VehicleMatrix& m) {
    return {v.x*double(m.rows[0][0]) + v.y*double(m.rows[1][0]) +
                v.z*double(m.rows[2][0]) + m.rows[3][0],
            v.x*double(m.rows[0][1]) + v.y*double(m.rows[1][1]) +
                v.z*double(m.rows[2][1]) + m.rows[3][1],
            v.x*double(m.rows[0][2]) + v.y*double(m.rows[1][2]) +
                v.z*double(m.rows[2][2]) + m.rows[3][2]};
}

class Reader {
public:
    explicit Reader(const std::vector<std::uint8_t>& bytes) : bytes_(bytes) {}
    void require(std::size_t offset, std::size_t size) const {
        if (offset > bytes_.size() || size > bytes_.size() - offset)
            throw VehicleCollisionError("Truncated vehicle BSPv2 data");
    }
    std::uint16_t u16(std::size_t at) const {
        require(at, 2);
        return static_cast<std::uint16_t>(std::uint32_t(bytes_[at]) |
            (std::uint32_t(bytes_[at+1]) << 8));
    }
    std::uint32_t u32(std::size_t at) const {
        require(at, 4);
        return std::uint32_t(u16(at)) | (std::uint32_t(u16(at+2)) << 16);
    }
    float f32(std::size_t at) const {
        static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559,
                      "Vehicle BSPv2 requires IEEE754 float32");
        const auto bits = u32(at);
        float result;
        std::memcpy(&result, &bits, 4);
        if (!std::isfinite(result))
            throw VehicleCollisionError("Nonfinite vehicle BSPv2 coordinate");
        return result;
    }
    Vector3 vector(std::size_t at) const {
        return {f32(at), f32(at+4), f32(at+8)};
    }
private:
    const std::vector<std::uint8_t>& bytes_;
};

void validate(const VehicleCollisionComponent& component) {
    if (!component.mesh)
        throw VehicleCollisionError("Vehicle collision component has no mesh");
    const auto& m = component.transform;
    for (const auto& row : m.rows)
        for (float value : row)
            if (!std::isfinite(value))
                throw VehicleCollisionError("Nonfinite component transform");
    if (m.rows[0][3] != 0 || m.rows[1][3] != 0 || m.rows[2][3] != 0 ||
        m.rows[3][3] != 1)
        throw VehicleCollisionError("Component transform must be affine");
    const DVec x{m.rows[0][0], m.rows[0][1], m.rows[0][2]};
    const DVec y{m.rows[1][0], m.rows[1][1], m.rows[1][2]};
    const DVec z{m.rows[2][0], m.rows[2][1], m.rows[2][2]};
    if (dot(x, cross(y, z)) == 0)
        throw VehicleCollisionError("Singular component transform");
}

struct Candidate {
    VehicleCollisionHit hit;
    Vector3 local_raw_normal;
};

std::vector<VehicleCollisionHit> cast(
    const std::vector<VehicleCollisionComponent>& components,
    DVec start, DVec direction, double maximum) {
    std::vector<Candidate> candidates;
    for (std::size_t ci = 0; ci < components.size(); ++ci) {
        const auto& component = components[ci];
        validate(component);
        for (std::size_t ti = 0; ti < component.mesh->triangles.size(); ++ti) {
            const auto& tri = component.mesh->triangles[ti];
            for (const auto vertex : tri.vertices)
                if (!finite(convert(vertex)))
                    throw VehicleCollisionError("Nonfinite vehicle triangle vertex");
            if (tri.material_index >= component.mesh->materials.size())
                throw VehicleCollisionError("Vehicle triangle material is out of range");
            const DVec v0 = transform(tri.vertices[0], component.transform);
            const DVec e1 = sub(transform(tri.vertices[1], component.transform), v0);
            const DVec e2 = sub(transform(tri.vertices[2], component.transform), v0);
            const DVec p = cross(direction, e2);
            const double determinant = dot(e1, p);
            // #1513 WorldTriangle::intersects at 0x01172190 uses +/-1e-6.
            if (std::abs(determinant) < double(0.000001f)) continue;
            const DVec t = sub(start, v0);
            const double u = dot(t, p) / determinant;
            if (u < 0 || u > 1) continue;
            const DVec q = cross(t, e1);
            const double v = dot(direction, q) / determinant;
            if (v < 0 || u + v > 1) continue;
            const double distance = dot(e2, q) / determinant;
            if (!(distance > 0 && distance < maximum)) continue;
            const DVec raw = cross(e1, e2);
            const DVec authored = mul(raw, 1.0 / std::sqrt(dot(raw, raw)));
            double cosine = -dot(direction, authored);
            DVec normal = authored;
            // WGBspCollisionModel::collideSegment 0x00f4514a..0x00f451de
            // flips both returned normal and negative incidence, then clamps.
            if (cosine < 0) {
                cosine = -cosine;
                normal = mul(normal, -1);
            }
            VehicleCollisionHit hit;
            hit.distance = distance;
            hit.fraction = distance / maximum;
            hit.point = convert(add(start, mul(direction, distance)));
            hit.authored_normal = convert(authored);
            hit.normal = convert(normal);
            hit.hit_angle_cos = std::max(-1.0, std::min(1.0, cosine));
            hit.component_index = ci;
            hit.triangle_index = ti;
            hit.material_index = tri.material_index;
            const auto& a = tri.vertices[0];
            const auto& b = tri.vertices[1];
            const auto& c = tri.vertices[2];
            // Keep the native unnormalized float local normal as the seam key.
            const Vector3 e{c.x-a.x, c.y-a.y, c.z-a.z};
            const Vector3 f{c.x-b.x, c.y-b.y, c.z-b.z};
            const Vector3 local{e.y*f.z-e.z*f.y, e.z*f.x-e.x*f.z,
                                e.x*f.y-e.y*f.x};
            if (!finite(convert(local)) || !finite(convert(hit.point)))
                throw VehicleCollisionError("Invalid vehicle collision arithmetic");
            candidates.push_back({hit, local});
        }
    }
    std::sort(candidates.begin(), candidates.end(), [](const Candidate& a,
                                                      const Candidate& b) {
        return std::tie(a.hit.distance, a.hit.component_index, a.hit.triangle_index) <
               std::tie(b.hit.distance, b.hit.component_index, b.hit.triangle_index);
    });
    std::vector<Candidate> retained;
    for (const auto& candidate : candidates) {
        bool duplicate = false;
        for (auto prev = retained.rbegin(); prev != retained.rend(); ++prev) {
            // Native set comparator at 0x00f43e30 uses float(0.0004),
            // raw local normal equality and material-kind identity.
            if (candidate.hit.distance - prev->hit.distance >= double(0.0004f))
                break;
            const auto& a = candidate.local_raw_normal;
            const auto& b = prev->local_raw_normal;
            if (candidate.hit.component_index == prev->hit.component_index &&
                candidate.hit.material_index == prev->hit.material_index &&
                a.x == b.x && a.y == b.y && a.z == b.z) {
                duplicate = true;
                break;
            }
        }
        if (!duplicate) retained.push_back(candidate);
    }
    std::vector<VehicleCollisionHit> result;
    result.reserve(retained.size());
    for (const auto& candidate : retained) result.push_back(candidate.hit);
    return result;
}
} // namespace

VehicleCollisionMesh parse_vehicle_collision_bsp2(
    const std::vector<std::uint8_t>& bytes,
    const std::vector<std::string>& materials) {
    const Reader read(bytes);
    read.require(0, 60);
    if (read.u32(0) != 0x02505342U || read.u32(4) != 16 ||
        read.u32(8) != 40 || read.u32(12) != 40)
        throw VehicleCollisionError("Unsupported vehicle BSPv2 header or ABI");
    const auto count = read.u32(16);
    const auto nodes = read.u32(20);
    const auto shared = read.u32(24);
    const std::uint64_t size = 60ULL + std::uint64_t(count)*40 +
        std::uint64_t(shared)*4 + std::uint64_t(nodes)*40;
    if (!nodes || size > bytes.size())
        throw VehicleCollisionError("Invalid vehicle BSPv2 array sizes");
    VehicleCollisionMesh result;
    result.materials = materials;
    result.bounds_min = read.vector(28);
    result.bounds_max = read.vector(40);
    if (result.bounds_min.x > result.bounds_max.x ||
        result.bounds_min.y > result.bounds_max.y ||
        result.bounds_min.z > result.bounds_max.z)
        throw VehicleCollisionError("Invalid vehicle BSPv2 bounds");
    result.triangles.reserve(count);
    for (std::size_t index = 0; index < count; ++index) {
        const std::size_t at = 60 + index*40;
        VehicleCollisionTriangle triangle;
        for (std::size_t v = 0; v < 3; ++v)
            triangle.vertices[v] = read.vector(at + v*12);
        triangle.material_index = read.u16(at+36);
        triangle.padding = read.u16(at+38);
        if (triangle.material_index >= materials.size())
            throw VehicleCollisionError("Vehicle BSPv2 material index is out of range");
        result.triangles.push_back(triangle);
    }
    std::size_t cursor = static_cast<std::size_t>(size);
    while (cursor < bytes.size()) {
        read.require(cursor, 8);
        const auto length = read.u32(cursor+4);
        cursor += 8;
        read.require(cursor, length);
        cursor += length;
    }
    return result;
}

std::vector<VehicleCollisionHit> collide_vehicle_segment(
    const std::vector<VehicleCollisionComponent>& components,
    Vector3 start, Vector3 end) {
    if (!finite(convert(start)) || !finite(convert(end)))
        throw VehicleCollisionError("Nonfinite vehicle collision segment");
    const DVec direction = sub(convert(end), convert(start));
    const double length = std::sqrt(dot(direction, direction));
    if (length == 0) return {};
    return cast(components, convert(start), mul(direction, 1.0/length), length);
}

std::vector<VehicleCollisionHit> collide_vehicle_ray(
    const std::vector<VehicleCollisionComponent>& components,
    Vector3 origin, Vector3 direction, double maximum_distance) {
    if (!finite(convert(origin)) || !finite(convert(direction)) ||
        !std::isfinite(maximum_distance) || maximum_distance < 0)
        throw VehicleCollisionError("Invalid vehicle collision ray");
    const double length = std::sqrt(dot(convert(direction), convert(direction)));
    if (length == 0 || maximum_distance == 0) return {};
    return cast(components, convert(origin), mul(convert(direction), 1.0/length),
                maximum_distance);
}
} // namespace wot
