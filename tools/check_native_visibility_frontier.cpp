// Focused same-thread frontier contracts; no engine/game performance claim.
#include "offline_visibility.h"
#include "offline_simulation_control.h"
#include "offline_async_worker.h"
#include <atomic>
#include <cassert>
#include <chrono>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <thread>
#include <iostream>
#include <memory>
using namespace offline_visibility;
using namespace native_visibility;
struct Fixture {
    int64_t context;
    double now=1.;
    std::vector<FrontierActor> actors;
    DetectionInputs detection;
    Volume foliage_volume;
    Fixture(bool foliage=false) {
        FoliageSnapshot map;map.cell_size=32.;map.enabled=foliage;
        if(foliage) {
            Volume v;v.dynamic=true;v.center={{50.,0.,0.}};
            v.half_axes={{{{10.,0.,0.}},{{0.,10.,0.}},{{0.,0.,10.}}}};
            v.strength=.5;v.radius=20.;foliage_volume=v;map.instances.push_back(v);
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
    FrontierDetectionReply detect(const RayOracle& ray){return frontier_detect(context,actors[0].key,actors[1].key,now,1,detection,ray);}
    FrontierDetectionReply wait_detection(const RayOracle& ray) {
        const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(2);FrontierDetectionReply r;
        do{now+=.000001;frame();r=detect(ray);std::this_thread::yield();}while(r.status==2 && std::chrono::steady_clock::now()<end);
        assert(r.status!=2);return r;
    }
    void acknowledged() {
        std::atomic<unsigned> reached{0},finished{0};std::atomic<bool> release{false};
        for(unsigned i=0;i<2;++i)offline_async::schedule([&]{++reached;while(!release.load())std::this_thread::yield();++finished;});
        const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(2);
        while(reached.load()!=2 && std::chrono::steady_clock::now()<end)std::this_thread::yield();
        assert(reached.load()==2);release=true;
        while(finished.load()!=2 && std::chrono::steady_clock::now()<end)std::this_thread::yield();
        assert(finished.load()==2);now+=.000001;frame();
    }
};
// Compare the boolean result at every visibility threshold and adjacent float.
unsigned check_detection_laws() {
    unsigned laws=0;
    const std::vector<std::array<double,6>> covers={
        {{0,0,0,0,0,0}},{{.15,.15,.15,.15,.15,.15}},{{.6,.6,.6,.6,.6,.6}},
        {{.6,.45,.3,.15,0,.6}},{{.15,.6,.3,0,.45,.6}},{{.6,0,.6,0,.6,0}}};
    for(const auto& cover:covers)for(bool moving:{false,true})for(bool fired:{false,true})
    for(double view:{50.,300.,445.,520.})for(unsigned flags=0;flags<64;++flags) {
        PreparedVisibility p;p.detection.view_range=view;p.detection.base_camouflage={{.12,.25}};
        p.detection.moving=moving;p.detection.fired_recently=fired;p.detection.additive=.07;
        p.detection.multiplier=1.17;p.detection.shot_factor=.43;
        for(double c:cover){Ray r;r.foliage_bonus=c;p.rays.push_back(r);}
        std::vector<double> distances={0.,50.,51.,100.,200.,300.,445.,446.};
        for(unsigned point=0;point<6;++point) {
            std::vector<std::uint8_t> one(6,0);one[point]=1;
            const double edge=reduce_visibility(p,one).detection_distance;
            distances.push_back(std::nextafter(edge,-std::numeric_limits<double>::infinity()));
            distances.push_back(edge);distances.push_back(std::nextafter(edge,std::numeric_limits<double>::infinity()));
        }
        for(double distance:distances) {
            p.detection.distance=distance;
            std::vector<std::uint8_t> original;
            for(unsigned i=0;i<6;++i) {
                const bool clear=(flags&(1u<<i))!=0;original.push_back(clear);
                if(should_stop(p,i,clear))break;
            }
            const bool expected=reduce_visibility(p,original).detected;
            bool detected=distance<=50.;
            for(unsigned i=0;i<6;++i) {
                if(!can_detect_with_foliage(p.detection,p.rays[i].foliage_bonus))continue;
                if(flags&(1u<<i)){detected=true;break;}
            }
            assert(detected==expected);++laws;
        }
    }
    return laws;
}

// Detection receipts must never masquerade as complete LOS/foliage geometry.
unsigned check_detection_contracts() {
    {
        Fixture f(true);
        auto human=f.actors[0];human.key={{0,3}};human.identity=3;
        f.actors.push_back(human);f.frame();
        const auto context=f.context;auto &actors=f.actors;
        double &now=f.now;DetectionInputs &d=f.detection;
        unsigned rays=0;auto clear=[&](const Ray&){++rays;return true;};
        auto detect=[&](const ActorKey& observer,const DetectionInputs& input,const RayOracle& ray){
            return frontier_detect(context,observer,actors[1].key,now,1,input,ray);};
        auto wait=[&](const ActorKey& observer,const DetectionInputs& input,const RayOracle& ray){
            const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);FrontierDetectionReply r;
            do{now+=.000001;assert(frontier_frame(context,actors,0,now));r=detect(observer,input,ray);std::this_thread::yield();}
            while(r.status==2 && std::chrono::steady_clock::now()<deadline);assert(r.status==0);return r;};
        assert(detect(actors[0].key,d,clear).status==2);auto positive=wait(actors[0].key,d,clear);
        assert(positive.detected && rays==1 && frontier_snapshot(context).pending==0);
        // A generic consumer cannot inherit an incomplete LOS/cover result.
        rays=0;auto generic=frontier_sight(context,actors[0].key,actors[1].key,now,1,d,clear);assert(generic.status==2);
    const auto generic_deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
    do{now+=.000001;assert(frontier_frame(context,actors,0,now));generic=frontier_sight(context,actors[0].key,actors[1].key,now,1,d,clear);std::this_thread::yield();}
    while(generic.status==2 && std::chrono::steady_clock::now()<generic_deadline);
        assert(generic.status==0 && generic.foliage==.5 && rays==6);
        // A separate human observer has an independent profile and job.
        DetectionInputs weak=d;weak.distance=200.;rays=0;
        assert(detect(actors[2].key,weak,clear).status==2);auto negative=wait(actors[2].key,weak,clear);
        assert(!negative.detected && rays==0);
        // Changing the profile while pending invalidates the frozen weak result.
        assert(detect(actors[2].key,weak,clear).status==2);weak.view_range=600.;now+=.01;
        rays=0;auto changed=detect(actors[2].key,weak,clear);
        assert(changed.status==0 && changed.detected && rays==1);
        // A relevant ray failure still escapes to the existing per-pair boundary.
        assert(detect(actors[0].key,d,clear).status==2);bool caught=false;
        try{wait(actors[0].key,d,[](const Ray&)->bool{throw std::runtime_error("query failed");});}
        catch(const std::runtime_error&){caught=true;}assert(caught);
        close(context);
    }
    unsigned lifecycle=0;
    {
        Fixture f(true);assert(f.detect([](const Ray&){return true;}).status==2);f.acknowledged();
        auto r=f.detect([&](const Ray&){close(f.context);return true;});
        assert(r.status==3 && !r.detected);++lifecycle;
    }
    {
        Fixture f(true);assert(f.detect([](const Ray&){return true;}).status==2);f.acknowledged();
        unsigned old_calls=0;
        auto r=f.detect([&](const Ray&){++old_calls;++f.actors[1].identity;
            assert(frontier_actor(f.context,f.actors[1]));
            assert(f.detect([](const Ray&){return true;}).status==2);return true;});
        assert(r.status==3 && !r.detected && old_calls==1);
        assert(frontier_snapshot(f.context).pending==1 && frontier_snapshot(f.context).completed==0);
        unsigned next_calls=0;r=f.wait_detection([&](const Ray&){++next_calls;return true;});
        assert(r.status==0 && r.detected && next_calls==1 && frontier_snapshot(f.context).pending==0);++lifecycle;
    }
    {
        Fixture f(true);f.detection.distance=200.;assert(f.detect([](const Ray&){return true;}).status==2);f.acknowledged();
        f.foliage_volume.strength=0.;Update change;change.instances.push_back({0,f.foliage_volume});
        change.cells.push_back({{1,0},{0}});change.cells.push_back({{1,-1},{0}});assert(update(f.context,change));
        unsigned calls=0;auto r=f.detect([&](const Ray&){++calls;return true;});
        assert(r.status==0 && r.detected && calls==1 && frontier_snapshot(f.context).reasons[3]==1);++lifecycle;
    }
    {
        Fixture f(true);assert(f.detect([](const Ray&){return true;}).status==2);f.acknowledged();
        f.actors[1].target_available=false;assert(frontier_actor(f.context,f.actors[1]));unsigned calls=0;
        auto r=f.detect([&](const Ray&){++calls;return true;});assert(r.status==3 && !r.detected && calls==0);
        f.actors[1].target_available=true;assert(frontier_actor(f.context,f.actors[1]));
        r=f.wait_detection([&](const Ray&){++calls;return true;});assert(r.status==0 && r.detected && calls==1);++lifecycle;
    }
    {
        Fixture f(true);assert(f.detect([](const Ray&){return true;}).status==2);f.acknowledged();
        f.detection.distance=300.;auto r=f.detect([](const Ray&){return true;});
        assert(r.status==0 && r.detected && r.sampled_at==1.);++lifecycle;
    }
    {
        Fixture f(true);assert(f.detect([](const Ray&){return true;}).status==2);f.acknowledged();unsigned calls=0;
        auto r=frontier_sight(f.context,f.actors[0].key,f.actors[1].key,f.now,1,f.detection,
            [&](const Ray&){++calls;return true;});
        assert(r.status==0 && r.has_detection && r.foliage==.5 && calls==6);++lifecycle;
    }
    return 5+lifecycle;
}

struct WorkerGate {
    std::atomic<unsigned> reached{0},finished{0};
    std::atomic<bool> release{false};
    WorkerGate() {
        for(unsigned i=0;i<2;++i)offline_async::schedule([this]{
            ++reached;while(!release.load())std::this_thread::yield();++finished;});
        const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(2);
        while(reached.load()!=2 && std::chrono::steady_clock::now()<end)std::this_thread::yield();
        assert(reached.load()==2);
    }
    void finish() {
        release=true;
        const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(2);
        while(finished.load()!=2 && std::chrono::steady_clock::now()<end)std::this_thread::yield();
        assert(finished.load()==2);
    }
    ~WorkerGate(){finish();}
};
unsigned check_fresh_contracts() {
    unsigned checks=0;
    for(bool ready:{false,true})for(unsigned reason:{1,2,3,4}) {
        if(reason==3 && !ready)continue;
        Fixture f(true);
        std::unique_ptr<WorkerGate> gate;
        if(!ready)gate.reset(new WorkerGate);
        assert(f.detect([](const Ray&){return true;}).status==2);
        if(ready)f.acknowledged();
        const auto before=frontier_snapshot(f.context);
        f.now+=reason==4?.8:.1;
        f.actors[0].observer_pose.position[0]=20.;
        f.actors[1].target_pose.position[0]=120.;f.frame();
        if(reason==2)f.detection.view_range=370.;
        if(reason==3){Update u;f.foliage_volume.strength=0.;u.instances.push_back({0,f.foliage_volume});
            u.cells.push_back({{1,0},{0}});assert(update(f.context,u));}
        unsigned rays=0;
        auto r=frontier_detect(f.context,f.actors[0].key,f.actors[1].key,f.now,
            reason==1?2:1,f.detection,[&](const Ray& ray){
                ++rays;assert(ray.start[0]==20. && ray.end[0]==120.);
                const auto live=frontier_snapshot(f.context);
                assert(live.pending==1 && live.submitted==before.submitted+1);
                if(reason==3)assert(ray.foliage_bonus==0.);
                return true;});
        assert(r.status==0 && r.detected && r.sampled_at==f.now && rays==1);
        auto after=frontier_snapshot(f.context);
        assert(after.pending==0 && after.completed==before.completed+1 && after.reasons[reason]==1);
        assert(after.worker_seconds==before.worker_seconds);
        if(gate){gate->finish();gate.reset();f.acknowledged();}
        after=frontier_snapshot(f.context);
        assert(after.pending==0 && after.completed==before.completed+1);
        ++checks;
    }
    // The same lifetime guards also apply when preparation is synchronous.
    for(unsigned action=0;action<4;++action) {
        Fixture f;assert(f.detect([](const Ray&){return true;}).status==2);f.acknowledged();
        f.now+=.8;f.frame();unsigned rays=0;bool threw=false;
        try {
            auto r=f.detect([&](const Ray&){++rays;
                if(action==0)close(f.context);
                if(action==1){++f.actors[1].identity;assert(frontier_actor(f.context,f.actors[1]));
                    assert(f.detect([](const Ray&){return true;}).status==2);}
                if(action==2)throw std::runtime_error("local query failed");
                if(action==3){auto inner=f.detect([](const Ray&){return true;});assert(inner.status==0);}
                return true;});
            assert(r.status==3 && !r.detected);
        }catch(const std::runtime_error&){assert(action==2);threw=true;}
        assert(rays==1);
        if(action==1){auto r=f.wait_detection([](const Ray&){return true;});assert(r.status==0 && r.detected);}
        if(action==2){assert(threw);auto r=f.detect([](const Ray&){return true;});assert(r.status==0 && r.detected);}
        if(action!=0){auto snapshot=frontier_snapshot(f.context);assert(snapshot.pending==0 && snapshot.completed==1);}
        ++checks;
    }
    // A phase callback may retire the context, frame, or actors before install.
    for(unsigned action=0;action<4;++action) {
        Fixture f;assert(f.detect([](const Ray&){return true;}).status==2);f.acknowledged();
        f.now+=.8;f.frame();unsigned rays=0;
        auto r=frontier_detect(f.context,f.actors[0].key,f.actors[1].key,f.now,1,f.detection,
            [&](const Ray&){++rays;return true;},[&](){
                if(action==0)close(f.context);
                if(action==1)f.frame();
                if(action==2){++f.actors[1].identity;assert(frontier_actor(f.context,f.actors[1]));}
                if(action==3)assert(f.detect([](const Ray&){return true;}).status==2);
                return uint64_t(0);});
        assert(r.status==(action==3?2u:3u) && !r.detected && rays==0);
        if(action!=0){auto s=frontier_snapshot(f.context);assert(s.completed==0 && s.pending==(action==3?1u:0u));}
        ++checks;
    }
    // A reused actor key never makes an identity replacement a fresh shortcut.
    {
        Fixture f;assert(f.detect([](const Ray&){return true;}).status==2);f.acknowledged();
        ++f.actors[1].identity;f.now+=.8;f.frame();unsigned rays=0;
        assert(f.detect([&](const Ray&){++rays;return true;}).status==2 && rays==0);
        assert(f.wait_detection([&](const Ray&){++rays;return true;}).status==0 && rays==1);++checks;
    }
    // Explicit preparation permission cannot consume even an already-ready job.
    for(bool stale:{false,true}) {
        Fixture f;assert(f.detect([](const Ray&){return true;}).status==2);f.acknowledged();
        if(stale){f.now+=.8;f.frame();}
        unsigned rays=0;
        auto r=frontier_detect(f.context,f.actors[0].key,f.actors[1].key,f.now,1,f.detection,
            [&](const Ray&){++rays;return true;},PhaseOracle(),false);
        assert(r.status==2 && rays==0 && frontier_snapshot(f.context).pending==1);
        f.acknowledged();r=f.detect([&](const Ray&){++rays;return true;});
        assert(r.status==0 && r.detected && rays==1);++checks;
    }
    return checks;
}

namespace control_budget_check {
namespace sim=offline_simulation;
namespace ctl=offline_simulation::control;
namespace vis=offline_visibility;
namespace nv=native_visibility;

struct Fixture {
    ctl::Store store;
    int64_t context;
    double now=1.;
    int sequence=0;
    unsigned rays=0, world_slots=0, prepare_slots=0;
    std::vector<sim::ActorKey> order;
    std::vector<ctl::ActorSample> samples;
    std::vector<vis::FrontierActor> actors;
    Fixture(unsigned targets=64) {
        nv::FoliageSnapshot foliage;foliage.enabled=false;
        context=vis::open(foliage);
        std::vector<ctl::ActorConfig> configs;
        for(unsigned i=0;i<=targets;++i) {
            sim::ActorKey key{sim::ActorKind::Bot,int64_t(i+1)};
            ctl::ActorConfig config;config.key=key;config.team=i?2:1;
            config.view_moving=config.view_still=400.;config.radio=0.;
            configs.push_back(config);order.push_back(key);
            ctl::ActorSample sample;sample.key=key;sample.alive=true;
            sample.decision_due=(i==0);sample.fire_sequence=0;
            sample.pose.position={{i?100.+i*.01:0.,0.,0.}};samples.push_back(sample);
            vis::FrontierActor actor;actor.key={{1,int64_t(i+1)}};actor.identity=i+1;
            actor.checkpoints.available=true;actor.target_available=true;
            for(unsigned j=0;j<6;++j)actor.checkpoints.points[j]={{0.,1.+j*.01,0.}};
            actor.observer_pose.position=sample.pose.position;
            actor.target_pose=actor.observer_pose;actors.push_back(actor);
        }
        store.configure(configs);
    }
    ~Fixture(){vis::close(context);}
    void frame(double time,double distance=100.) {
        now=time;rays=world_slots=prepare_slots=0;
        for(size_t i=1;i<samples.size();++i) {
            samples[i].pose.position={{distance+i*.01,0.,0.}};
            actors[i].observer_pose.position=samples[i].pose.position;
            actors[i].target_pose=actors[i].observer_pose;
        }
        store.update(samples);
        store.begin(sim::TickToken{1,1,++sequence,0},order,48,true,false,now);
        assert(vis::frontier_frame(context,actors,0,now));
    }
    ctl::SightReply probe(const ctl::SightRequest &r) {
        if(r.world_query)++world_slots;else ++prepare_slots;
        nv::DetectionInputs d;d.distance=r.detection.distance;d.view_range=r.detection.view;
        d.base_camouflage={{r.detection.camo_moving,r.detection.camo_still}};
        d.moving=r.detection.moving;d.fired_recently=r.detection.fired;
        d.additive=r.detection.additive;d.multiplier=r.detection.multiplier;d.shot_factor=r.detection.shot_factor;
        auto result=vis::frontier_detect(context,{{int64_t(r.observer.kind),r.observer.id}},
            {{int64_t(r.target.kind),r.target.id}},r.now,r.fire_sequence,d,
            [&](const nv::Ray&){assert(r.world_query);++rays;return true;},vis::PhaseOracle(),r.world_query);
        ctl::SightReply out;out.status=result.status==0?sim::QueryStatus::DetectionComplete:static_cast<sim::QueryStatus>(result.status);
        out.has_detection=result.status==0;out.detected=result.detected;out.sampled_at=result.sampled_at;return out;
    }
    void contacts() {
        store.contacts(order[0],[&](const ctl::SightRequest &r){return probe(r);});
        assert(world_slots<=48 && rays<=48);
        store.finish();
    }
    void ready() {
        // Both FIFO workers acknowledge all older prepare operations.
        std::atomic<unsigned> reached{0},done{0};std::atomic<bool> release{false};
        for(unsigned i=0;i<2;++i)offline_async::schedule([&]{++reached;while(!release.load())std::this_thread::yield();++done;});
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
        while(reached.load()!=2 && std::chrono::steady_clock::now()<deadline)std::this_thread::yield();
        assert(reached.load()==2);release=true;
        while(done.load()!=2 && std::chrono::steady_clock::now()<deadline)std::this_thread::yield();
        assert(done.load()==2);
    }
};

unsigned run() {
    for(bool changed:{false,true}) {
        Fixture f;
        f.frame(1.);f.contacts();
        assert(f.rays==0 && f.prepare_slots==48);
        f.ready();
        // A temporary loss of range clears Store inflight but keeps frontier
        // geometry ready, reproducing the budget ownership mismatch.
        f.frame(1.2,500.);f.contacts();assert(f.rays==0);
        if(changed) for(size_t i=1;i<f.samples.size();++i)f.samples[i].fire_sequence=1;
        f.frame(1.4);f.contacts();
        assert(f.prepare_slots==48 && f.world_slots==0 && f.rays==0);
        f.ready();
        // Subsequent admissions own real query budget, including a changed
        // ready job that requires a fresh synchronous preparation.
        for(unsigned i=0;i<4;++i) {
            for(size_t j=1;j<f.samples.size();++j)f.samples[j].fire_sequence+=1;
            f.frame(1.6+i*.2);f.contacts();
            assert(f.rays<=48 && f.world_slots<=48);
            if(i==0)assert(f.rays==48);
            f.ready();
        }
    }
    // A restored perception checkpoint intentionally lacks native inflight
    // ownership, while the frontier may still own the ready receipt.
    {
        Fixture f(1);f.frame(1.);f.contacts();f.ready();
        const auto snapshot=f.store.snapshot();f.store.restore(snapshot);
        f.frame(1.2);f.contacts();assert(f.world_slots==0 && f.rays==0 && f.prepare_slots==1);
        f.frame(1.4);f.contacts();assert(f.world_slots==1 && f.rays==1);
    }
    return 3;
}

}

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
    const auto fresh_contracts=check_fresh_contracts();
    const auto budget_contracts=control_budget_check::run();
    const auto detection_laws=check_detection_laws();
    const auto detection_contracts=check_detection_contracts();
    std::cout << "visibility frontier: " << checks << " generic contracts, "
              << detection_contracts << " detection contracts, "
              << fresh_contracts << " fresh lifecycle contracts, "
              << budget_contracts << " control budget groups, "
              << detection_laws << " truth-table cases passed\n";
}
