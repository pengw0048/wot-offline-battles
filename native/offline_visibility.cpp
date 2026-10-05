#include "offline_visibility.h"
#include "offline_async_worker.h"
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <unordered_map>
#include <utility>
namespace offline_visibility {
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
    std::vector<Completion> take(int64_t id) {
        std::vector<Completion> out;
        std::lock_guard<std::mutex> lock(mutex_);const auto context=find(id);
        if (!context || context->closed) return out;
        out.reserve(context->completed.size());
        for (const auto &job:context->completed) {
            Completion value;value.job_id=job->id;value.worker_seconds=job->worker_seconds;
            value.status=job->cancelled.load(std::memory_order_relaxed)?1:job->status;
            if (!value.status) value.rays=job->prepared.rays;
            else context->jobs.erase(job->id);
            job->emitted=true;out.push_back(std::move(value));
        }
        context->completed.clear();return out;
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
int64_t open(native_visibility::FoliageSnapshot snapshot) {return service().open_map(std::move(snapshot));}
bool update(int64_t context,Update changes) {return service().update_map(context,std::move(changes));}
bool submit(int64_t context,int64_t job_id,native_visibility::PairInput pair) {return service().submit_job(context,job_id,std::move(pair));}
std::vector<Completion> poll(int64_t context) {return service().take(context);}
bool reduce(int64_t context,int64_t job_id,const std::vector<std::uint8_t> &clear,native_visibility::VisibilityResult &result) {return service().finish(context,job_id,clear,result);}
void cancel(int64_t context,const std::vector<int64_t> &jobs) {service().cancel_jobs(context,jobs);}
void close(int64_t context) {service().close_map(context);}
}
