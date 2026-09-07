/* Resumable baked A* experiment. Preserve navigation.py's expansion order,
 * floating-point addition order, partial-path rules and fair scheduling.
 * Geometry probing and path finalization remain with the Python owner. */
#include "astar_core.h"
#include "combat_core.h"
#include "driver_core.h"
#include "perception_core.h"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <deque>
#include <limits>
#include <memory>
#include <queue>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {
struct Invalid : std::runtime_error { Invalid() : std::runtime_error("invalid command") {} };
struct Reader {
    double *b; int n; int p;
    double number() {
        if (p >= n || !std::isfinite(b[p])) throw Invalid();
        return b[p++];
    }
    int integer(int minimum = 0, int maximum = 20000000) {
        double v = number();
        if (v < minimum || v > maximum || v != std::floor(v)) throw Invalid();
        return static_cast<int>(v);
    }
    void end() { if (p != n) throw Invalid(); }
};
using Edge = uint64_t;
Edge edge_key(int a, int b) {
    if (a > b) std::swap(a, b);
    return (static_cast<uint64_t>(a) << 32) | static_cast<uint32_t>(b);
}
struct Graph {
    int width, height;
    double cell, ox, oz, grade, heuristic, clearance, shallow, diagonal;
    std::vector<double> heights;
    std::vector<unsigned char> links, hazards;
    std::unordered_map<Edge, std::pair<double, double> > failed;
    std::unordered_map<Edge, double> hulls;
    std::vector<Edge> expired;
    bool valid(int i) const {
        // The graph's links encode traversability. Match _baked_index exactly;
        // do not invent a second hazard admission rule in the native adapter.
        return i >= 0 && i < width * height && std::isfinite(heights[i]);
    }
    double distance(int a, int b) const {
        double dx = a % width - b % width, dz = a / width - b / width;
        return std::sqrt(dx * dx + dz * dz);
    }
};
struct Entry { double priority; uint64_t sequence; int cell; double cost; };
struct Later {
    bool operator()(const Entry &a, const Entry &b) const {
        if (a.priority != b.priority) return a.priority > b.priority;
        return a.sequence > b.sequence;
    }
};
struct Search {
    std::shared_ptr<Graph> graph;
    int start, goal, maximum, expansions = 0, closest;
    double now, closest_distance;
    bool prefer, done = false, hard_present = false;
    uint64_t sequence = 0;
    std::priority_queue<Entry, std::vector<Entry>, Later> frontier;
    std::unordered_map<int, double> costs;
    std::unordered_map<int, int> parents;
    std::unordered_map<Edge, double> local;
    std::unordered_set<Edge> hard;
    std::vector<std::pair<double, double> > avoid;
    std::vector<int> path;

    void finish(int reached = -1) {
        done = true;
        if (reached < 0) {
            if (hard_present && closest == start) return;
            if (closest_distance <= 3.0 || (!frontier.empty() && closest != start))
                reached = closest;
            else return;
        }
        path.push_back(reached);
        while (path.back() != start) {
            auto parent = parents.find(path.back());
            if (parent == parents.end() || path.size() > costs.size()) throw Invalid();
            path.push_back(parent->second);
        }
        std::reverse(path.begin(), path.end());
    }
    void step() {
        if (done) return;
        Graph &g = *graph;
        if (start < 0 || goal < 0) { done = true; return; }
        if (expansions >= maximum) { finish(); return; }
        Entry current;
        bool found = false;
        while (!frontier.empty()) {
            current = frontier.top(); frontier.pop();
            if (current.cost == costs.at(current.cell)) { found = true; break; }
        }
        if (!found) { finish(); return; }
        ++expansions;
        double distance = g.distance(current.cell, goal);
        if (distance < closest_distance) {
            closest = current.cell; closest_distance = distance;
        }
        if (current.cell == goal) { finish(current.cell); return; }
        static const int dxs[] = {-1, 0, 1, -1, 1, -1, 0, 1};
        static const int dzs[] = {-1, -1, -1, 0, 0, 1, 1, 1};
        int x = current.cell % g.width, z = current.cell / g.width;
        for (int direction = 0; direction < 8; ++direction) {
            if (!(g.links[current.cell] & (1 << direction))) continue;
            int nx = x + dxs[direction], nz = z + dzs[direction];
            if (nx < 0 || nz < 0 || nx >= g.width || nz >= g.height) continue;
            int next = nz * g.width + nx;
            if (!g.valid(next)) continue;
            Edge edge = edge_key(current.cell, next);
            if (hard.count(edge)) continue;
            double run = g.cell * ((dxs[direction] && dzs[direction]) ? g.diagonal : 1.0);
            double delta = g.heights[next] / 1000.0 - g.heights[current.cell] / 1000.0;
            double slope = std::fabs(delta) / std::max(run, 0.1);
            double ratio = slope / std::max(0.05, g.grade);
            double slope_cost = run * ratio * ratio * 6.0;
            if (delta < 0.0) slope_cost *= 1.25;
            int links = 0;
            for (int bit = 0; bit < 8; ++bit) links += !!(g.links[next] & (1 << bit));
            double terrain = prefer ? static_cast<double>(8 - links) * g.cell * g.clearance : 0.0;
            if (g.hazards[next] & 4) terrain += g.cell * g.shallow;
            double px = g.ox + nx * g.cell, pz = g.oz + nz * g.cell;
            for (auto point : avoid) {
                double dx = px - point.first, dz = pz - point.second;
                double d = std::sqrt(dx * dx + dz * dz);
                if (d < g.cell * 1.5) terrain += (g.cell * 1.5 - d) * 3.0;
            }
            double local_cost = 0.0, failed_cost = 0.0;
            auto local_it = local.find(edge);
            if (local_it != local.end()) local_cost = local_it->second;
            auto hull_it = g.hulls.find(edge);
            if (hull_it != g.hulls.end()) failed_cost = hull_it->second;
            double timed_cost = 0.0;
            auto failed_it = g.failed.find(edge);
            if (failed_it != g.failed.end()) {
                if (now >= failed_it->second.first) {
                    g.expired.push_back(edge); g.failed.erase(failed_it);
                } else timed_cost = failed_it->second.second;
            }
            failed_cost = std::max(failed_cost, timed_cost);
            double cost = current.cost + run + slope_cost + terrain + failed_cost + local_cost;
            auto previous = costs.find(next);
            if (previous == costs.end() || cost < previous->second) {
                costs[next] = cost; parents[next] = current.cell;
                double heuristic = g.distance(next, goal) * g.cell * g.heuristic;
                frontier.push({cost + heuristic, ++sequence, next, cost});
            }
        }
        // Exactly one Python generator yield occurs after this expansion.
        // Exhaustion is observed on the next paid step, including at the cap.
    }
};
std::unordered_map<int, std::shared_ptr<Graph> > graphs;
std::unordered_map<int, std::unique_ptr<Search> > searches;
int next_graph = 1, next_search = 1;

std::shared_ptr<Graph> get_graph(int id) {
    auto it = graphs.find(id); if (it == graphs.end()) throw Invalid(); return it->second;
}
Search &get_search(int id) {
    auto it = searches.find(id); if (it == searches.end()) throw Invalid(); return *it->second;
}
int cell_index(Reader &r, const Graph &g, bool allow_missing = false) {
    return r.integer(allow_missing ? -1 : 0, g.width * g.height - 1);
}
Edge read_edge(Reader &r, const Graph &g) {
    int a = cell_index(r, g), b = cell_index(r, g); return edge_key(a, b);
}
int execute(double *b, int n) {
    Reader r{b, n, 0}; int op = r.integer(0, 9);
    if (op == 0) { r.end(); searches.clear(); graphs.clear(); return 0; }
    if (op == 1) {
        auto g = std::make_shared<Graph>();
        g->width = r.integer(1, 4096); g->height = r.integer(1, 4096);
        int cells = g->width * g->height;
        if (cells > 4000000) throw Invalid();
        g->cell = r.number(); g->ox = r.number(); g->oz = r.number();
        g->grade = r.number(); g->heuristic = r.number();
        g->clearance = r.number(); g->shallow = r.number(); g->diagonal = r.number();
        if (g->cell < 1.0 || g->grade < 0.05 || g->heuristic < 0 || g->diagonal < 1.0 || n != 11 + cells * 3) throw Invalid();
        g->heights.reserve(cells); g->links.reserve(cells); g->hazards.reserve(cells);
        for (int i = 0; i < cells; ++i) {
            double height = b[r.p++];
            if (std::isinf(height)) throw Invalid();
            g->heights.push_back(height);
            g->links.push_back(static_cast<unsigned char>(r.integer(0, 255)));
            g->hazards.push_back(static_cast<unsigned char>(r.integer(0, 255)));
        }
        int id = next_graph++; graphs[id] = g; b[0] = id; return 0;
    }
    if (op == 2) {
        auto g = get_graph(r.integer()); int count = r.integer();
        std::unordered_map<Edge, std::pair<double, double> > failed;
        for (int i = 0; i < count; ++i) {
            Edge edge = read_edge(r, *g); double expiry = r.number(), penalty = r.number();
            failed[edge] = {expiry, penalty};
        }
        count = r.integer(); std::unordered_map<Edge, double> hulls;
        for (int i = 0; i < count; ++i) {
            Edge edge = read_edge(r, *g); double penalty = r.number();
            hulls[edge] = penalty;
        }
        r.end(); g->failed.swap(failed); g->hulls.swap(hulls); return 0;
    }
    if (op == 3) {
        std::unique_ptr<Search> s(new Search()); s->graph = get_graph(r.integer());
        s->start = cell_index(r, *s->graph, true); s->goal = cell_index(r, *s->graph, true);
        s->maximum = r.integer(-1000000, 1000000); s->now = r.number();
        s->prefer = r.integer(0, 1) != 0; r.end();
        if ((s->start >= 0 && !s->graph->valid(s->start)) || (s->goal >= 0 && !s->graph->valid(s->goal))) throw Invalid();
        s->closest = s->start;
        s->closest_distance = s->graph->distance(s->start, s->goal);
        if (s->start >= 0) {
            s->costs[s->start] = 0.0; s->frontier.push({0.0, 0, s->start, 0.0});
        }
        int id = next_search++; searches[id] = std::move(s); b[0] = id; return 0;
    }
    if (op == 4) {
        Search &s = get_search(r.integer()); int count = r.integer();
        std::vector<std::pair<double, double> > avoid;
        for (int i = 0; i < count; ++i) { double x = r.number(), z = r.number(); avoid.push_back({x, z}); }
        std::unordered_map<Edge, double> local; count = r.integer();
        for (int i = 0; i < count; ++i) {
            Edge edge = read_edge(r, *s.graph); double cost = r.number();
            local[edge] = cost;
        }
        bool hard_present = r.integer(0, 1) != 0;
        std::unordered_set<Edge> hard; count = r.integer();
        for (int i = 0; i < count; ++i) hard.insert(read_edge(r, *s.graph));
        r.end(); s.avoid.swap(avoid); s.local.swap(local); s.hard.swap(hard);
        s.hard_present = hard_present; return 0;
    }
    if (op == 5) {
        int budget = r.integer(0, 1000000), count = r.integer(0, 4096);
        if (n != 8 + count) throw Invalid();
        r.p = 8; std::deque<int> queue;
        for (int i = 0; i < count; ++i) { int id = r.integer(); get_search(id); queue.push_back(id); }
        int consumed = 0, completed = 0, expansions = 0;
        while (consumed < budget && !queue.empty()) {
            int id = queue.front(); queue.pop_front(); Search &s = get_search(id);
            int previous = s.expansions; s.step(); expansions += s.expansions - previous; ++consumed;
            if (s.done) { completed = id; break; }
            queue.push_back(id);
        }
        b[3] = consumed; b[4] = completed; b[5] = queue.size(); b[6] = expansions;
        for (size_t i = 0; i < queue.size(); ++i) b[8 + i] = queue[i];
        return 0;
    }
    if (op == 6) {
        Search &s = get_search(r.integer());
        if (!s.done || n < static_cast<int>(2 + s.path.size())) throw Invalid();
        b[0] = s.path.size(); b[1] = s.expansions;
        for (size_t i = 0; i < s.path.size(); ++i) b[2 + i] = s.path[i];
        return 0;
    }
    if (op == 7) {
        auto g = get_graph(r.integer());
        if (n < static_cast<int>(1 + 2 * g->expired.size())) throw Invalid();
        b[0] = g->expired.size();
        for (size_t i = 0; i < g->expired.size(); ++i) {
            b[1 + i * 2] = g->expired[i] >> 32;
            b[2 + i * 2] = static_cast<uint32_t>(g->expired[i]);
        }
        g->expired.clear(); return 0;
    }
    if (op == 8) { int id = r.integer(); r.end(); searches.erase(id); return 0; }
    if (op == 9) { int id = r.integer(); r.end(); graphs.erase(id); return 0; }
    throw Invalid();
}
}
extern "C" int offline_astar_dispatch(double *buffer, int count) {
    if (!buffer || count < 1 || count > 12000012) return 1;
    try {
        if (!std::isfinite(buffer[0]) || buffer[0] != std::floor(buffer[0]) ||
            buffer[0] < 0 || buffer[0] > 1000000000) return 2;
        if (buffer[0] == 0 && count != 1) return 2;
        if (buffer[0] == 0) { offline_combat_reset(); offline_driver_reset(); offline_perception_reset(); }
        if (buffer[0] >= 300) return offline_perception_dispatch(buffer, count);
        if (buffer[0] >= 200) return offline_driver_dispatch(buffer, count);
        if (buffer[0] >= 100) return offline_combat_dispatch(buffer, count);
        return execute(buffer, count);
    }
    catch (const Invalid &) { return 2; }
    catch (const std::bad_alloc &) { return 3; }
    catch (const std::exception &) { return 4; }
    catch (...) { return 5; }
}
