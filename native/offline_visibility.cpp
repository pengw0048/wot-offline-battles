#include "offline_visibility.h"
#include "offline_async_worker.h"
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <unordered_map>
#include <utility>
#include <map>
#include <set>
#include <cmath>
#include <limits>
namespace offline_visibility {
namespace {
void frontier_open(int64_t);
void frontier_changed(int64_t,const Update &);
void frontier_close(int64_t);
}

namespace {
struct Job {
    int64_t id=0;
    native_visibility::PairInput input;
    std::shared_ptr<const native_visibility::FoliageSnapshot> snapshot;
    native_visibility::PreparedVisibility prepared;
    std::atomic<bool> cancelled{false};
    bool done=false,emitted=false;
    unsigned status=0;
    double worker_seconds=0.;
};
struct Context {
    bool closed=false;
    std::shared_ptr<const native_visibility::FoliageSnapshot> snapshot;
    std::unordered_map<int64_t,std::shared_ptr<Job>> jobs;
    std::vector<std::shared_ptr<Job>> completed;
};
class Service {
    std::mutex mutex_;
    int64_t next_context_=1;
    std::unordered_map<int64_t,std::shared_ptr<Context>> contexts_;
    std::shared_ptr<Context> find(int64_t id) {
        const auto value=contexts_.find(id);
        return value==contexts_.end()?nullptr:value->second;
    }
    void run(const std::shared_ptr<Context> &context,const std::shared_ptr<Job> &job) {
        const auto start=std::chrono::steady_clock::now();
        unsigned status=0;
        native_visibility::PreparedVisibility prepared;
        if (!job->cancelled.load(std::memory_order_relaxed)) {
            try {prepared=native_visibility::prepare_visibility(job->input,*job->snapshot);}
            catch (...) {status=2;}
        }
        const double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
        std::lock_guard<std::mutex> lock(mutex_);
        if (context->closed) return;
        job->status=job->cancelled.load(std::memory_order_relaxed)?1:status;
        if (!job->status) job->prepared=std::move(prepared);
        job->snapshot.reset();job->done=true;job->worker_seconds=elapsed;
        context->completed.push_back(job);
    }
public:
    double cell_size(int64_t id) {
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        return context?context->snapshot->cell_size:32.;
    }
    int64_t open_map(native_visibility::FoliageSnapshot snapshot) {
        auto context=std::make_shared<Context>();
        context->snapshot=std::make_shared<const native_visibility::FoliageSnapshot>(std::move(snapshot));
        std::lock_guard<std::mutex> lock(mutex_);
        const auto id=next_context_++;contexts_.emplace(id,std::move(context));return id;
    }
    bool update_map(int64_t id,Update changes) {
        std::shared_ptr<Context> context;
        std::shared_ptr<const native_visibility::FoliageSnapshot> previous;
        {
            std::lock_guard<std::mutex> lock(mutex_);context=find(id);
            if (!context || context->closed) return false;
            previous=context->snapshot;
        }
        // Existing in-flight jobs retain their immutable snapshot. Python
        // serializes update calls; the expensive copy never holds this mutex.
        auto next=std::make_shared<native_visibility::FoliageSnapshot>(*previous);
        for (auto &entry:changes.instances) {
            if (entry.first>next->instances.size()) return false;
            if (entry.first==next->instances.size()) next->instances.push_back(std::move(entry.second));
            else next->instances[entry.first]=std::move(entry.second);
        }
        for (auto &entry:changes.cells) {
            for (size_t index:entry.second) if (index>=next->instances.size()) return false;
            if (entry.second.empty()) next->cells.erase(entry.first);
            else next->cells[entry.first]=std::move(entry.second);
        }
        next->inactive_instances=std::move(changes.inactive);
        std::lock_guard<std::mutex> lock(mutex_);
        if (context->closed || context->snapshot!=previous) return false;
        context->snapshot=std::move(next);return true;
    }
    bool submit_job(int64_t id,int64_t job_id,native_visibility::PairInput input) {
        auto job=std::make_shared<Job>();job->id=job_id;job->input=std::move(input);
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        if (!context || context->closed || context->jobs.count(job_id)) return false;
        job->snapshot=context->snapshot;context->jobs.emplace(job_id,job);
        offline_async::schedule([this,context,job] {run(context,job);});return true;
    }
    bool prepare_ready(int64_t id,int64_t job_id,native_visibility::PairInput input,
                       Completion &completion) {
        auto job=std::make_shared<Job>();job->id=job_id;job->input=std::move(input);
        std::shared_ptr<Context> context;
        std::shared_ptr<const native_visibility::FoliageSnapshot> snapshot;
        {
            std::lock_guard<std::mutex> lock(mutex_);context=find(id);
            if (!context || context->closed || context->jobs.count(job_id)) return false;
            snapshot=context->snapshot;
        }
        // Preparing numeric foliage data cannot call the engine. Keep the
        // immutable snapshot alive without holding a lock over this work.
        try {job->prepared=native_visibility::prepare_visibility(job->input,*snapshot);}
        catch (...) {job->status=2;}
        job->done=true;job->emitted=true;
        completion.job_id=job_id;completion.status=job->status;
        if (!job->status) completion.rays=job->prepared.rays;
        std::lock_guard<std::mutex> lock(mutex_);
        if (context->closed || context->snapshot!=snapshot || context->jobs.count(job_id)) return false;
        // The caller consumes this completion directly. No worker time or
        // asynchronous completion is attributed to synchronous preparation.
        context->jobs.emplace(job_id,std::move(job));return true;
    }
    std::vector<Completion> take(int64_t id,bool frontier=false) {
        std::vector<Completion> out;
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        if (!context || context->closed) return out;
        out.reserve(context->completed.size());
        std::vector<std::shared_ptr<Job>> retained;
        for (const auto &job:context->completed) {
            if((job->id<0)!=frontier){retained.push_back(job);continue;}
            Completion value;value.job_id=job->id;value.worker_seconds=job->worker_seconds;
            value.status=job->cancelled.load(std::memory_order_relaxed)?1:job->status;
            if (!value.status) value.rays=job->prepared.rays;
            else context->jobs.erase(job->id);
            job->emitted=true;out.push_back(std::move(value));
        }
        context->completed=std::move(retained);return out;
    }
    bool finish(int64_t id,int64_t job_id,const std::vector<std::uint8_t> &clear,
                native_visibility::VisibilityResult &result) {
        std::shared_ptr<Job> job;
        {
            std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
            if (!context || context->closed) return false;
            const auto found=context->jobs.find(job_id);
            if (found==context->jobs.end()) return false;
            job=found->second;
            if (!job->done || !job->emitted || job->status || job->cancelled.load(std::memory_order_relaxed)) return false;
            if (clear.size()>job->prepared.rays.size()) return false;
            // A partial prefix is complete only at the same early-stop point
            // as the existing sight loop, including empty geometry results.
            if (clear.size()<job->prepared.rays.size() && (clear.empty() ||
                !native_visibility::should_stop(job->prepared,clear.size()-1,clear.back()!=0))) return false;
            context->jobs.erase(found);
        }
        result=native_visibility::reduce_visibility(job->prepared,clear);return true;
    }
    bool finish_detection(int64_t id,int64_t job_id,const std::vector<std::uint8_t> &observed,
                          bool &detected) {
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        if (!context || context->closed) return false;
        const auto found=context->jobs.find(job_id);
        if (found==context->jobs.end()) return false;
        const auto job=found->second;
        if (!job->done || !job->emitted || job->status || job->cancelled.load(std::memory_order_relaxed)) return false;
        if (observed.size()>job->prepared.rays.size()) return false;
        bool positive=job->prepared.detection.distance<=50.;
        for(std::size_t i=0;i<observed.size();++i) {
            const bool eligible=native_visibility::can_detect_with_foliage(
                job->prepared.detection,job->prepared.rays[i].foliage_bonus);
            if(observed[i]>2 || (observed[i]==2 && eligible)) return false;
            if(observed[i]==1 && eligible) positive=true;
        }
        if(!positive && observed.size()!=job->prepared.rays.size()) return false;
        detected=positive;context->jobs.erase(found);return true;
    }
    void cancel_jobs(int64_t id,const std::vector<int64_t> &ids) {
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        if (!context || context->closed) return;
        for (int64_t job_id:ids) {
            const auto found=context->jobs.find(job_id);if (found==context->jobs.end()) continue;
            const auto job=found->second;job->cancelled.store(true,std::memory_order_relaxed);
            if (job->emitted) context->jobs.erase(found);
        }
    }
    void close_map(int64_t id) {
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        if (!context) return;
        context->closed=true;
        for (const auto &entry:context->jobs) entry.second->cancelled.store(true,std::memory_order_relaxed);
        context->jobs.clear();context->completed.clear();contexts_.erase(id);
    }
};
// Match the process-lived executor: no service destruction or thread join
// during DLL detach. Explicit context close releases all per-round data.
Service &service() {static Service *const value=new Service;return *value;}
}
int64_t open(native_visibility::FoliageSnapshot snapshot) {const auto id=service().open_map(std::move(snapshot));frontier_open(id);return id;}
bool update(int64_t context,Update changes) {if (!service().update_map(context,changes)) return false;frontier_changed(context,changes);return true;}
bool submit(int64_t context,int64_t job_id,native_visibility::PairInput pair) {return service().submit_job(context,job_id,std::move(pair));}
std::vector<Completion> poll(int64_t context) {return service().take(context);}
bool reduce(int64_t context,int64_t job_id,const std::vector<std::uint8_t> &clear,native_visibility::VisibilityResult &result) {return service().finish(context,job_id,clear,result);}
void cancel(int64_t context,const std::vector<int64_t> &jobs) {service().cancel_jobs(context,jobs);}
void close(int64_t context) {frontier_close(context);service().close_map(context);}
namespace {
using PairKey=std::pair<ActorKey,ActorKey>;
struct FrontierJob {
    int64_t id=0, fire=-1;
    uint64_t observer_identity=0,target_identity=0;
    native_visibility::DetectionInputs detection;
    double sampled_at=0.,requested_at=0.;
    bool done=false,unknown=false;
    unsigned status=0;
    std::vector<native_visibility::Ray> rays;
    std::set<native_visibility::Cell> dirty;
};
struct Frontier {
    bool closed=false;
    int64_t next_job=0;
    uint64_t phase=0,frame_version=0;
    double poll_at=std::numeric_limits<double>::quiet_NaN();
    FrontierSnapshot counts;
    std::map<ActorKey,FrontierActor> actors;
    std::map<PairKey,FrontierJob> jobs;
    std::map<int64_t,PairKey> by_id;
};
std::mutex frontier_mutex;
std::map<int64_t,std::shared_ptr<Frontier>> frontiers;
std::shared_ptr<Frontier> frontier_get(int64_t id) {
    std::lock_guard<std::mutex> lock(frontier_mutex);
    const auto found=frontiers.find(id);
    return found==frontiers.end()?nullptr:found->second;
}
void frontier_open(int64_t id) {
    std::lock_guard<std::mutex> lock(frontier_mutex);
    frontiers[id]=std::make_shared<Frontier>();
}
void frontier_close(int64_t id) {
    std::lock_guard<std::mutex> lock(frontier_mutex);
    const auto found=frontiers.find(id);
    if(found==frontiers.end()) return;
    found->second->closed=true;frontiers.erase(found);
}
void frontier_changed(int64_t id,const Update &update) {
    const auto f=frontier_get(id);if(!f || f->closed)return;
    for(auto &entry:f->jobs) {
        if(update.cells.empty())entry.second.unknown=true;
        for(const auto &cell:update.cells)entry.second.dirty.insert(cell.first);
    }
}
void frontier_cancel(int64_t id,Frontier &f,const PairKey &key,unsigned reason=0) {
    const auto found=f.jobs.find(key);if(found==f.jobs.end())return;
    cancel(id,{found->second.id});f.by_id.erase(found->second.id);f.jobs.erase(found);
    ++f.counts.cancelled;++f.counts.reasons[reason];
}
bool same_detection(const native_visibility::DetectionInputs &a,
                    const native_visibility::DetectionInputs &b) {
    // Distance changes every frame and is intentionally absent from the token.
    return a.view_range==b.view_range && a.base_camouflage==b.base_camouflage &&
        a.moving==b.moving && a.fired_recently==b.fired_recently &&
        a.additive==b.additive && a.multiplier==b.multiplier && a.shot_factor==b.shot_factor;
}
bool foliage_changed(const FrontierJob &job,double cell_size) {
    if(job.unknown)return true;
    if(job.dirty.empty() || job.rays.empty())return false;
    double minx=job.rays[0].start[0],maxx=minx,minz=job.rays[0].start[2],maxz=minz;
    for(const auto &ray:job.rays)for(const auto &point:{ray.start,ray.end}) {
        minx=std::min(minx,point[0]);maxx=std::max(maxx,point[0]);
        minz=std::min(minz,point[2]);maxz=std::max(maxz,point[2]);
    }
    const double x0=std::floor(minx/cell_size)-1,x1=std::floor(maxx/cell_size)+1;
    const double z0=std::floor(minz/cell_size)-1,z1=std::floor(maxz/cell_size)+1;
    for(const auto &cell:job.dirty)if(x0<=cell.first && cell.first<=x1 && z0<=cell.second && cell.second<=z1)return true;
    return false;
}
void frontier_poll(int64_t id,Frontier &f,double now) {
    if(f.poll_at==now)return;
    f.poll_at=now;
    for(auto &completion:service().take(id,true)) {
        const auto found=f.by_id.find(completion.job_id);if(found==f.by_id.end())continue;
        f.counts.worker_seconds+=completion.worker_seconds;
        auto job=f.jobs.find(found->second);if(job==f.jobs.end())continue;
        job->second.done=true;job->second.status=completion.status;
        job->second.rays=std::move(completion.rays);
    }
    std::vector<PairKey> expired;
    for(const auto &entry:f.jobs)if(now-entry.second.requested_at>10.)expired.push_back(entry.first);
    for(const auto &key:expired)frontier_cancel(id,f,key,5);
}
}
bool frontier_frame(int64_t id,std::vector<FrontierActor> actors,uint64_t phase,double now) {
    const auto f=frontier_get(id);if(!f || f->closed)return false;
    std::map<ActorKey,FrontierActor> next;
    for(auto &actor:actors)next.emplace(actor.key,std::move(actor));
    std::vector<PairKey> stale;
    for(const auto &entry:f->jobs) {
        const auto a=next.find(entry.first.first),b=next.find(entry.first.second);
        if(a==next.end() || b==next.end() || !b->second.target_available || a->second.identity!=entry.second.observer_identity ||
           b->second.identity!=entry.second.target_identity)stale.push_back(entry.first);
    }
    for(const auto &key:stale)frontier_cancel(id,*f,key);
    f->actors=std::move(next);f->phase=phase;++f->frame_version;frontier_poll(id,*f,now);return true;
}
bool frontier_actor(int64_t id,FrontierActor actor) {
    const auto f=frontier_get(id);if(!f || f->closed)return false;
    const auto old=f->actors.find(actor.key);
    if(!actor.identity || (old!=f->actors.end() && old->second.identity!=actor.identity)) {
        std::vector<PairKey> stale;
        for(const auto &entry:f->jobs)if(entry.first.first==actor.key || entry.first.second==actor.key)stale.push_back(entry.first);
        for(const auto &key:stale)frontier_cancel(id,*f,key);
    }
    if(!actor.target_available && actor.identity) {
        std::vector<PairKey> stale;
        for(const auto &entry:f->jobs)if(entry.first.second==actor.key)stale.push_back(entry.first);
        for(const auto &key:stale)frontier_cancel(id,*f,key);
    }
    if(!actor.identity)f->actors.erase(actor.key);
    else f->actors[actor.key]=std::move(actor);
    return true;
}
namespace {
struct FrontierEvaluation {
    unsigned status=3; // completed=0, pending=2, failed=3
    bool detected=false,line_of_sight=false;
    double foliage=0.,sampled_at=0.;
};
FrontierEvaluation frontier_evaluate(int64_t id,const ActorKey &observer,const ActorKey &target,
    double now,int64_t fire,const native_visibility::DetectionInputs &detection,const RayOracle &ray,
    const PhaseOracle &phase,bool detection_only,bool world_query) {
    FrontierEvaluation reply;reply.sampled_at=now;
    const auto f=frontier_get(id);if(!f || f->closed)return reply;
    ++f->counts.requests;frontier_poll(id,*f,now);
    const auto a=f->actors.find(observer),b=f->actors.find(target);
    if(a==f->actors.end() || b==f->actors.end() || !b->second.target_available)return reply;
    const PairKey key(observer,target);auto found=f->jobs.find(key);
    bool fresh_prepare=false;
    if(found!=f->jobs.end()) {
        const auto &job=found->second;
        // The foliage cell size is immutable over the context lifetime.
        // Fetch it from the job snapshot via a small service accessor below.
        int reason=-1;
        if(job.observer_identity!=a->second.identity || job.target_identity!=b->second.identity)reason=0;
        else if(job.fire!=fire)reason=1;
        else if(!same_detection(job.detection,detection))reason=2;
        else if(job.done && foliage_changed(job,service().cell_size(id)))reason=3;
        else if(now-job.sampled_at>=0.75)reason=4;
        if(reason>=0){
            // A sparse planning cadence may otherwise replace every result
            // before it can be consumed. Refresh the same actor pair from
            // current inputs; identity changes still start a new async job.
            fresh_prepare=detection_only && world_query && reason>=1 && reason<=4;
            frontier_cancel(id,*f,key,static_cast<unsigned>(reason));found=f->jobs.end();
        }
    }
    if(found==f->jobs.end()) {
        // The clock capability may synchronously reenter Python and replace
        // actors or submit this same pair. Retain no map iterator across it.
        const auto observer_identity=a->second.identity,target_identity=b->second.identity;
        const auto frame_version=f->frame_version;
        const uint64_t observer_phase=phase?phase():f->phase;
        if(f->closed || f->frame_version!=frame_version)return reply;
        const auto current_a=f->actors.find(observer),current_b=f->actors.find(target);
        if(current_a==f->actors.end() || current_b==f->actors.end() ||
           current_a->second.identity!=observer_identity || current_b->second.identity!=target_identity ||
           !current_b->second.target_available)return reply;
        if(f->jobs.count(key)){reply.status=2;return reply;}
        FrontierJob job;job.id=--f->next_job;job.fire=fire;job.detection=detection;
        job.observer_identity=current_a->second.identity;job.target_identity=current_b->second.identity;
        job.sampled_at=job.requested_at=now;
        native_visibility::PairInput input;input.observer_checkpoints=current_a->second.checkpoints;
        input.target_checkpoints=current_b->second.checkpoints;input.observer=current_a->second.observer_pose;
        input.target=current_b->second.target_pose;input.observer_phase=observer_phase;input.detection=detection;
        if(fresh_prepare) {
            Completion completion;
            if(!service().prepare_ready(id,job.id,std::move(input),completion))return reply;
            job.done=true;job.status=completion.status;job.rays=std::move(completion.rays);
        } else if(!submit(id,job.id,std::move(input)))return reply;
        f->by_id.emplace(job.id,key);f->jobs.emplace(key,std::move(job));++f->counts.submitted;
        if(!fresh_prepare){reply.status=2;return reply;}
        found=f->jobs.find(key);
    }
    found->second.requested_at=now;
    // A preparation slot may encounter an already-ready frontier receipt
    // after control eligibility changes. Only a world-query slot may use it.
    if(!world_query){reply.status=2;return reply;}
    // Copy owned rays before engine callbacks, which may close this context.
    const FrontierJob job=found->second;
    if(!job.done){reply.status=2;return reply;}
    if(job.status){frontier_cancel(id,*f,key,6);reply.status=2;return reply;}
    std::vector<uint8_t> clear;clear.reserve(job.rays.size());
    for(const auto &item:job.rays) {
        if(f->closed)return reply;
        const auto current=f->jobs.find(key);
        if(current==f->jobs.end() || current->second.id!=job.id)return reply;
        const bool eligible=!detection_only || native_visibility::can_detect_with_foliage(
            job.detection,item.foliage_bonus);
        if(!eligible){clear.push_back(2);continue;}
        const bool value=ray(item);clear.push_back(value?1:0);
        if(f->closed)return reply;
        if(value && (detection_only || item.foliage_bonus<=0.))break;
    }
    if(detection_only) {
        if(!service().finish_detection(id,job.id,clear,reply.detected))return reply;
    } else {
        native_visibility::VisibilityResult result;
        if(!reduce(id,job.id,clear,result))return reply;
        reply.detected=result.detected;reply.line_of_sight=result.line_of_sight;
        reply.foliage=result.foliage_bonus;
    }
    // Do not erase a replacement submitted by synchronous engine reentry.
    found=f->jobs.find(key);
    if(found!=f->jobs.end() && found->second.id==job.id){f->jobs.erase(found);f->by_id.erase(job.id);}
    ++f->counts.completed;f->counts.max_completion_age=std::max(f->counts.max_completion_age,std::max(0.,now-job.sampled_at));
    reply.status=0;reply.sampled_at=job.sampled_at;return reply;
}
}
FrontierReply frontier_sight(int64_t id,const ActorKey &observer,const ActorKey &target,
    double now,int64_t fire,const native_visibility::DetectionInputs &detection,const RayOracle &ray,const PhaseOracle &phase) {
    const auto value=frontier_evaluate(id,observer,target,now,fire,detection,ray,phase,false,true);
    FrontierReply reply;reply.status=value.status==0?(value.line_of_sight?0:1):value.status;
    reply.has_detection=value.status==0;reply.detected=value.detected;
    reply.foliage=value.foliage;reply.sampled_at=value.sampled_at;return reply;
}
FrontierDetectionReply frontier_detect(int64_t id,const ActorKey &observer,const ActorKey &target,
    double now,int64_t fire,const native_visibility::DetectionInputs &detection,const RayOracle &ray,const PhaseOracle &phase,bool world_query) {
    const auto value=frontier_evaluate(id,observer,target,now,fire,detection,ray,phase,true,world_query);
    FrontierDetectionReply reply;reply.status=value.status;reply.detected=value.detected;
    reply.sampled_at=value.sampled_at;return reply;
}
FrontierSnapshot frontier_snapshot(int64_t id) {
    const auto f=frontier_get(id);if(!f || f->closed)return {};
    auto result=f->counts;result.pending=f->jobs.size();return result;
}

}
