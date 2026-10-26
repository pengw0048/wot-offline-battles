"""Exercise the production instanced geometry owner with recording D3D state.

This proves stream/declaration restoration and checked failure paths, not GPU
projection, native COM acceptance, or rendering equivalence.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOST = r'''
#include "wot/stock_vehicle_traces.hpp"
#include <cassert>
#include <vector>
using DWORD=unsigned;using UINT=unsigned;using HRESULT=int;
#define FAILED(hr) ((hr)<0)
constexpr unsigned D3DSTREAMSOURCE_INDEXEDDATA=0x40000000u;
constexpr unsigned D3DSTREAMSOURCE_INSTANCEDATA=0x80000000u;
int failOperation=0,operation=0,draws=0,guards=0;
bool failed(){return ++operation==failOperation;}
namespace DX{
struct VertexBuffer{};struct VertexDeclaration{};
struct Device{
DWORD frequency0=17,frequency9=19;UINT offset=23,stride=29;
VertexBuffer originalBuffer,temporaryBuffer;VertexDeclaration originalDeclaration,combinedDeclaration;
VertexBuffer* stream=&originalBuffer;VertexDeclaration* declaration=&originalDeclaration;
HRESULT GetStreamSourceFreq(unsigned n,DWORD* value){if(failed())return -1;*value=n==0?frequency0:frequency9;return 0;}
HRESULT GetStreamSource(unsigned n,VertexBuffer** value,UINT* off,UINT* size){assert(n==9);if(failed())return -1;*value=stream;*off=offset;*size=stride;return 0;}
HRESULT GetVertexDeclaration(VertexDeclaration** value){if(failed())return -1;*value=declaration;return 0;}
HRESULT SetStreamSourceFreq(unsigned n,DWORD value){if(failed())return -1;(n==0?frequency0:frequency9)=value;return 0;}
HRESULT SetStreamSource(unsigned n,VertexBuffer* value,UINT off,UINT size){assert(n==9);if(failed())return -1;stream=value;offset=off;stride=size;return 0;}
};
}
template<class T>struct ComObjectWrap{T* value=nullptr;T** operator&(){return &value;}T* pComObject(){return value;}};
template<class T>struct Ptr{T* value;T* operator->()const{return value;}T* getObject()const{return value;}explicit operator bool()const{return value;}};
namespace Moo{
struct RenderContext{DX::Device gpu;DX::Device* device(){return &gpu;}
HRESULT setVertexDeclaration(DX::VertexDeclaration* value){if(failed())return -1;gpu.declaration=value;return 0;}};
RenderContext& rc(){static RenderContext context;return context;}
struct VertexDeclaration{
static VertexDeclaration* expectedBase;static VertexDeclaration* expectedInstances;
static VertexDeclaration* combine(VertexDeclaration* base,VertexDeclaration* instances){assert(base==expectedBase&&instances==expectedInstances);if(failed())return nullptr;static VertexDeclaration value;return &value;}
DX::VertexDeclaration* declaration(){return &rc().gpu.combinedDeclaration;}
};
VertexDeclaration* VertexDeclaration::expectedBase=nullptr;VertexDeclaration* VertexDeclaration::expectedInstances=nullptr;
struct Vertices{VertexDeclaration base;HRESULT setVertices(bool software,bool instanced){assert(!software&&!instanced);return failed()?-1:0;}const VertexDeclaration* pDecl(){return &base;}};
struct Primitive{
HRESULT setPrimitives(){return failed()?-1:0;}
HRESULT drawInstancedPrimitiveGroup(unsigned group,unsigned count){
if(failed())return -1;auto& d=rc().gpu;
assert(count==7&&(group==3||group==5));assert(d.frequency0==(D3DSTREAMSOURCE_INDEXEDDATA|7));
assert(d.frequency9==(D3DSTREAMSOURCE_INSTANCEDATA|1));assert(d.stream==&d.temporaryBuffer);
assert(d.offset==0&&d.stride==96&&d.declaration==&d.combinedDeclaration);++draws;return 0;}
};
struct Group{unsigned groupIndex_;};
struct Geometry{Ptr<Vertices> vertices_;Ptr<Primitive> primitives_;std::vector<Group> primitiveGroups_;};
struct RenderSet{std::vector<Geometry> geometry_;};
struct Visual{
std::vector<RenderSet> sets;std::vector<RenderSet>& renderSets(){return sets;}
struct ReadGuard{ReadGuard(Visual*){++guards;}~ReadGuard(){--guards;}};
};
struct VertexBuffer{HRESULT set(unsigned stream,unsigned off,unsigned stride){return rc().gpu.SetStreamSource(stream,&rc().gpu.temporaryBuffer,off,stride);}};
}
struct TraceRenderer{
Ptr<Moo::Visual> cube;Moo::VertexDeclaration* instanceDeclaration;Moo::VertexBuffer instances;
@PRODUCTION@
};
int main(){
Moo::Visual cube;Moo::Vertices vertices;Moo::Primitive primitive;Moo::VertexDeclaration instances;
Moo::VertexDeclaration::expectedBase=&vertices.base;Moo::VertexDeclaration::expectedInstances=&instances;
cube.sets={{{{{&vertices},{&primitive},{{3},{5}}},{{&vertices},{&primitive},{{3},{5}}}}}};
TraceRenderer renderer{{&cube},&instances,{}};
assert(renderer.drawGeometry(7)&&draws==4);const int successfulOperations=operation;
auto check=[](){auto& d=Moo::rc().gpu;assert(d.frequency0==17&&d.frequency9==19);
assert(d.stream==&d.originalBuffer&&d.offset==23&&d.stride==29&&d.declaration==&d.originalDeclaration);assert(!guards);};
check();
// Every checked acquisition/draw failure must leave the caller's state intact.
// Restoration calls are deliberately outside the injected operation range.
for(int target=1;target<=successfulOperations-4;++target){
operation=0;failOperation=target;draws=0;bool result=renderer.drawGeometry(7);
assert(!result);check();}
failOperation=0;operation=0;assert(renderer.drawGeometry(7));check();
}
'''


class TraceRendererTests(unittest.TestCase):
    def test_actual_trace_setting_option_metadata_and_callback_lifetime(self):
        source = (ROOT/'bigworld/stock_vehicle_traces.cpp').read_text()
        start = source.index('class VehicleTracesSetting :')
        end = source.index('\n};',start)+3
        host = r'''
#include <cassert>
#include <string>
#include <vector>
bool vehicleTracesEnabled=true;
namespace Moo{struct GraphicsSetting{
std::string label;int option;std::vector<std::string> labels;std::vector<bool> advanced;
GraphicsSetting(const char* n,const char*,int initial,bool delayed,bool restart):label(n),option(initial){assert(!delayed&&!restart);}
void addOption(const char* n,const char*,bool supported,bool adv=false){assert(supported);labels.push_back(n);advanced.push_back(adv);}
virtual void onOptionSelected(int)=0;virtual ~GraphicsSetting(){};};}
@PRODUCTION@
int main(){VehicleTracesSetting setting;
assert(setting.label=="VEHICLE_TRACES_ENABLED"&&setting.option==0&&vehicleTracesEnabled);
assert(setting.labels==std::vector<std::string>({"OFF","ON"}));
assert(setting.advanced==std::vector<bool>({false,true}));
setting.onOptionSelected(0);assert(!vehicleTracesEnabled);
setting.onOptionSelected(1);assert(vehicleTracesEnabled);
}
'''.replace('@PRODUCTION@',source[start:end])
        with tempfile.TemporaryDirectory(prefix='actual-trace-setting-') as directory:
            root = Path(directory)
            (root/'probe.cpp').write_text(host)
            result = subprocess.run(['c++','-std=c++14',str(root/'probe.cpp'),'-o',str(root/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(root/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_geometry_state_and_failures(self):
        source = (ROOT/'bigworld/stock_vehicle_traces.cpp').read_text()
        start = source.index('    bool drawGeometry(unsigned count)')
        end = source.index('    bool draw()', start)
        host = HOST.replace('@PRODUCTION@', source[start:end])
        with tempfile.TemporaryDirectory(prefix='actual-trace-renderer-') as directory:
            root = Path(directory)
            (root/'probe.cpp').write_text(host)
            result = subprocess.run(['c++','-std=c++14','-I',str(ROOT/'include'),str(root/'probe.cpp'),'-o',str(root/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(root/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__ == '__main__':
    unittest.main()
