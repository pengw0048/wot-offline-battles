"""Compile actual LOD distance/ranking logic with explicit camera receipts.

Matrix inputs are controlled fixtures; this does not validate Windows camera
publication, native scheduling, or the complete render pipeline.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'bigworld/stock_vehicle_visuals.cpp'

HOST = r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <map>
#include <memory>
#include <vector>
using uint32=std::uint32_t;
struct Vector3 {
    float x=0,y=0,z=0;
    Vector3 operator-(const Vector3& other)const {return{x-other.x,y-other.y,z-other.z};}
    float length()const {return std::sqrt(x*x+y*y+z*z);}
};
struct Vector4 {float x=0,y=0,z=0,w=0;};
struct Matrix {
    bool valid=true;Vector3 origin;
    bool invert(const Matrix& source) {origin=source.origin;return source.valid;}
    Vector3 applyToOrigin()const {return origin;}
    void applyPoint(Vector4& clip,Vector3 point)const {
        clip={point.x*.1f,point.y*.1f,point.z*.01f,1.f};
    }
};
namespace Moo {
struct Context {
    Matrix camera,projection;float zoom=.5f;
    const Matrix& view()const {return camera;}
    const Matrix& viewProjection()const {return projection;}
    float zoomFactor()const {return zoom;}
};
Context context;
Context& rc() {return context;}
}
struct PositionLink {Vector3 position;};
struct LinkPtr {PositionLink* value=nullptr;PositionLink* get()const{return value;}};
bool sampleStockTranslationLink(PositionLink* link,Vector3& position) {
    if(!link)return false;position=link->position;return true;
}
struct LodState {
    bool live=true,active=true,frustum=true,ownVehicle=false;uint32 priority=0;
    float distance=0;LinkPtr positionLink;
    bool valid()const {return live;}
};
struct LodGroup {
    std::vector<std::weak_ptr<LodState>> all,active;
};
std::map<uint32,LodGroup> lodGroups;
unsigned errors=0;
#define ERROR_MSG(...) (++errors)
'''

CASES = r'''
int main() {
    PositionLink first{{3,0,4}},second{{30,0,0}},third{{0,0,8}},player{{0,0,20}};
    auto a=std::make_shared<LodState>();a->positionLink.value=&first;
    auto b=std::make_shared<LodState>();b->positionLink.value=&second;
    auto c=std::make_shared<LodState>();c->positionLink.value=&third;
    auto p=std::make_shared<LodState>();p->positionLink.value=&player;p->ownVehicle=true;
    lodGroups[1].all={b,p,c,a};lodGroups[1].active={b,p,c,a};
    updateLodCalculators();
    assert(a->distance==2.5f && c->distance==4.f && p->distance==10.f && b->distance==30.f);
    assert(a->priority==1 && c->priority==2 && p->priority==0 && b->priority==3);

    // Group zero updates distance but has no ordinal priority pass.
    auto unranked=std::make_shared<LodState>();unranked->positionLink.value=&first;
    unranked->priority=17;unranked->frustum=false;
    lodGroups[0].all={unranked};lodGroups[0].active={unranked};
    updateLodCalculators();assert(unranked->distance==5.f && unranked->priority==17);

    // Original 0075ea90 stalls the complete pass for a live, inactive group.
    auto dormant=std::make_shared<LodState>();dormant->active=false;
    dormant->positionLink.value=&first;lodGroups[2].all={dormant};
    first.position={0,0,2};updateLodCalculators();assert(a->distance==2.5f);
    dormant->live=false;updateLodCalculators();
    assert(lodGroups.count(2)==0 && a->distance==1.f);

    // Missing camera publication must preserve the last real LOD outputs.
    Moo::context.camera.valid=false;first.position={0,0,4};
    updateLodCalculators();assert(a->distance==1.f && errors==1);
}
'''


class StockVehicleLodTest(unittest.TestCase):
    def test_actual_camera_and_priority_receipts(self):
        source = SOURCE.read_text()
        begin = source.index('void updateLodCalculators()')
        end = source.index('\n}\n}\n\nclass StockLodLink', begin) + len('\n}')
        program = HOST + source[begin:end] + CASES
        with tempfile.TemporaryDirectory(prefix='stock-lod-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(program)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', '-ffp-contract=off',
                            str(path / 'probe.cpp'), '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
