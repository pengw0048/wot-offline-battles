#include "offline_navigation.h"
#include "offline_async_worker.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <deque>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <queue>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace offline_navigation {
namespace {
constexpr double PI = 3.141592653589793238462643383279502884;
const std::array<Cell, 8> DIRECTIONS = {{{-1,-1},{0,-1},{1,-1},{-1,0},{1,0},{-1,1},{0,1},{1,1}}};
struct CellHash {
    size_t operator()(const Cell &cell) const {
        return static_cast<size_t>(static_cast<uint32_t>(cell[0])) * 0x9e3779b1U ^
               static_cast<size_t>(static_cast<uint32_t>(cell[1]));
    }
};
using Edge = std::pair<Cell, Cell>;
Edge edge(Cell first, Cell second) { return first < second ? Edge(first,second) : Edge(second,first); }
struct EdgeHash {
    size_t operator()(const Edge &value) const {
        return CellHash()(value.first) * 0x85ebca6bU ^ CellHash()(value.second);
    }
};
double distance(Point a, Point b) {
    const double dx = a[0]-b[0], dz = a[2]-b[2];
    return std::sqrt(dx*dx+dz*dz);
}
std::vector<Cell> crossed_cells(Cell start, Cell end) {
    int x=start[0], z=start[1];
    const int dx=std::abs(end[0]-x), dz=std::abs(end[1]-z);
    const int sx=x<end[0]?1:-1, sz=z<end[1]?1:-1;
    int error=dx-dz;
    std::vector<Cell> cells{start};
    while (x!=end[0] || z!=end[1]) {
        const int twice=error*2;
        if (twice>-dz) { error-=dz; x+=sx; }
        if (twice<dx) { error+=dx; z+=sz; }
        cells.push_back({{x,z}});
    }
    return cells;
}
using SegmentKey = std::array<int64_t, 6>;
SegmentKey segment_key(Point start, Point end) {
    // Match TerrainGrid._point_key, including the directed height layer.
    return {{static_cast<int64_t>(std::floor(start[0]*.5+.5)),
             static_cast<int64_t>(std::floor(start[2]*.5+.5)),
             static_cast<int64_t>(std::floor(start[1]/8.+.5)),
             static_cast<int64_t>(std::floor(end[0]*.5+.5)),
             static_cast<int64_t>(std::floor(end[2]*.5+.5)),
             static_cast<int64_t>(std::floor(end[1]/8.+.5))}};
}
struct Map : MapInput {
    explicit Map(MapInput value) : MapInput(std::move(value)) {
        const size_t count=static_cast<size_t>(width)*static_cast<size_t>(height);
        if (width<=0 || height<=0 || heights.size()!=count || links.size()!=count || hazards.size()!=count)
            throw std::invalid_argument("invalid navigation map");
        cell_size=std::max(1.,cell_size); max_grade=std::max(.05,max_grade);
    }
    bool flat(Cell cell) const { return cell[0]>=0 && cell[0]<width && cell[1]>=0 && cell[1]<height; }
    int index(Cell cell) const { return cell[1]*width+cell[0]; }
    bool valid(Cell cell) const { return flat(cell) && std::isfinite(heights[index(cell)]); }
    Cell cell_for(Point point) const {
        return {{static_cast<int>(std::floor((point[0]-origin_x)/cell_size+.5)),
                 static_cast<int>(std::floor((point[2]-origin_z)/cell_size+.5))}};
    }
    Point point_for(Cell cell) const {
        return {{origin_x+cell[0]*cell_size,heights[index(cell)],origin_z+cell[1]*cell_size}};
    }
    bool inside(Point point) const {
        return !has_bounds || (bounds[0]<=point[0] && point[0]<=bounds[2] && bounds[1]<=point[2] && point[2]<=bounds[3]);
    }
    bool nearest(Cell cell, int radius, Cell &out) const {
        if (valid(cell)) { out=cell; return true; }
        bool found=false; int best_distance=0;
        for (int reach=1;reach<=radius;++reach) {
            for (int z=cell[1]-reach;z<=cell[1]+reach;++z)
                for (int x=cell[0]-reach;x<=cell[0]+reach;++x) {
                    if (std::max(std::abs(x-cell[0]),std::abs(z-cell[1]))!=reach) continue;
                    const Cell candidate={{x,z}};
                    if (!valid(candidate)) continue;
                    const int dx=x-cell[0], dz=z-cell[1], value=dx*dx+dz*dz;
                    if (!found || value<best_distance) {out=candidate;best_distance=value;found=true;}
                }
            if (found) return true;
        }
        return false;
    }
    std::vector<Cell> segment_cells(Point start, Point end, bool require_height=true) const {
        Cell first=cell_for(start), last=cell_for(end);
        if (require_height) {
            if (!nearest(first,2,first) || !valid(last)) return {};
        } else if (!flat(first) || !flat(last)) return {};
        return crossed_cells(first,last);
    }
    bool linked(Cell a, Cell b) const {
        if (!valid(a) || !valid(b)) return false;
        const Cell delta={{b[0]-a[0],b[1]-a[1]}};
        for (size_t direction=0;direction<DIRECTIONS.size();++direction)
            if (DIRECTIONS[direction]==delta) return (links[index(a)]&(1U<<direction))!=0;
        return false;
    }
    bool corridor(Point start, Point end, bool dry) const {
        const auto cells=segment_cells(start,end);
        if (cells.empty()) return false;
        for (size_t offset=1;offset<cells.size();++offset) {
            if (!valid(cells[offset]) || !linked(cells[offset-1],cells[offset])) return false;
            if (dry && (hazards[index(cells[offset])]&4U)) return false;
        }
        return true;
    }
    bool shallow_free(Point start, Point end) const {
        const auto cells=segment_cells(start,end);
        if (cells.empty()) return false;
        for (size_t offset=1;offset<cells.size();++offset)
            if (!valid(cells[offset]) || (hazards[index(cells[offset])]&4U)) return false;
        return true;
    }
    unsigned link_count(Cell cell) const {
        if (!valid(cell)) return 0;
        unsigned value=links[index(cell)]&255U, count=0;
        while (value) {value&=value-1; ++count;}
        return count;
    }
    double exposure(const std::vector<Point> &path, size_t first, size_t last, bool shortcut) const {
        std::vector<Cell> cells;
        if (first==last) {
            Cell cell;
            if (!nearest(cell_for(path[first]),2,cell)) return -1.;
            cells.push_back(cell);
        } else {
            for (size_t at=first;at<last;at=shortcut?last:at+1) {
                auto segment=segment_cells(path[at],path[shortcut?last:at+1]);
                if (segment.empty()) return -1.;
                size_t begin=(!cells.empty() && cells.back()==segment.front())?1:0;
                cells.insert(cells.end(),segment.begin()+static_cast<ptrdiff_t>(begin),segment.end());
            }
        }
        if (cells.empty()) return -1.;
        unsigned missing=0;
        for (Cell cell:cells) missing+=8-link_count(cell);
        return static_cast<double>(missing)/static_cast<double>(cells.size());
    }
};
using Penalties = std::unordered_map<Edge,double,EdgeHash>;
using HardEdges = std::unordered_set<Edge,EdgeHash>;
using ReviewCells = std::unordered_set<Cell,CellHash>;
double penalty_for(const Penalties &values, Edge key) {
    const auto found=values.find(key); return found==values.end()?0.:found->second;
}
bool preserves_climb(const std::vector<Point> &path,size_t first,size_t last) {
    if (last-first<2) return true;
    for (size_t index=first+1;index<last;++index) {
        const Point before=path[index-1], pivot=path[index], after=path[index+1];
        const double ox=after[0]-pivot[0], oz=after[2]-pivot[2], run=std::sqrt(ox*ox+oz*oz);
        if (run<=.1 || (after[1]-pivot[1])/run<=.10) continue;
        const double ix=pivot[0]-before[0], iz=pivot[2]-before[2];
        if (std::abs(ix)+std::abs(iz)<=.1) continue;
        double turn=std::atan2(ox,oz)-std::atan2(ix,iz);
        while (turn>PI) turn-=2.*PI;
        while (turn<-PI) turn+=2.*PI;
        if (std::abs(turn)>.30) return false;
    }
    return true;
}
struct Frontier {
    double priority,cost; uint64_t sequence; Cell cell;
};
struct CompareFrontier {
    bool operator()(const Frontier &a,const Frontier &b) const {
        return a.priority>b.priority || (a.priority==b.priority && a.sequence>b.sequence);
    }
};
struct Node { double cost; Cell parent; bool has_parent; };
struct Candidate {Cell cell;double cost;};
struct PendingQuery { Point start,end; SegmentKey key; };
enum class Stage { Start, Expand, Relax, Finish, ProvePath, Done };
enum class RunState { Queued, Running, Waiting, Done };
struct Job {
    std::shared_ptr<const Map> map;
    SearchInput input;
    Penalties penalties,world;
    HardEdges hard;
    ReviewCells review_cells;
    std::map<SegmentKey,bool> reviews;
    std::vector<PendingQuery> requests;
    std::priority_queue<Frontier,std::vector<Frontier>,CompareFrontier> frontier;
    std::unordered_map<Cell,Node,CellHash> nodes;
    std::vector<Candidate> candidates;
    std::vector<Point> path;
    Cell start={},goal={},current={},closest={},reached={};
    double closest_distance=0.,worker_seconds=0.;
    uint64_t sequence=0;
    unsigned expanded=0,attempt_expanded=0,status=0,unanswered=0;
    bool has_reached=false,queued=false;
    std::atomic<bool> cancelled{false};
    Stage stage=Stage::Start;
    RunState state=RunState::Queued;
    explicit Job(std::shared_ptr<const Map> data, SearchInput value):map(std::move(data)),input(std::move(value)) {
        for (const auto &row:input.edge_penalties) penalties[edge(row.first,row.second)]=row.value;
        for (const auto &row:input.world_penalties) world[edge(row.first,row.second)]=row.value;
        for (const auto &row:input.hard_edges) hard.insert(edge(row.first,row.second));
        for (Cell cell:input.native_review_cells) review_cells.insert(cell);
        input.edge_penalties.clear(); input.world_penalties.clear();
        input.hard_edges.clear(); input.native_review_cells.clear();
    }
    bool needs_review(Point a,Point b) const {
        if (review_cells.empty()) return false;
        for (Cell cell:map->segment_cells(a,b,false)) if (review_cells.count(cell)) return true;
        return false;
    }
    // -1: oracle pending, 0: blocked, 1: clear. The long native-review
    // rejection belongs to the original corridor law, not a new work cap.
    int review(Point a,Point b) {
        if (distance(a,b)>map->cell_size*12.) return 0;
        const auto key=segment_key(a,b);
        const auto found=reviews.find(key);
        if (found!=reviews.end()) return found->second?1:0;
        for (const auto &request:requests) if (request.key==key) return -1;
        requests.push_back({a,b,key});
        return -1;
    }
    bool reviewed_blocked(Point a,Point b) const {
        if (!needs_review(a,b)) return false;
        if (distance(a,b)>map->cell_size*12.) return true;
        const auto found=reviews.find(segment_key(a,b));
        return found!=reviews.end() && !found->second;
    }
    bool hard_segment(Point a,Point b) const {
        if (hard.empty()) return false;
        const auto cells=crossed_cells(map->cell_for(a),map->cell_for(b));
        for (size_t index=1;index<cells.size();++index)
            if (hard.count(edge(cells[index-1],cells[index]))) return true;
        return false;
    }
    bool world_segment(Point a,Point b) const {
        if (world.empty()) return false;
        const auto cells=crossed_cells(map->cell_for(a),map->cell_for(b));
        for (size_t index=1;index<cells.size();++index)
            if (penalty_for(world,edge(cells[index-1],cells[index]))>0.) return true;
        return false;
    }
    int segment_clear(Point a,Point b,bool dry) {
        if (!map->inside(b)) return 0;
        if (dry && (world_segment(a,b) || !map->shallow_free(a,b))) return 0;
        if (distance(a,b)<.25) return 1;
        if (!map->corridor(a,b,false)) return 0;
        return needs_review(a,b)?review(a,b):1;
    }
    double terrain_penalty(Cell cell) const {
        double value=input.prefer_clearance?(8-map->link_count(cell))*map->cell_size*.20:0.;
        if (map->hazards[map->index(cell)]&4U) value+=map->cell_size*4.;
        const auto where=map->point_for(cell);
        for (Point point:input.avoid_points) {
            const double d=distance(where,point);
            if (d<map->cell_size*1.5) value+=(map->cell_size*1.5-d)*3.;
        }
        return value;
    }
    void terminate(unsigned value) {status=value;stage=Stage::Done;if(value)path.clear();}
    void restart_search() {
        // A rejected corridor removes only that directed proof from the next
        // candidate search. Keep all owned receipts and the original search
        // expansion limit; cumulative expanded remains diagnostic work.
        frontier={};nodes.clear();candidates.clear();path.clear();
        sequence=0;attempt_expanded=0;has_reached=false;stage=Stage::Start;
    }
    void prove_path() {
        for (size_t at=1;at<path.size();++at) {
            if (reviewed_blocked(path[at-1],path[at])) {restart_search();return;}
        }
        // Search candidates may traverse unknown review edges, but every
        // selected edge is proved before publication. Emit the entire path's
        // proof frontier together, never one A* expansion per render frame.
        for (size_t at=1;at<path.size();++at)
            if (needs_review(path[at-1],path[at])) review(path[at-1],path[at]);
        auto candidate=path;
        Point raw=input.goal;
        const Cell cell=map->cell_for(raw);
        raw[1]=(map->inside(raw) && map->valid(cell))?map->heights[map->index(cell)]:candidate.back()[1];
        if (!hard_segment(candidate.back(),raw) && segment_clear(candidate.back(),raw,false)!=0)
            candidate.push_back(raw);
        std::vector<Point> smoothed;
        if (candidate.size()<3) smoothed=candidate;
        else {
            smoothed.push_back(candidate[0]);
            size_t at=0;
            while (at<candidate.size()-1) {
                size_t last=std::min(candidate.size()-1,at+6);
                for (;last>at+1;--last) {
                    bool clearance=true;
                    if (input.prefer_clearance) {
                        const double original=map->exposure(candidate,at,last,false);
                        const double shortcut=map->exposure(candidate,at,last,true);
                        clearance=original>=0. && shortcut>=0. && shortcut<=original+.25;
                    }
                    if (clearance && preserves_climb(candidate,at,last) &&
                        !hard_segment(candidate[at],candidate[last]) &&
                        segment_clear(candidate[at],candidate[last],true)!=0) break;
                }
                // Unknown chords are prospective choices only. Each failed
                // chord yields the next batch of alternatives on the next
                // proof wave, while unrelated chords advance in parallel.
                smoothed.push_back(candidate[last]);at=last;
            }
        }
        if (!requests.empty()) return;
        path=std::move(smoothed);terminate(0);
    }
    void advance() {
        requests.clear();
        unsigned quantum=64;
        for (;;) {
            if (cancelled.load(std::memory_order_relaxed)) {terminate(1);return;}
            if (stage==Stage::Done) return;
            if (stage==Stage::Start) {
                if (!map->nearest(map->cell_for(input.start),3,start) || !map->nearest(map->cell_for(input.goal),3,goal)) {
                    terminate(0);return;
                }
                nodes.emplace(start,Node{0.,{},false});
                frontier.push({0.,0.,0,start});
                closest=start;
                const double dx=start[0]-goal[0],dz=start[1]-goal[1];
                closest_distance=std::sqrt(dx*dx+dz*dz);
                stage=Stage::Expand;
            }
            if (stage==Stage::Expand) {
                if (!quantum) return;
                if (frontier.empty() || attempt_expanded>=input.max_expansions) {stage=Stage::Finish;continue;}
                const Frontier entry=frontier.top();frontier.pop();
                const auto node=nodes.find(entry.cell);
                if (node==nodes.end() || entry.cost!=node->second.cost) continue;
                --quantum;++expanded;++attempt_expanded;current=entry.cell;
                const double dx=current[0]-goal[0],dz=current[1]-goal[1];
                const double goal_distance=std::sqrt(dx*dx+dz*dz);
                if (goal_distance<closest_distance) {closest=current;closest_distance=goal_distance;}
                if (current==goal) {reached=current;has_reached=true;stage=Stage::Finish;continue;}
                const Point a=map->point_for(current);
                const unsigned links=map->links[map->index(current)];
                candidates.clear();
                for (size_t direction=0;direction<DIRECTIONS.size();++direction) {
                    if (!(links&(1U<<direction))) continue;
                    const Cell delta=DIRECTIONS[direction];
                    const Cell next={{current[0]+delta[0],current[1]+delta[1]}};
                    if (!map->valid(next)) continue;
                    const Edge key=edge(current,next);
                    if (hard.count(key)) continue;
                    const Point b=map->point_for(next);
                    if (reviewed_blocked(a,b)) continue;
                    const double run=map->cell_size*(delta[0] && delta[1]?std::sqrt(2.):1.);
                    const double dy=b[1]-a[1],slope=std::abs(dy)/std::max(run,.1),ratio=slope/map->max_grade;
                    double slope_cost=run*ratio*ratio*6.;
                    if (dy<0.) slope_cost*=1.25;
                    const double cost=node->second.cost+run+slope_cost+terrain_penalty(next)+
                        penalty_for(world,key)+penalty_for(penalties,key);
                    const auto previous=nodes.find(next);
                    if (previous!=nodes.end() && !(cost<previous->second.cost)) continue;
                    candidates.push_back({next,cost});
                }
                stage=Stage::Relax;
            }
            if (stage==Stage::Relax) {
                for (const auto &candidate:candidates) {
                    nodes[candidate.cell]={candidate.cost,current,true};
                    const double dx=candidate.cell[0]-goal[0],dz=candidate.cell[1]-goal[1];
                    const double heuristic=std::sqrt(dx*dx+dz*dz)*map->cell_size*map->heuristic_weight;
                    frontier.push({candidate.cost+heuristic,candidate.cost,++sequence,candidate.cell});
                }
                stage=Stage::Expand;continue;
            }
            if (stage==Stage::Finish) {
                if (!has_reached) {
                    if ((!hard.empty() && closest==start) ||
                        (closest_distance>3. && (frontier.empty() || closest==start))) {
                        terminate(0);return;
                    }
                    reached=closest;
                }
                std::vector<Cell> cells{reached};
                while (cells.back()!=start) {
                    const auto found=nodes.find(cells.back());
                    if (found==nodes.end() || !found->second.has_parent) throw std::runtime_error("navigation parent missing");
                    cells.push_back(found->second.parent);
                }
                path.clear();path.reserve(cells.size()+1);
                for (auto at=cells.rbegin();at!=cells.rend();++at) path.push_back(map->point_for(*at));
                stage=Stage::ProvePath;
            }
            if (stage==Stage::ProvePath) {
                prove_path();
                if (!requests.empty()) return;
            }
        }
    }
};
struct Context {
    std::shared_ptr<const Map> map;
    bool closed=false;
    std::unordered_map<int64_t,std::shared_ptr<Job>> jobs;
    struct Awaited { std::shared_ptr<Job> job; SegmentKey key; };
    std::unordered_map<int64_t,Awaited> awaited;
    std::vector<Query> ready_queries;
    std::vector<std::shared_ptr<Job>> completed;
};
class Service {
    std::mutex mutex_;
    std::unordered_map<int64_t,std::shared_ptr<Context>> contexts_;
    int64_t next_context_=1,next_query_=1;
    void queue(const std::shared_ptr<Context> &context,const std::shared_ptr<Job> &job) {
        if (job->queued || job->state==RunState::Running || job->state==RunState::Done) return;
        job->queued=true;job->state=RunState::Queued;
        offline_async::schedule([this,context,job] { run(context,job); });
    }
    void run(const std::shared_ptr<Context> &context,const std::shared_ptr<Job> &job) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            job->queued=false;
            if (context->closed) return;
            job->state=RunState::Running;
        }
        const auto start=std::chrono::steady_clock::now();
        try { job->advance(); } catch (...) {job->terminate(2);}
        const auto end=std::chrono::steady_clock::now();
        const double duration=std::chrono::duration<double>(end-start).count();
        std::lock_guard<std::mutex> lock(mutex_);
        job->worker_seconds+=duration;
        if (context->closed) return;
        if (job->cancelled.load(std::memory_order_relaxed)) job->terminate(1);
        if (job->stage==Stage::Done) {
            job->state=RunState::Done;context->completed.push_back(job);return;
        }
        if (!job->requests.empty()) {
            job->state=RunState::Waiting;
            job->unanswered=static_cast<unsigned>(job->requests.size());
            for (const auto &request:job->requests) {
                const int64_t id=next_query_++;
                context->awaited.emplace(id,Context::Awaited{job,request.key});
                context->ready_queries.push_back({id,job->input.job_id,request.start,request.end});
            }
        } else {job->state=RunState::Queued;queue(context,job);}
    }
    std::shared_ptr<Context> find(int64_t id) {
        const auto value=contexts_.find(id);return value==contexts_.end()?nullptr:value->second;
    }
public:
    int64_t open_map(MapInput input) {
        auto context=std::make_shared<Context>();
        context->map=std::make_shared<Map>(std::move(input));
        std::lock_guard<std::mutex> lock(mutex_);
        const int64_t id=next_context_++;contexts_.emplace(id,std::move(context));return id;
    }
    bool submit_job(int64_t id,SearchInput input) {
        std::shared_ptr<Context> context;
        {
            std::lock_guard<std::mutex> lock(mutex_);context=find(id);
            if (!context || context->closed || context->jobs.count(input.job_id)) return false;
        }
        auto job=std::make_shared<Job>(context->map,std::move(input));
        std::lock_guard<std::mutex> lock(mutex_);
        if (context->closed || context->jobs.count(job->input.job_id)) return false;
        context->jobs.emplace(job->input.job_id,job);queue(context,job);return true;
    }
    Poll take(int64_t id) {
        Poll result;
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        result.stats[5]=offline_async::worker_count();
        if (!context || context->closed) return result;
        for (const auto &entry:context->jobs) {
            const auto state=entry.second->state;
            if (state!=RunState::Done) ++result.stats[0];
            if (state==RunState::Running) ++result.stats[1];
            if (state==RunState::Waiting) ++result.stats[2];
        }
        result.stats[3]=context->ready_queries.size();result.stats[4]=context->completed.size();
        result.queries=std::move(context->ready_queries);context->ready_queries.clear();
        result.completed.reserve(context->completed.size());
        for (const auto &job:context->completed) {
            Completion value;
            value.job_id=job->input.job_id;value.hull_revision=job->input.hull_revision;
            value.status=job->cancelled.load(std::memory_order_relaxed)?1:job->status;
            value.expanded=job->expanded;value.worker_seconds=job->worker_seconds;
            if (!value.status) value.path=std::move(job->path);
            result.completed.push_back(std::move(value));context->jobs.erase(job->input.job_id);
        }
        context->completed.clear();return result;
    }
    void supply(int64_t id,const std::vector<std::pair<int64_t,bool>> &answers) {
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        if (!context || context->closed) return;
        for (const auto &answer:answers) {
            const auto found=context->awaited.find(answer.first);
            if (found==context->awaited.end()) continue;
            const auto job=found->second.job;
            if (!job->cancelled.load(std::memory_order_relaxed)) {
                job->reviews[found->second.key]=answer.second;
                if (job->unanswered) --job->unanswered;
                if (!job->unanswered && job->state==RunState::Waiting) queue(context,job);
            }
            context->awaited.erase(found);
        }
    }
    void cancel_jobs(int64_t id,const std::vector<int64_t> &ids) {
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        if (!context || context->closed) return;
        std::unordered_set<int64_t> cancelled;
        for (int64_t job_id:ids) {
            const auto found=context->jobs.find(job_id);
            if (found==context->jobs.end()) continue;
            const auto job=found->second;job->cancelled.store(true,std::memory_order_relaxed);cancelled.insert(job_id);
            if (job->state==RunState::Waiting) queue(context,job);
        }
        for (auto at=context->awaited.begin();at!=context->awaited.end();)
            if (cancelled.count(at->second.job->input.job_id)) at=context->awaited.erase(at); else ++at;
        auto &ready=context->ready_queries;
        ready.erase(std::remove_if(ready.begin(),ready.end(),[&](const Query &q){return cancelled.count(q.job_id)!=0;}),ready.end());
    }
    void close_map(int64_t id) {
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        if (!context) return;
        context->closed=true;
        for (const auto &entry:context->jobs) entry.second->cancelled.store(true,std::memory_order_relaxed);
        context->awaited.clear();context->ready_queries.clear();context->completed.clear();context->jobs.clear();
        contexts_.erase(id);
    }
};
// Keep the process-lived service available until OS thread termination. An
// explicit round close releases its map/jobs without a DLL-detach destructor.
Service &service() {static Service *const value=new Service;return *value;}
}
int64_t open(MapInput map) {return service().open_map(std::move(map));}
bool submit(int64_t context,SearchInput search) {return service().submit_job(context,std::move(search));}
Poll poll(int64_t context) {return service().take(context);}
void answer(int64_t context,const std::vector<std::pair<int64_t,bool>> &answers) {service().supply(context,answers);}
void cancel(int64_t context,const std::vector<int64_t> &jobs) {service().cancel_jobs(context,jobs);}
void close(int64_t context) {service().close_map(context);}
}
