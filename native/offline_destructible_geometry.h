#ifndef WOT_OFFLINE_DESTRUCTIBLE_GEOMETRY_H
#define WOT_OFFLINE_DESTRUCTIBLE_GEOMETRY_H
#include <array>
#include <cstdint>
#include <map>
#include <memory>
#include <set>
#include <utility>
#include <vector>

namespace offline_destructible {
using Point = std::array<double, 3>;
using Cell = std::pair<int, int>;
using Key = std::pair<std::int64_t, int>;
struct Box { Point center{}; std::vector<Point> axes; int material = -1; };
struct Pose {
    Point position{}, minimum{}, maximum{};
    double yaw = 0., pitch = 0., roll = 0., travel = 0., motion_yaw = 0.;
    bool explicit_motion_yaw = false;
};
struct Instance { Key key; int kind = 0; std::vector<Box> boxes; std::set<Cell> bins; };
struct Item {
    int index = 0; Point position{};
    bool tree = false, named = false, catalog = false;
    double radius = 0.;
};
struct Chunk {
    std::map<int, Item> items;
    std::map<Cell, std::vector<int>> origins, extended;
};
struct Candidate { Key key; int box = 0; bool contact = false; };
struct CatalogResult {
    std::vector<Candidate> candidates, grouped;
    std::uint64_t members = 0, duplicates = 0;
    bool had_members = false;
};
struct BodyResult {
    std::vector<int> items;
    std::uint64_t nearby = 0;
    bool found_nearby = false;
};
Box vehicle_box(const Pose &pose);
std::vector<Box> tree_sweep(const Point &start, double start_yaw,
    const Point &end, double end_yaw, const Point &minimum,
    const Point &maximum, double pivot);
bool intersects(const Box &left, const Box &right);
class Store {
    std::map<Key, Instance> instances_;
    std::map<Cell, std::set<Key>> contacts_, baked_;
    std::map<Key, std::set<Cell>> reverse_;
    std::map<std::int64_t, Chunk> chunks_;
public:
    void put(Instance instance);
    void remove(Key key);
    void chunk(std::int64_t id, Chunk value);
    void drop_chunk(std::int64_t id);
    void baked(std::map<Cell, std::set<Key>> value);
    CatalogResult catalog(const Box &sweep, const Box *contact) const;
    BodyResult body(std::int64_t chunk_id, const Pose &pose, double speed,
                    const Box &sweep) const;
    std::vector<int> trees(std::int64_t chunk_id, const std::vector<Box> &sweeps,
                           double radius) const;
    std::vector<Key> missing(const Box &sweep) const;
};
std::int64_t open();
void close(std::int64_t handle);
std::shared_ptr<Store> find(std::int64_t handle);
}
#endif
