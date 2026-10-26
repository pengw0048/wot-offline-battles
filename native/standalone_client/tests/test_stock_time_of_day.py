"""Exercise the production day/night reader and sun transform fragments."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'stock_time_patch', ROOT / 'bigworld/patch_stock_time_of_day.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


class StockTimeOfDayTest(unittest.TestCase):
    def test_original_renderer_selection_and_matrix_order(self):
        host = r'''
#include <array>
#include <cassert>
#include <cmath>
#include <map>
#include <string>
#include <vector>
namespace BW { using string=std::string; }
struct IRendererPipeline { enum { TYPE_DEFERRED_SHADING, TYPE_FORWARD_SHADING }; };
struct Renderer {
    int mode=0;
    static Renderer& instance(){static Renderer value;return value;}
    int pipelineType()const{return mode;}
};
struct Section {
    std::map<std::string,float> values;
    std::map<std::string,std::vector<int>> lists;
    float readFloat(const std::string& key,float fallback)const{
        auto i=values.find(key);return i==values.end()?fallback:i->second;
    }
    void openSections(const std::string& key,std::vector<int>& out)const{
        auto i=lists.find(key);if(i!=lists.end())out.insert(out.end(),i->second.begin(),i->second.end());
    }
};
struct Matrix {
    float m[4][4]{};
    void identity(){for(int i=0;i<4;++i)for(int j=0;j<4;++j)m[i][j]=i==j?1.f:0.f;}
    void setRotateY(float a){identity();m[0][0]=m[2][2]=std::cos(a);m[0][2]=-std::sin(a);m[2][0]=std::sin(a);}
    void setRotateX(float a){identity();m[1][1]=m[2][2]=std::cos(a);m[1][2]=std::sin(a);m[2][1]=-std::sin(a);}
    void preMultiply(const Matrix& a){Matrix b;for(int i=0;i<4;++i)for(int j=0;j<4;++j)for(int k=0;k<4;++k)b.m[i][j]+=a.m[i][k]*m[k][j];*this=b;}
    void preRotateX(float a){Matrix b;b.setRotateX(a);preMultiply(b);}
    void preRotateY(float a){Matrix b;b.setRotateY(a);preMultiply(b);}
};
struct TimeOfDay {
    float sunAngle_=80, sunAngleZ_=0, time_=12;
    struct {Matrix sunTransform;} now_;
    std::vector<int> load(Section* dayNightSection){
'''
        source = host + PATCH.SUN_READER + '\nstd::vector<int> lightKeys;\n'
        source += PATCH.LIGHT_KEYS + '\nreturn lightKeys;}\nvoid tick(){\n'
        source += PATCH.SUN_TRANSFORM + '\n}};\n'
        source += r'''
int main(){
    Section s;s.values={{"angle",23},{"angleZ",17},{"forward/angle",42},{"deferred/angleZ",91}};
    s.lists={{"lightkey",{1}},{"forward/lightkey",{2}},{"deferred/lightkey",{3}}};
    TimeOfDay t;
    Renderer::instance().mode=0;
    assert(t.load(&s)==std::vector<int>{3});assert(t.sunAngle_==23&&t.sunAngleZ_==91);
    Renderer::instance().mode=1;
    assert(t.load(&s)==std::vector<int>{2});assert(t.sunAngle_==42&&t.sunAngleZ_==17);
    s.lists.erase("forward/lightkey");assert(t.load(&s)==std::vector<int>{3});
    s.lists.erase("deferred/lightkey");assert(t.load(&s)==std::vector<int>{1});
    s.lists.clear();assert(t.load(&s).empty());
    s.values.clear();t.load(&s);assert(t.sunAngle_==80&&t.sunAngleZ_==0);
    for(float angle:{-80.f,0.f,37.f,80.f,180.f})
    for(float angleZ:{0.f,1.1f,90.f,201.f})
    for(float hour:{0.f,6.f,12.f,18.f,23.5f}){
        t.sunAngle_=angle;t.sunAngleZ_=angleZ;t.time_=hour;t.tick();
        const double x=double(angle*-0.017453292f),z=double(angleZ*0.017453292f);
        const double h=double(6.2831855f-hour*0.2617994f);
        // Independently rotate the local -Z ray through the three axes.
        const double vx=-std::sin(h),vy=std::cos(h)*std::sin(x),vz=-std::cos(h)*std::cos(x);
        const std::array<double,3> expected{{vx*std::cos(z)+vz*std::sin(z),vy,-vx*std::sin(z)+vz*std::cos(z)}};
        for(int i=0;i<3;++i)assert(std::abs(-t.now_.sunTransform.m[2][i]-expected[i])<2e-6);
    }
}
'''
        with tempfile.TemporaryDirectory(prefix='stock-time-of-day-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(source)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17',
                '-Wall', '-Wextra', '-Werror', str(path / 'probe.cpp'),
                '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
