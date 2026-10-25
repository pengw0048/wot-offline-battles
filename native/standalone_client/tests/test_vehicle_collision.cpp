#include "wot/vehicle_collision.hpp"

#include <cmath>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>

namespace {
using Bytes = std::vector<std::uint8_t>;

void check(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
bool close(double a, double b, double tolerance = 1e-6) {
    return std::abs(a-b) < tolerance;
}
bool close(wot::Vector3 a, wot::Vector3 b) {
    return close(a.x,b.x) && close(a.y,b.y) && close(a.z,b.z);
}
void put32(Bytes& bytes, std::size_t at, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        bytes[at+i] = static_cast<std::uint8_t>(value >> (i*8));
}
void put16(Bytes& bytes, std::size_t at, std::uint16_t value) {
    bytes[at] = static_cast<std::uint8_t>(value);
    bytes[at+1] = static_cast<std::uint8_t>(value >> 8);
}
void putFloat(Bytes& bytes, std::size_t at, float value) {
    std::uint32_t bits;
    std::memcpy(&bits, &value, 4);
    put32(bytes, at, bits);
}
std::uint16_t get16(const Bytes& bytes, std::size_t at) {
    return static_cast<std::uint16_t>(unsigned(bytes.at(at)) |
        (unsigned(bytes.at(at+1)) << 8));
}
float getFloat(const Bytes& bytes, std::size_t at) {
    std::uint32_t bits = 0;
    for (unsigned i = 0; i < 4; ++i)
        bits |= std::uint32_t(bytes.at(at+i)) << (i*8);
    float result;
    std::memcpy(&result, &bits, 4);
    return result;
}
wot::VehicleCollisionTriangle triangle(float z = 0, std::uint16_t material = 0) {
    return {{{-1,-1,z}, {1,-1,z}, {0,1,z}}, material, 0};
}
Bytes encode(const std::vector<wot::VehicleCollisionTriangle>& triangles) {
    Bytes bytes(60 + triangles.size()*40 + 40);
    put32(bytes, 0, 0x02505342);
    put32(bytes, 4, 16); put32(bytes, 8, 40); put32(bytes, 12, 40);
    put32(bytes, 16, static_cast<std::uint32_t>(triangles.size()));
    put32(bytes, 20, 1);
    for (std::size_t axis = 0; axis < 3; ++axis) {
        putFloat(bytes, 28+axis*4, -10);
        putFloat(bytes, 40+axis*4, 10);
    }
    for (std::size_t index = 0; index < triangles.size(); ++index) {
        const std::size_t at = 60 + index*40;
        for (std::size_t v = 0; v < 3; ++v) {
            putFloat(bytes, at+v*12, triangles[index].vertices[v].x);
            putFloat(bytes, at+v*12+4, triangles[index].vertices[v].y);
            putFloat(bytes, at+v*12+8, triangles[index].vertices[v].z);
        }
        put16(bytes, at+36, triangles[index].material_index);
        put16(bytes, at+38, triangles[index].padding);
    }
    return bytes;
}
void rejected(const Bytes& bytes, const std::vector<std::string>& materials) {
    try {
        wot::parse_vehicle_collision_bsp2(bytes, materials);
    } catch (const wot::VehicleCollisionError&) { return; }
    throw std::runtime_error("Malformed BSPv2 unexpectedly accepted");
}
void reader_checks() {
    auto tri = triangle();
    tri.padding = 0xbeef;
    auto bytes = encode({tri});
    auto mesh = wot::parse_vehicle_collision_bsp2(bytes, {"armor_1"});
    check(mesh.triangles.size() == 1 && mesh.materials[0] == "armor_1" &&
        mesh.triangles[0].padding == 0xbeef, "BSPv2 triangle/material/padding");
    check(close(mesh.triangles[0].vertices[2], {0,1,0}), "BSPv2 coordinates");
    check(close(mesh.bounds_min, {-10,-10,-10}), "BSPv2 bounds");
    auto changed = bytes; changed[0] = 0; rejected(changed, {"armor_1"});
    changed = bytes; put32(changed, 8, 36); rejected(changed, {"armor_1"});
    changed = bytes; changed.pop_back(); rejected(changed, {"armor_1"});
    changed = bytes; put32(changed, 16, 0xffffffffU); rejected(changed, {"armor_1"});
    changed = bytes; put32(changed, 20, 0); rejected(changed, {"armor_1"});
    changed = bytes; put16(changed, 96, 1); rejected(changed, {"armor_1"});
    changed = bytes; putFloat(changed, 60, std::numeric_limits<float>::infinity());
    rejected(changed, {"armor_1"});
    changed = bytes; putFloat(changed, 28, 11); rejected(changed, {"armor_1"});
    // Native user data is key/uint32 length/payload, following node arrays.
    changed = bytes; changed.resize(bytes.size()+11);
    put32(changed, bytes.size(), 7); put32(changed, bytes.size()+4, 3);
    check(wot::parse_vehicle_collision_bsp2(changed, {"armor_1"}).triangles.size() == 1,
          "Native BSP user-data record");
    changed.pop_back(); rejected(changed, {"armor_1"});
}

void hits_and_current_pose() {
    auto mesh = wot::parse_vehicle_collision_bsp2(encode({triangle()}), {"armor_1"});
    wot::VehicleCollisionComponent component{"hull", &mesh, wot::vehicle_identity_matrix()};
    auto hit = wot::collide_vehicle_segment({component}, {0,0,-3}, {0,0,3});
    check(hit.size() == 1 && close(hit[0].distance, 3) && close(hit[0].fraction, 0.5),
          "Forward hit distance");
    check(close(hit[0].authored_normal, {0,0,1}) && close(hit[0].normal, {0,0,-1}) &&
          close(hit[0].hit_angle_cos, 1), "Native-facing normal and incidence");
    hit = wot::collide_vehicle_ray({component}, {0,0,3}, {0,0,-12}, 6);
    check(hit.size() == 1 && close(hit[0].distance, 3) &&
          close(hit[0].normal, {0,0,1}) && close(hit[0].hit_angle_cos, 1),
          "Two-sided reverse hit");
    hit = wot::collide_vehicle_segment({component}, {-1,0,-1}, {1,0,1});
    check(hit.size() == 1 && close(hit[0].hit_angle_cos, std::sqrt(0.5)),
          "Oblique incidence");
    auto& m = component.transform;
    m.rows[0][0] = 0; m.rows[0][2] = -1;
    m.rows[2][0] = 1; m.rows[2][2] = 0;
    m.rows[3][0] = 10; m.rows[3][1] = 4; m.rows[3][2] = 20;
    hit = wot::collide_vehicle_segment({component}, {7,4,20}, {13,4,20});
    check(hit.size() == 1 && close(hit[0].point, {10,4,20}) &&
          close(hit[0].authored_normal, {1,0,0}) && close(hit[0].normal, {-1,0,0}),
          "Current component orientation and translation");
    check(wot::collide_vehicle_segment({component}, {0,0,-3}, {0,0,3}).empty(),
          "Spawn-pose geometry must not remain collidable");
    m.rows[3][0] = 12;
    hit = wot::collide_vehicle_segment({component}, {7,4,20}, {13,4,20});
    check(hit.size() == 1 && close(hit[0].distance, 5), "Updated component pose");
}

void seams_and_material_identity() {
    auto a = triangle();
    auto reverse = a;
    std::swap(reverse.vertices[1], reverse.vertices[2]);
    auto other_material = a; other_material.material_index = 1;
    auto different_area = a; different_area.vertices[2].y = 2;
    const auto mesh = wot::parse_vehicle_collision_bsp2(
        encode({a, a, reverse, other_material, different_area,
                triangle(0.0002f), triangle(0.001f)}), {"armor_a", "armor_b"});
    wot::VehicleCollisionComponent component{"hull", &mesh, wot::vehicle_identity_matrix()};
    const auto hits = wot::collide_vehicle_segment({component}, {0,0,-2}, {0,0,2});
    check(hits.size() == 5, "Native seam dedup must retain different raw normals/materials");
    check(hits[0].triangle_index == 0 && hits[1].triangle_index == 2 &&
          hits[2].material_index == 1 && hits[3].triangle_index == 4 &&
          hits[4].triangle_index == 6, "Deterministic seam representative and ordering");
    const auto separate = wot::collide_vehicle_segment({component, component},
        {0,0,-2}, {0,0,2});
    check(separate.size() == 10, "Component contacts must not merge");
}

void misses_and_invalid_input() {
    auto degenerate = triangle();
    degenerate.vertices[2] = degenerate.vertices[1];
    const auto mesh = wot::parse_vehicle_collision_bsp2(encode({triangle(), degenerate}), {"armor"});
    wot::VehicleCollisionComponent component{"gun", &mesh, wot::vehicle_identity_matrix()};
    check(wot::collide_vehicle_segment({component}, {2,0,-1}, {2,0,1}).empty(), "Outside triangle");
    check(wot::collide_vehicle_segment({component}, {-1,0,0}, {1,0,0}).empty(), "Coplanar segment");
    check(wot::collide_vehicle_segment({component}, {0,0,0}, {0,0,1}).empty(), "Exclusive starting face");
    check(wot::collide_vehicle_segment({component}, {0,0,-1}, {0,0,0}).empty(), "Exclusive ending face");
    check(wot::collide_vehicle_segment({component}, {0,0,0}, {0,0,0}).empty(), "Zero segment");
    component.transform.rows[0][0] = 0;
    try {
        wot::collide_vehicle_segment({component}, {0,0,-1}, {0,0,1});
    } catch (const wot::VehicleCollisionError&) { return; }
    throw std::runtime_error("Singular component transform accepted");
}

void original_resource(const char* path, const char* expected, const char* material_path) {
    std::ifstream input(path, std::ios::binary);
    check(bool(input), "Cannot open original BSPv2 section");
    const Bytes bytes{std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
    std::ifstream material_input(material_path);
    check(bool(material_input), "Cannot open original material-name list");
    std::vector<std::string> materials;
    for (std::string name; std::getline(material_input, name); ) materials.push_back(name);
    const auto mesh = wot::parse_vehicle_collision_bsp2(bytes, materials);
    check(mesh.triangles.size() == std::stoul(expected), "Original triangle count mismatch");
    for (std::size_t index = 0; index < mesh.triangles.size(); ++index) {
        const auto& triangle = mesh.triangles[index];
        const auto at = 60 + index*40;
        check(triangle.material_index == get16(bytes, at+36) &&
              triangle.padding == get16(bytes, at+38), "Original material/padding mismatch");
        for (std::size_t v = 0; v < 3; ++v)
            check(triangle.vertices[v].x == getFloat(bytes, at+v*12) &&
                  triangle.vertices[v].y == getFloat(bytes, at+v*12+4) &&
                  triangle.vertices[v].z == getFloat(bytes, at+v*12+8),
                  "Original triangle-coordinate mismatch");
        // Every nondegenerate original triangle is usable under a moved,
        // rotated component pose. Its centroid-directed ray must find that
        // exact material or an equivalent same-material seam representative.
        const auto a = triangle.vertices[0], b = triangle.vertices[1], c = triangle.vertices[2];
        const wot::Vector3 e{b.x-a.x,b.y-a.y,b.z-a.z};
        const wot::Vector3 f{c.x-a.x,c.y-a.y,c.z-a.z};
        wot::Vector3 n{e.y*f.z-e.z*f.y,e.z*f.x-e.x*f.z,e.x*f.y-e.y*f.x};
        const double len = std::sqrt(double(n.x)*n.x+double(n.y)*n.y+double(n.z)*n.z);
        if (len <= 1e-5) continue;
        n = {float(n.x/len),float(n.y/len),float(n.z/len)};
        const wot::Vector3 mid{(a.x+b.x+c.x)/3,(a.y+b.y+c.y)/3,(a.z+b.z+c.z)/3};
        wot::VehicleCollisionComponent component{"original", &mesh, wot::vehicle_identity_matrix()};
        auto& m = component.transform;
        m.rows[0][0]=0; m.rows[0][2]=-1; m.rows[2][0]=1; m.rows[2][2]=0;
        m.rows[3][0]=100; m.rows[3][1]=20; m.rows[3][2]=-50;
        const wot::Vector3 centre{mid.z+100,mid.y+20,-mid.x-50};
        const wot::Vector3 normal{n.z,n.y,-n.x};
        const auto hits = wot::collide_vehicle_segment({component},
            {centre.x-normal.x,centre.y-normal.y,centre.z-normal.z},
            {centre.x+normal.x,centre.y+normal.y,centre.z+normal.z});
        bool found = false;
        for (const auto& hit : hits)
            if (hit.material_index == triangle.material_index &&
                close(hit.distance, 1, 0.0001)) found = true;
        check(found, "Original transformed triangle not hittable with its material");
    }
    std::cout << "Original BSPv2 verified: " << path << ", triangles=" << mesh.triangles.size()
              << ", materials=" << mesh.materials.size() << '\n';
}
} // namespace

int main(int argc, char** argv) {
    try {
        reader_checks(); hits_and_current_pose(); seams_and_material_identity();
        misses_and_invalid_input();
        if (argc == 4) original_resource(argv[1], argv[2], argv[3]);
        else check(argc == 1, "Usage: test_vehicle_collision [bsp2 expected_triangles materials.txt]");
        std::cout << "Vehicle collision checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
