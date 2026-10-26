"""Compile the actual suspension update against a bounded scene-query host.

The host checks node/receipt/teardown contracts. Actual public-engine scene
queries and Windows visual results remain a separate validation boundary.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'bigworld/stock_vehicle_visuals.cpp'

HOST = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <vector>
using uint32=std::uint32_t;
struct Vector3 {
    float x=0,y=0,z=0;
    Vector3()=default;Vector3(float a,float b,float c):x(a),y(b),z(c){}
    Vector3 operator+(const Vector3& b) const { return {x+b.x,y+b.y,z+b.z}; }
    Vector3 operator*(float b) const { return {x*b,y*b,z*b}; }
};
struct Matrix {
    Vector3 rows[4]={{1,0,0},{0,1,0},{0,0,1},{0,0,0}};
    Vector3& operator[](unsigned i) { return rows[i]; }
    const Vector3& operator[](unsigned i) const { return rows[i]; }
    Vector3 applyToOrigin() const { return rows[3]; }
    Vector3 applyVector(Vector3 v) const {
        return rows[0]*v.x+rows[1]*v.y+rows[2]*v.z;
    }
    void translation(Vector3 v) { rows[3]=v; }
    void multiply(const Matrix& a,const Matrix& b) {
        for(unsigned i=0;i<3;++i) rows[i]=b.applyVector(a.rows[i]);
        rows[3]=b.applyVector(a.rows[3])+b.rows[3];
    }
};
struct BoundingBox {
    Vector3 min={-2,-1,-3},max={2,2,3};bool invalid=false;
    bool insideOut() const { return invalid; }
    Vector3 minBounds() const { return min; }Vector3 maxBounds() const { return max; }
};
struct PyObject { virtual ~PyObject()=default; };
struct Model : PyObject {
    bool valid=true;std::map<std::string,Matrix> authored,local;
    unsigned writes=0;
};
struct PyObjectPtr {
    explicit PyObjectPtr(PyObject* p=nullptr):value(p){}
    explicit operator bool()const { return value!=nullptr; }
    PyObject* get()const { return value; }PyObject* value;
};
struct WeakCompound {
    Model* value=nullptr;const PyObject* getPyObj()const { return value; }
};
struct StockLodLink : PyObject {
    bool valid=true;uint32 priority=0;float distance=0;
    bool sample(uint32& p,float& d)const { if(!valid)return false;p=priority;d=distance;return true; }
};
bool stockVehicleCompoundContext(PyObject* p,uint32& space,BoundingBox&) {
    space=7;return static_cast<Model*>(p)->valid;
}
bool stockVehicleCompoundNode(PyObject* p,const std::string& name,Matrix& authored,Matrix& parent,Matrix& world) {
    auto& model=*static_cast<Model*>(p);
    if(!model.valid || !model.authored.count(name)) return false;
    authored=model.authored.at(name);parent=Matrix{};world=model.local.at(name);return true;
}
bool writeStockVehicleCompoundNodeLocal(PyObject* p,const std::string& name,const Matrix& value) {
    auto& model=*static_cast<Model*>(p);if(!model.valid)return false;
    model.local.at(name)=value;++model.writes;return true;
}
namespace wot {
using V=Vector3;
struct VehicleCollisionBounds { V minimum,maximum; };
struct VehicleCollisionRay { V origin,direction;float length; };
struct VehicleCollisionRayHit { float distance=0;V normal;bool is_terrain=false; };
struct VehicleCollisionProvider {
    bool ready=true;unsigned calls=0;std::vector<bool> hits;std::vector<float> distances;
    std::function<void()> during;
    bool prepare(const VehicleCollisionBounds& bounds,uint32 space,uint32 visibility) {
        assert(bounds.minimum.y==-2 && space==7 && visibility==UINT32_MAX);calls=0;return ready;
    }
    bool query_ray(const VehicleCollisionRay& ray,VehicleCollisionRayHit& hit) {
        assert(ray.direction.x==0 && ray.direction.y==-1 && ray.direction.z==0 && ray.length==1);
        assert(std::fabs(ray.origin.y-.8f)<1e-6);
        unsigned index=calls++;if(during)during();
        if(hits.at(index)) {hit.distance=distances.at(index);return true;}return false;
    }
};
}
struct SuspensionNode {
    std::string name;Matrix authored;float minimum,maximum,offset;bool left;
};
struct SuspensionState {
    WeakCompound compound;std::vector<SuspensionNode> nodes;PyObjectPtr lodLink;
    float thickness=.1f,maxDistance=100;uint32 maxPriority=3;
    unsigned flags=0;bool active=true,wasEligible=false,valid_=true;unsigned stopped=0;
    void deactivate() {active=false;++stopped;}
    void update(float elapsed,wot::VehicleCollisionProvider& provider);
};
static unsigned errors=0;
#define ERROR_MSG(...) (++errors)
'''

CASES = r'''
int main() {
    Model model;
    Matrix left,right;left.translation({-1,0,0});right.translation({1,0,0});
    model.authored={{"left",left},{"right",right}};model.local=model.authored;
    SuspensionState state;state.compound.value=&model;
    state.nodes={{"left",left,-.2f,.3f,9.f,true},{"right",right,-.2f,.3f,9.f,false}};
    wot::VehicleCollisionProvider query;query.hits={true,false};query.distances={.4f,0};
    state.update(.1f,query);
    assert(state.nodes[0].offset==.1f && state.nodes[1].offset==-.1f);
    assert(state.flags==8 && model.local.at("left").rows[3].y==.1f);
    assert(model.local.at("right").rows[3].y==-.1f && model.writes==2);

    state.flags|=1;state.update(.1f,query);
    assert(state.nodes[0].offset==.2f && model.local.at("left").rows[3].y==.1f);
    assert(state.flags==9 && query.calls==2 && model.writes==3);

    StockLodLink lod;lod.distance=200;state.lodLink=PyObjectPtr(&lod);
    state.update(.1f,query);assert(!state.wasEligible && model.writes==3);
    lod.distance=10;state.update(.1f,query);
    assert(state.wasEligible && state.nodes[0].offset==.1f && state.nodes[1].offset==-.1f);

    // Failure preparing a real scene is not a no-hit/airborne receipt.
    unsigned flags=state.flags,writes=model.writes;query.ready=false;
    state.update(.1f,query);
    assert(!state.active && state.flags==flags && model.writes==writes && state.stopped==1);

    // Destroyed during the query: the local candidate must never be committed.
    query.ready=true;state.active=true;state.valid_=true;
    query.during=[&] {state.valid_=false;state.nodes.clear();};
    state.update(.1f,query);assert(model.writes==writes && state.flags==flags);
    assert(errors>=1);
}
'''


class StockSuspensionPresentationTest(unittest.TestCase):
    def test_actual_update_receipts(self):
        source = SOURCE.read_text()
        begin = source.index('void SuspensionState::update(')
        end = source.index('\nvoid updateSuspensions(', begin)
        program = HOST + source[begin:end] + CASES
        with tempfile.TemporaryDirectory(prefix='stock-suspension-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(program)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', '-ffp-contract=off',
                            str(path / 'probe.cpp'), '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
