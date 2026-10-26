"""Compile original recoil curve and actual manager publication bodies.

This tests frame publication, cancellation, and authored basis preservation.
The small host does not stand in for the Python C ABI or Windows renderer.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VehicleRecoilTest(unittest.TestCase):
    def test_original_curve_and_matrix_publication(self):
        source = (ROOT / 'bigworld/stock_vehicle_animators.cpp').read_text()
        begin = source.index('struct VehicleRecoilCurve\n')
        end = source.index('\nstruct RecoilState', begin)
        manager_begin = source.index('void tickStockVehicleRecoil(')
        manager_end = source.index('\nnamespace\n{\nclass RecoilScriptShutdown', manager_begin)
        host = r'''
#include <cassert>
#include <cmath>
#include <memory>
#include <vector>
'''
        state = r'''
struct Matrix {
    float values[4][4]{};
    float* operator[](unsigned i) { return values[i]; }
};
struct Link { float value=0; };
struct LinkPtr {
    Link* ptr=nullptr;
    Link* get() { return ptr; }
};
struct RecoilState {
    VehicleRecoilCurve curve{0.25f,0.75f,2.f,100.f};
    Matrix basis,animated;
    LinkPtr lodLink;
    bool active=true,alive=true;
    bool valid() const { return alive; }
    void deactivate() { active=false; }
};
std::vector<std::weak_ptr<RecoilState>> activeRecoil;
unsigned samples=0,errors=0;
bool sampleStockFloatLink(Link* link,float& value) {
    ++samples;if(!link)return false;value=link->value;return true;
}
#define ERROR_MSG(...) (++errors)
'''
        cases = r'''
int main() {
    VehicleRecoilCurve curve(0.25f,0.75f,2.f,100.f);
    assert(curve.advance(0.25f,0.f)==0.f);
    curve.recoil();
    assert(std::fabs(curve.advance(0.25f,0.f)-2.f)<0.000001f);
    assert(std::fabs(curve.advance(0.375f,0.f)-1.41421356f)<0.000001f);
    curve.recoil();
    assert(curve.elapsed==0.f);
    assert(curve.advance(0.1f,101.f)==0.f && !curve.recoiling);
    assert(curve.advance(0.1f,0.f)==0.f);

    auto value=std::make_shared<RecoilState>();
    Link lod;value->lodLink.ptr=&lod;
    value->basis[0][0]=3.f;value->basis[3][0]=5.f;value->basis[3][2]=7.f;
    activeRecoil.push_back(value);
    value->curve.recoil();
    tickStockVehicleRecoil(0.25f);
    assert(value->animated[0][0]==3.f && value->animated[3][0]==5.f);
    assert(std::fabs(value->animated[3][2]-5.f)<0.000001f);
    assert(value->basis[3][2]==7.f);
    // Every frame starts with the basis, so recoil never accumulates twice.
    tickStockVehicleRecoil(0.f);
    assert(std::fabs(value->animated[3][2]-5.f)<0.000001f);

    // Duration cancellation occurs before consulting the LOD functor.
    value->lodLink.ptr=nullptr;
    const unsigned previousSamples=samples;
    tickStockVehicleRecoil(1.f);
    assert(!value->curve.recoiling && value->active && errors==0);
    assert(samples==previousSamples && value->animated[3][2]==7.f);
    // Missing required LOD is a local presentation failure, not fake motion.
    value->curve.recoil();
    tickStockVehicleRecoil(0.1f);
    assert(!value->active && errors==1 && value->animated[3][2]==7.f);
    value->active=true;value->alive=false;
    value->animated[3][2]=13.f;
    tickStockVehicleRecoil(0.1f);
    assert(value->animated[3][2]==13.f);
}
'''
        with tempfile.TemporaryDirectory(prefix='stock-recoil-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(
                host + source[begin:end] + state + source[manager_begin:manager_end] + cases)
            subprocess.run([
                os.environ.get('CXX', 'clang++'), '-std=c++17', '-Wall',
                '-Wextra', '-Werror', '-ffp-contract=off', str(path / 'probe.cpp'),
                '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
