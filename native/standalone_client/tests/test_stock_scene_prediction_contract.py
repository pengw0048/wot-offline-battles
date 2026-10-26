"""Compile actual bridge bodies against the public collide callback contract.

This tests the adapter's filtering/ownership boundary; it does not prove a
Windows engine query or the unavailable original destructible metadata.
"""
from pathlib import Path
import os
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1] / 'bigworld/stock_scene_prediction.cpp'

PRELUDE = r'''
#include <cassert>
#include <cmath>
#include <cstddef>
#include <functional>
#include <memory>
#include <vector>
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
const int COLLIDE_STOP=0,COLLIDE_BEFORE=1,COLLIDE_ALL=3;
using SpaceID=int;
struct Vector3 { float x=0,y=0,z=0; };
struct WorldTriangle {
    unsigned flag,material;
    unsigned flags() const { return flag; }
    unsigned materialKind() const { return material; }
};
struct CollisionObstacle { bool moving=false;bool identified=false;bool allowed=false;bool failure=false; };
struct StockObstacleIdentity { unsigned chunkID=0,itemIndex=0;float scale=1; };
class CollisionCallback {
public:
    virtual ~CollisionCallback()=default;
    virtual int operator()(const CollisionObstacle&,const WorldTriangle&,float)=0;
};
struct Filter {};
struct Entity { int space=1;int spaceID() const { return space; }
    Filter* pFilter() const { return nullptr; } };
using EntityPtr=std::shared_ptr<Entity>;
struct PyEntity {
    bool destroyed=false;
    EntityPtr entity=std::make_shared<Entity>();
    bool isDestroyed() const { return destroyed; }
    EntityPtr pEntity() const { return entity; }
};
template<class T> struct WeakPyPtr {
    T* value=nullptr;
    const T* get() const { return value; }
};
struct PyObjectPtr { explicit PyObjectPtr(PyEntity*) {} };
static bool pythonError=false;
const int PyExc_RuntimeError=1,PyExc_ValueError=2;
int PyErr_Occurred() { return pythonError; }
void PyErr_SetString(int,const char*) { pythonError=true; }
struct Event { unsigned flags,material;float distance;bool moving=false,identified=false,allowed=false,failure=false; };
struct ClientSpace {
    bool cleared_=false;
    std::vector<std::vector<Event>> edges;
    std::size_t calls=0;
    std::function<void()> during;
    bool cleared() const { return cleared_; }
    int id() const { return 1; }
    float collide(const Vector3&,const Vector3&,CollisionCallback& callback) {
        float nativeDistance=-1;
        const auto events=edges.at(calls++);
        for (const auto& event:events) {
            // BSPSweepVisitor/TerrainRayCollisionCallback assign dist before
            // the filter. Returning COLLIDE_ALL leaves this receipt in place.
            nativeDistance=event.distance;
            const int response=callback(CollisionObstacle{event.moving,event.identified,event.allowed,event.failure},
                WorldTriangle{event.flags,event.material},event.distance);
            if (response==COLLIDE_STOP) break;
        }
        if (during) during();
        return nativeDistance;
    }
};
using ClientSpacePtr=std::shared_ptr<ClientSpace>;
struct SpaceManager {
    ClientSpacePtr value;
    static SpaceManager& instance() { static SpaceManager manager;return manager; }
    ClientSpacePtr space(int) { return value; }
};
static bool callbackAllowed=false,callbackFailure=false;
static unsigned callbackCalls=0;
bool readStockObstacleIdentity(const CollisionObstacle& obstacle,StockObstacleIdentity&) {
    callbackAllowed=obstacle.allowed;callbackFailure=obstacle.failure;
    return obstacle.identified;
}
bool mayBreakDestructible(const PyEntity*,const StockObstacleIdentity&,
    unsigned,const Vector3&,bool& allowed) {
    ++callbackCalls;
    if (callbackFailure) { PyErr_SetString(PyExc_RuntimeError,"Callback failed");return false; }
    allowed=callbackAllowed;return true;
}
'''

CASES = r'''
int main() {
    PyEntity entity;
    WeakPyPtr<PyEntity> owner{&entity};
    Vector3 edges[8]{};
    auto query=[&](std::vector<std::vector<Event>> events,bool initial=false) {
        pythonError=false;
        auto space=std::make_shared<ClientSpace>();space->edges=events;
        SpaceManager::instance().value=space;
        bool blocked=initial;
        const bool success=queryStockScenePrediction(1,edges,owner,{},blocked);
        return std::make_pair(success,blocked);
    };
    auto ignored=std::vector<Event>{{0x10,0,1},{0x40,0,2},{0,87,3},{0,100,4}};
    assert(query({ignored,ignored,ignored,ignored})==std::make_pair(true,false));
    assert(SpaceManager::instance().value->calls==4);
    assert(query({{{0x8,0,1}}})==std::make_pair(true,true)); // Real terrain surface.
    assert(query({{{0,0,1,true}}})==std::make_pair(true,true)); // No blanket moving skip.
    assert(query({{{0x80,0,1}}})==std::make_pair(true,true)); // Not in original 0x50 mask.
    assert(query({{},{{0,101,2}}})==std::make_pair(true,true));
    assert(SpaceManager::instance().value->calls==2);
    for (unsigned material:{71u,72u,73u,86u}) {
        assert(query({{{0,material,1}}})==std::make_pair(false,false));
        assert(pythonError);
        assert(query({{{0,material,1}}},true)==std::make_pair(false,true));
    }
    const auto breakable=std::vector<Event>{{0,71,1,false,true,true,false}};
    assert(query({breakable,breakable,breakable,breakable})==std::make_pair(true,false));
    assert(callbackCalls==4);
    assert(query({{{0,71,1,false,true,false,false}}})==std::make_pair(true,true));
    assert(query({{{0,71,1,false,true,false,true}}})==std::make_pair(false,false));
    pythonError=false;
    auto space=std::make_shared<ClientSpace>();space->edges={{}};
    SpaceManager::instance().value=space;
    space->during=[&]{entity.destroyed=true;};
    bool blocked=true;
    assert(!queryStockScenePrediction(1,edges,owner,{},blocked));
    assert(blocked && pythonError);
    entity.destroyed=false;pythonError=false;
    entity.entity->space=2;
    assert(!queryStockScenePrediction(1,edges,owner,{},blocked));
    entity.entity->space=1;pythonError=false;
    edges[7].z=INFINITY;
    assert(!queryStockScenePrediction(1,edges,owner,{},blocked));
    edges[7].z=0;pythonError=false;
    space->cleared_=true;
    assert(!queryStockScenePrediction(1,edges,owner,{},blocked));
    pythonError=false;owner.value=nullptr;
    assert(!queryStockScenePrediction(1,edges,owner,{},blocked));
}
'''


def main():
    source = SOURCE.read_text()
    body = ('namespace {\n' + source[source.index('bool finite('):source.index('bool mayBreakDestructible(')] +
            source[source.index('class PredictionEdgeHit'):])
    with tempfile.TemporaryDirectory(prefix='wot-prediction-contract-') as directory:
        path = Path(directory)
        (path / 'check.cpp').write_text(PRELUDE + body + CASES)
        executable = str(path / 'check')
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra',
                        '-Werror', str(path / 'check.cpp'), '-o', executable], check=True)
        subprocess.run([executable], check=True)
    print('Actual prediction bridge filtering, four-edge ordering and owner failure checks passed')


if __name__ == '__main__':
    main()
