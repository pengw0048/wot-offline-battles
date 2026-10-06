// Focused same-thread frontier contracts; no engine/game performance claim.
#include "offline_visibility.h"
#include "offline_async_worker.h"
#include <atomic>
#include <cassert>
#include <chrono>
#include <stdexcept>
#include <thread>
#include <iostream>
using namespace offline_visibility;
using namespace native_visibility;
struct Fixture {
    int64_t context;
    double now=1.;
    std::vector<FrontierActor> actors;
    DetectionInputs detection;
    Fixture(bool foliage=false) {
        FoliageSnapshot map;map.cell_size=32.;map.enabled=foliage;
        if(foliage) {
            Volume v;v.dynamic=true;v.center={{50.,0.,0.}};
            v.half_axes={{{{10.,0.,0.}},{{0.,10.,0.}},{{0.,0.,10.}}}};
            v.strength=.5;v.radius=20.;map.instances.push_back(v);
            map.cells[{1,0}]={0};map.cells[{1,-1}]={0};
        }
        context=open(map);
        for(int i=0;i<2;++i) {
            FrontierActor a;a.key={{1,i+1}};a.identity=i+1;a.checkpoints.available=true;
            for(unsigned p=0;p<6;++p)a.checkpoints.points[p]={{0.,1.+p*.01,0.}};
            a.observer_pose.position={{i*100.,0.,0.}};a.target_pose=a.observer_pose;
            actors.push_back(a);
        }
        detection.distance=100.;detection.view_range=360.;detection.base_camouflage={{.1,.2}};
        frame();
    }
    ~Fixture(){close(context);}
    void frame(){assert(frontier_frame(context,actors,0,now));}
    FrontierReply request(const RayOracle &ray,int64_t fire=1) {
        return frontier_sight(context,actors[0].key,actors[1].key,now,fire,detection,ray);
    }
    FrontierReply wait(const RayOracle &ray,int64_t fire=1) {
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
        while(std::chrono::steady_clock::now()<deadline) {
            now+=.000001;frame();const auto r=request(ray,fire);
            if(r.status!=2)return r;
            std::this_thread::yield();
        }
        throw std::runtime_error("worker did not acknowledge");
    }
};
int main() {
    unsigned checks=0;
    {
        Fixture f;unsigned rays=0;auto ray=[&](const Ray&){++rays;return true;};
        assert(f.request(ray).status==2 && rays==0);const double sampled=f.now;
        auto r=f.wait(ray);assert(r.status==0 && r.has_detection && rays==1 && r.sampled_at==sampled);++checks;
    }
    {
        Fixture f;unsigned rays=0;auto ray=[&](const Ray&){++rays;return false;};
        f.request(ray);auto r=f.wait(ray);assert(r.status==1 && r.has_detection && !r.detected && rays==6);++checks;
    }
    {
        Fixture f(true);unsigned rays=0;f.request([](const Ray&){return true;});
        auto r=f.wait([&](const Ray&){++rays;return true;});
        assert(r.status==0 && rays==6 && r.foliage>0.);++checks;
    }
    {
        Fixture f;f.actors[0].checkpoints.available=false;f.actors[1].checkpoints.available=false;f.frame();
        unsigned rays=0;f.request([](const Ray&){return true;});
        auto r=f.wait([&](const Ray&){++rays;return false;});assert(r.status==1 && rays==1);++checks;
    }
    {
        Fixture f;f.request([](const Ray&){return true;});const auto sampled=f.now;
        f.detection.distance=101.;auto r=f.wait([](const Ray&){return true;});
        assert(r.sampled_at==sampled);++checks;
    }
    for(unsigned cause=0;cause<5;++cause) {
        Fixture f;f.request([](const Ray&){return true;});const auto sampled=f.now;
        f.now+=.1;
        if(cause==0)f.actors[1].identity+=10;
        if(cause==1)f.detection.fired_recently=true;
        if(cause==2)f.now=sampled+.75;
        if(cause==3){Update changes;assert(update(f.context,changes));}
        f.frame();unsigned rays=0;
        auto r=f.request([&](const Ray&){++rays;return true;},cause==4?2:1);
        // A foliage change is applied once the old job completes; wait handles
        // either pending-old or pending-replacement without querying stale rays.
        if(cause==3 && r.status==2) {
            r=f.wait([&](const Ray&){++rays;return true;});
            assert(r.sampled_at>sampled && rays==1);
        } else {assert(r.status==2 && rays==0);r=f.wait([&](const Ray&){++rays;return true;},cause==4?2:1);
            assert(r.sampled_at>sampled && rays==1);}
        ++checks;
    }
    {
        Fixture f(true);f.request([](const Ray&){return true;});const auto sampled=f.now;
        Update changes;changes.cells.push_back({{999,999},{}});assert(update(f.context,changes));
        auto r=f.wait([](const Ray&){return true;});assert(r.sampled_at==sampled);++checks;
    }
    {
        Fixture f;f.request([](const Ray&){return true;});const auto sampled=f.now;
        f.actors[0].observer_pose.position={{20.,0.,0.}};
        f.actors[1].target_pose.position={{120.,0.,0.}};f.frame();
        auto r=f.wait([](const Ray&ray){assert(ray.start[0]==0. && ray.end[0]==100.);return true;});
        assert(r.sampled_at==sampled);++checks;
    }
    {
        Fixture f;f.request([](const Ray&){return true;});
        auto r=f.wait([&](const Ray&){close(f.context);return true;});assert(r.status==3);++checks;
    }
    {
        Fixture f;f.request([](const Ray&){return true;});
        bool caught=false;
        try{f.wait([](const Ray&)->bool{throw std::runtime_error("local engine failure");});}
        catch(const std::runtime_error&){caught=true;}
        assert(caught);auto r=f.wait([](const Ray&){return true;});assert(r.status==0);++checks;
    }
    {
        Fixture f;f.request([](const Ray&){return true;});const auto sampled=f.now;
        f.now+=10.1;f.frame();assert(f.request([](const Ray&){return true;}).status==2);
        auto r=f.wait([](const Ray&){return true;});assert(r.sampled_at>sampled);++checks;
    }
    {
        Fixture f;f.request([](const Ray&){return true;});
        auto first=frontier_snapshot(f.context);assert(first.submitted==1 && first.pending==1 && first.completed==0);
        auto r=f.wait([](const Ray&){return true;});assert(r.status==0);
        auto done=frontier_snapshot(f.context);assert(done.completed==1 && done.pending==0 && done.cancelled==0);
        auto repeat=frontier_snapshot(f.context);assert(repeat.completed==1 && repeat.requests==done.requests);
        f.request([](const Ray&){return true;});
        FrontierActor removed=f.actors[1];removed.identity=0;
        assert(frontier_actor(f.context,removed));assert(frontier_actor(f.context,removed));
        auto cancelled=frontier_snapshot(f.context);
        assert(cancelled.submitted==2 && cancelled.completed==1 && cancelled.pending==0 && cancelled.cancelled==1);
        assert(cancelled.reasons[0]==1);++checks;
    }
    {
        Fixture f;f.actors[0].target_available=false;f.frame();
        f.request([](const Ray&){return true;});auto r=f.wait([](const Ray&){return true;});
        assert(r.status==0); // Last Effort source still observes a live target.
        auto blocked=frontier_sight(f.context,f.actors[1].key,f.actors[0].key,f.now,1,f.detection,[](const Ray&){return true;});
        assert(blocked.status==3);++checks;
    }
    {
        Fixture f;f.request([](const Ray&){return true;});
        f.actors[1].target_available=false;assert(frontier_actor(f.context,f.actors[1]));
        auto stats=frontier_snapshot(f.context);assert(stats.pending==0 && stats.cancelled==1);++checks;
    }
    for(bool legacy_first:{false,true}) {
        Fixture f;PairInput input;input.observer_checkpoints=f.actors[0].checkpoints;
        input.target_checkpoints=f.actors[1].checkpoints;input.observer=f.actors[0].observer_pose;
        input.target=f.actors[1].target_pose;input.detection=f.detection;
        assert(submit(f.context,77,input));assert(f.request([](const Ray&){return true;}).status==2);
        if(!legacy_first){auto r=f.wait([](const Ray&){return true;});assert(r.status==0);}
        std::vector<Completion> completions;
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
        while(completions.empty() && std::chrono::steady_clock::now()<deadline){completions=poll(f.context);std::this_thread::yield();}
        assert(completions.size()==1 && completions[0].job_id==77);
        if(legacy_first){auto r=f.wait([](const Ray&){return true;});assert(r.status==0);}
        VisibilityResult r;assert(reduce(f.context,77,{1},r) && r.line_of_sight);
        assert(frontier_snapshot(f.context).completed==1);++checks;
    }
    {
        Fixture f;assert(f.request([](const Ray&){return true;}).status==2);
        cancel(f.context,{-1});auto r=f.wait([](const Ray&){return true;});
        const auto stats=frontier_snapshot(f.context);
        assert(r.status==0 && stats.submitted==2 && stats.completed==1 && stats.cancelled==1 && stats.reasons[6]==1);++checks;
    }
    {
        Fixture f;unsigned clocks=0;uint64_t current_phase=0;
        auto clock=[&]{++clocks;return current_phase;};
        const auto request=[&](const RayOracle &ray){return frontier_sight(f.context,f.actors[0].key,f.actors[1].key,f.now,1,f.detection,ray,clock);};
        assert(request([](const Ray&){return true;}).status==2 && clocks==1);
        current_phase=1;assert(request([](const Ray&){return true;}).status==2 && clocks==1);
        auto expected=vehicle_check_points(f.actors[0].checkpoints,f.actors[0].observer_pose,true,0)[0];
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
        FrontierReply r;
        do{f.now+=.000001;f.frame();r=request([&](const Ray&ray){assert(ray.start==expected);return true;});std::this_thread::yield();}
        while(r.status==2 && std::chrono::steady_clock::now()<deadline);
        assert(r.status==0 && clocks==1);
        assert(request([](const Ray&){return true;}).status==2 && clocks==2);
        expected=vehicle_check_points(f.actors[0].checkpoints,f.actors[0].observer_pose,true,1)[0];
        do{f.now+=.000001;f.frame();r=request([&](const Ray&ray){assert(ray.start==expected);return true;});std::this_thread::yield();}
        while(r.status==2 && std::chrono::steady_clock::now()<deadline);
        assert(r.status==0 && clocks==2);++checks;
    }
    {
        Fixture f;bool nested=false;
        auto phase=[&]{
            if(!nested){nested=true;assert(f.request([](const Ray&){return true;}).status==2);}
            return uint64_t(0);
        };
        auto r=frontier_sight(f.context,f.actors[0].key,f.actors[1].key,f.now,1,f.detection,[](const Ray&){return true;},phase);
        assert(r.status==2 && frontier_snapshot(f.context).submitted==1);++checks;
    }
    {
        Fixture f;
        auto phase=[&]{FrontierActor removed=f.actors[1];removed.identity=0;assert(frontier_actor(f.context,removed));return uint64_t(0);};
        auto r=frontier_sight(f.context,f.actors[0].key,f.actors[1].key,f.now,1,f.detection,[](const Ray&){return true;},phase);
        assert(r.status==3 && frontier_snapshot(f.context).submitted==0);++checks;
    }
    {
        Fixture f;
        auto phase=[&]{f.frame();return uint64_t(0);};
        auto r=frontier_sight(f.context,f.actors[0].key,f.actors[1].key,f.now,1,f.detection,[](const Ray&){return true;},phase);
        assert(r.status==3 && frontier_snapshot(f.context).submitted==0);++checks;
    }
    {
        Fixture f(true);auto third=f.actors[1];third.key={{1,3}};third.identity=3;
        f.actors.push_back(third);f.frame();
        assert(f.request([](const Ray&){return true;}).status==2);
        const auto second=[&](const RayOracle &ray){return frontier_sight(f.context,f.actors[0].key,third.key,f.now,1,f.detection,ray);};
        assert(second([](const Ray&){return true;}).status==2);
        // A two-worker acknowledged barrier guarantees both preceding jobs
        // completed, without a sleep or a test-only production endpoint.
        std::atomic<unsigned> reached{0},finished{0};std::atomic<bool> release{false};
        for(unsigned i=0;i<2;++i)offline_async::schedule([&]{++reached;while(!release.load())std::this_thread::yield();++finished;});
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
        while(reached.load()!=2 && std::chrono::steady_clock::now()<deadline)std::this_thread::yield();
        assert(reached.load()==2);release=true;
        while(finished.load()!=2 && std::chrono::steady_clock::now()<deadline)std::this_thread::yield();
        assert(finished.load()==2);
        f.now+=.000001;f.frame();
        bool changed=false;unsigned first_rays=0,second_rays=0;
        auto first=f.request([&](const Ray&){
            ++first_rays;
            if(!changed){changed=true;Update u;u.inactive.insert(0);u.cells={{{1,0},{0}},{{1,-1},{0}}};assert(update(f.context,u));}
            return true;
        });
        assert(first.status==0 && first.foliage>0. && first_rays==6);
        // Pair two was already ready, but the earlier ray effect invalidates
        // its old foliage sample before any of its engine queries can run.
        assert(second([&](const Ray&){++second_rays;return true;}).status==2);
        assert(second_rays==0 && frontier_snapshot(f.context).reasons[3]==1);
        FrontierReply r;
        do{f.now+=.000001;f.frame();r=second([&](const Ray&){++second_rays;return true;});std::this_thread::yield();}
        while(r.status==2 && std::chrono::steady_clock::now()<deadline);
        assert(r.status==0 && r.foliage==0. && second_rays==1);++checks;
    }
    std::cout << "visibility frontier: " << checks << " focused contracts passed\n";
}
