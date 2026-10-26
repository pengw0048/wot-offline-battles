"""Check deferred owner composition against pristine public engine sources.

These checks establish source and phase contracts, not Windows GPU acceptance.
"""
import importlib.util
import io
import itertools
import os
from pathlib import Path
import math
import random
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class DeferredOwnerPatchTests(unittest.TestCase):
    def test_actual_depth_frame_lifetime_and_prepare_failure(self):
        source = (ROOT/'bigworld/stock_deferred_buffers.cpp').read_text()
        bodies = []
        for method,next_method in (('begin()', 'end()'), ('end()', 'select('), ('prepare()', 'objectKindCopy()')):
            start = source.index('bool StockDeferredBuffers::'+method)
            marker = ('DX::BaseTexture* StockDeferredBuffers::'+next_method if next_method=='objectKindCopy()'
                      else 'bool StockDeferredBuffers::'+next_method)
            bodies.append(source[start:source.index(marker,start)])
        host = r'''
#include <cassert>
#include <cstddef>
#define FAILED(hr) ((hr)<0)
#define SUCCEEDED(hr) ((hr)>=0)
struct Surface{unsigned refs=1;void AddRef(){++refs;}void Release(){assert(refs);--refs;}};
template<class T>struct ComObjectWrap{
T* value=nullptr;~ComObjectWrap(){if(value)value->Release();}
T** operator&(){assert(!value);return &value;}
ComObjectWrap& operator=(T* p){if(p)p->AddRef();if(value)value->Release();value=p;return *this;}
T* pComObject(){return value;}};
struct Device{
Surface* current=nullptr;bool failGet=false,failSet=false;unsigned gets=0,sets=0;
int GetDepthStencilSurface(Surface** output){++gets;if(failGet)return -1;assert(current);current->AddRef();*output=current;return 0;}
int SetDepthStencilSurface(Surface* value){++sets;if(failSet)return -1;if(value)value->AddRef();if(current)current->Release();current=value;return 0;}
};
namespace Moo{struct RenderContext{Device gpu;Device* device(){return &gpu;}};
RenderContext& rc(){static RenderContext context;return context;}}
struct StockDeferredBuffers{
ComObjectWrap<Surface> previousDepth_,depthSurface_;
bool frameActive_=false,prepared_=false,kindCopied_=false;
bool resourcesReady=true,fillSuccess=true,conversionSuccess=true,depthEffectsSuccess=true;
unsigned fills=0,conversions=0,depthEffects=0;
bool ready(){return resourcesReady;}
bool fillLinearDepth(){++fills;return fillSuccess;}
bool convertMaterials(){++conversions;return conversionSuccess;}
bool fillDepthEffects(){++depthEffects;return depthEffectsSuccess;}
bool begin();bool end();bool prepare();~StockDeferredBuffers(){end();}
};
@PRODUCTION@
int main(){
Surface original,depth;auto& device=Moo::rc().gpu;
assert(device.SetDepthStencilSurface(&original)==0&&original.refs==2);
{
StockDeferredBuffers owner;owner.depthSurface_=&depth;assert(depth.refs==2);
assert(!owner.prepare()&&!owner.fills&&!owner.conversions);
owner.resourcesReady=false;assert(!owner.begin()&&!device.gets);
owner.resourcesReady=true;device.failGet=true;assert(!owner.begin()&&!owner.previousDepth_.value);
device.failGet=false;device.failSet=true;assert(!owner.begin()&&!owner.previousDepth_.value);
assert(original.refs==2&&depth.refs==2&&device.current==&original);
device.failSet=false;assert(owner.begin()&&device.current==&depth&&original.refs==2&&depth.refs==3);
const auto gets=device.gets;assert(owner.begin()&&device.gets==gets);
assert(owner.prepare()&&owner.prepared_&&owner.fills==1&&owner.conversions==1);
owner.fillSuccess=false;assert(!owner.prepare()&&!owner.prepared_&&owner.conversions==1);
owner.fillSuccess=true;owner.conversionSuccess=false;assert(!owner.prepare()&&!owner.prepared_&&owner.conversions==2);
owner.conversionSuccess=true;owner.depthEffectsSuccess=false;assert(!owner.prepare()&&!owner.prepared_&&owner.depthEffects==2);
owner.kindCopied_=true;assert(owner.end()&&device.current==&original);
assert(!owner.frameActive_&&!owner.prepared_&&!owner.kindCopied_&&!owner.previousDepth_.value);
assert(original.refs==2&&depth.refs==2);
const auto sets=device.sets;assert(owner.end()&&device.sets==sets);
assert(owner.begin());device.failSet=true;assert(!owner.end()&&!owner.previousDepth_.value);
assert(original.refs==1&&depth.refs==3);device.failSet=false;
assert(device.SetDepthStencilSurface(&original)==0);
assert(owner.begin()); // Destructor must restore the previous depth once.
}
assert(device.current==&original&&original.refs==2&&depth.refs==1);
device.SetDepthStencilSurface(nullptr);assert(original.refs==1);
}
'''.replace('@PRODUCTION@','\n'.join(bodies))
        with tempfile.TemporaryDirectory(prefix='actual-deferred-frame-') as directory:
            path = Path(directory)
            (path/'probe.cpp').write_text(host)
            result = subprocess.run(['c++','-std=c++14',str(path/'probe.cpp'),'-o',str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_depth_effect_passes_quality_coordinates_and_failures(self):
        source = (ROOT/'bigworld/stock_deferred_buffers.cpp').read_text()

        def body(signature):
            start = source.index(signature)
            end = source.index('{',start)
            level = 1
            while level:
                end += 1
                level += (source[end]=='{')-(source[end]=='}')
            return source[start:end+1]

        production = '\n'.join(body(signature) for signature in (
            'class TargetScope', 'Vector4 targetProjection(',
            'bool StockDeferredBuffers::select(', 'bool StockDeferredBuffers::draw(',
            'bool StockDeferredBuffers::blurDepthEffects(', 'bool StockDeferredBuffers::fillDepthEffects()',
            'bool drawStockDeferredSunLight('))
        # The class extraction includes its closing brace, not its semicolon.
        production = production.replace('bool saved_;\n}', 'bool saved_;\n};')
        host = r"""
#include "wot/stock_deferred_buffers.hpp"
#include "wot/stock_dynamic_resolution.hpp"
#include <cassert>
#include <cmath>
#include <cstdlib>
#include <map>
#include <memory>
#include <string>
#include <vector>
#define FAILED(hr) ((hr)<0)
#define SUCCEEDED(hr) ((hr)>=0)
using uint=unsigned;using D3DXHANDLE=const char*;
const int D3DCLEAR_TARGET=1;
unsigned steps=0,failAt=0;
bool step(){return ++steps!=failAt;}
struct Vector4{float x,y,z,w;Vector4(float a=0,float b=0,float c=0,float d=0):x(a),y(b),z(c),w(d){}};
namespace DX{struct Surface{std::string name;};struct Viewport{uint X,Y,Width,Height;float MinZ,MaxZ;};}
template<class T>struct ComObjectWrap{
T* value=nullptr;operator bool()const{return value!=nullptr;}T* pComObject()const{return value;}
ComObjectWrap& operator=(T* p){value=p;return *this;}};
struct ID3DXEffect{
std::map<std::string,std::vector<float>> values;std::map<std::string,std::string> textures;
bool importancePresent=false;D3DXHANDLE GetParameterByName(void*,const char* n){return importancePresent?n:nullptr;}
int put(const char* name,std::vector<float> value){if(!step())return -1;values[name]=value;return 0;}
int SetInt(const char* n,int v){return put(n,{float(v)});}int SetFloat(const char* n,float v){return put(n,{v});}
int SetBool(const char* n,bool v){return put(n,{float(v)});}
int SetVector(const char* n,const Vector4* v){return put(n,{v->x,v->y,v->z,v->w});}
int SetTexture(const char* n,DX::Surface* v){if(!step())return -1;textures[n]=v?v->name:"";return 0;}
};
struct Pass{std::string technique,target;bool scene,halfTexel;uint width,height;std::map<std::string,std::vector<float>> parameters;std::map<std::string,std::string> textures;};
std::vector<Pass> passes;
namespace Moo{
struct EffectMaterial{
std::string technique;ID3DXEffect definition,binding;bool selected=false;
bool setTechnique(const char* n){if(!step())return false;technique=n;selected=false;return true;}
struct Properties{EffectMaterial* owner;bool select(EffectMaterial&){if(!step())return false;owner->selected=true;return true;}};
Properties properties(){return {this};}EffectMaterial* pEffect(){return this;}
ID3DXEffect* effect(){assert(selected);return &binding;}
};
// pEffect() on ManagedEffect and EffectMaterial are separate public classes.
struct Material:EffectMaterial{
struct Managed{Material* owner;ID3DXEffect* pEffect(){return owner->effect();}} managed{this};
Managed* pEffect(){return &managed;}
struct Properties{Material* owner;bool select(Managed&){if(!step())return false;owner->selected=true;return true;}};
Properties properties(){return {this};}
};
using EffectMaterialPtr=std::shared_ptr<Material>;
struct RenderTarget{
uint w,h;DX::Surface surface,depth;
RenderTarget(uint a,uint b,const char* n):w(a),h(b),surface{n},depth{"depth"}{}
uint width()const{return w;}uint height()const{return h;}
int pSurface(ComObjectWrap<DX::Surface>& output){if(!step())return -1;output=&surface;return 0;}
DX::Surface* pTexture(){return &surface;}DX::Surface* depthBuffer(){return &depth;}
};
using RenderTargetPtr=std::shared_ptr<RenderTarget>;
struct Context{
struct Saved{uint w,h;bool scene;std::string target;};
std::vector<Saved> stack;uint w=1279,h=719;bool scene=true;std::string target="scene";
struct Desc{uint Width=1279,Height=719;} desc;
bool pushRenderTarget(){if(!step())return false;stack.push_back({w,h,scene,target});return true;}
void popRenderTarget(){assert(!stack.empty());auto s=stack.back();stack.pop_back();w=s.w;h=s.h;scene=s.scene;target=s.target;}
int setRenderTarget(unsigned index,const ComObjectWrap<DX::Surface>& s){if(!step())return -1;if(index==0)target=s.value->name;return 0;}
int setRenderTarget(unsigned,void*){return step()?0:-1;}
uint screenWidth()const{return w;}uint screenHeight()const{return h;}
void screenWidth(uint v){w=v;}void screenHeight(uint v){h=v;}
const Desc& backBufferDesc()const{return desc;}
struct Device{int SetDepthStencilSurface(DX::Surface*){return step()?0:-1;}
int Clear(unsigned,void*,int flags,unsigned color,float,unsigned){assert(flags==D3DCLEAR_TARGET&&color==0x00ff00ffu);return step()?0:-1;}} gpu;
Device* device(){return &gpu;}
struct Constants{void updateSharedConstants(int){}} constants;
Constants& effectVisualContext(){return constants;}
struct Quad{
bool drawChecked(Material& m,uint width,uint height,bool scaleHalfTexel);
bool drawChecked(Material& m,uint width,uint height);
} quad;
Quad& fsQuad(){return quad;}
};
Context& rc(){static Context context;return context;}
bool Context::Quad::drawChecked(Material& m,uint width,uint height,bool adjust){
if(!step())return false;assert(m.selected);
passes.push_back({m.technique,rc().target,rc().scene,adjust,width,height,m.binding.values,m.binding.textures});return true;}
bool Context::Quad::drawChecked(Material& m,uint width,uint height){return drawChecked(m,width,height,rc().scene);}
enum{CONSTANTS_PER_SCREEN};
}
int setStockSceneViewport(DX::Viewport*,bool scene){if(!step())return -1;Moo::rc().scene=scene;return 0;}
struct Resolution{wot::StockDynamicResolution value;const wot::StockDynamicResolution& state()const{return value;}} resolution;
Resolution* stockDynamicResolution(){return &resolution;}
struct SSAOSettings{float m_radius=.1f,m_fade=2.f,m_amplify=1.f;};
struct SSAO{SSAOSettings value;const SSAOSettings& settings()const{return value;}};
struct Pipeline{SSAO ssao;SSAO* ssaoSupport(){return &ssao;}};
wot::StockScreenSpaceShadowSettings activeScreenShadows;
struct StockDeferredBuffers{
Pipeline pipeline_;wot::StockDepthEffectQuality depthQuality_;bool aoEnvironment=true;
ComObjectWrap<DX::Surface> depthSurface_;
Moo::RenderTargetPtr reducedDepth_,lowPrecisionDepth_,depthEffectsHalf_,depthEffectsFull_,depthEffectsScratch_,depthEffectsHalfScratch_;
Moo::RenderTargetPtr aoNoise_,horizonNoise_;
Moo::EffectMaterialPtr aoMaterial_,contactShadowMaterial_,bilateralUpscaleMaterial_,bilateralBlurMaterial_;
StockDeferredBuffers(){
depthSurface_=&fullDepth;
reducedDepth_=std::make_shared<Moo::RenderTarget>(639,359,"reducedDepth");
lowPrecisionDepth_=std::make_shared<Moo::RenderTarget>(639,359,"lowDepth");
depthEffectsHalf_=std::make_shared<Moo::RenderTarget>(639,359,"half");
depthEffectsHalfScratch_=std::make_shared<Moo::RenderTarget>(639,359,"halfScratch");
depthEffectsFull_=std::make_shared<Moo::RenderTarget>(1279,719,"full");
depthEffectsScratch_=std::make_shared<Moo::RenderTarget>(1279,719,"fullScratch");
aoNoise_=std::make_shared<Moo::RenderTarget>(1,1,"sphere");horizonNoise_=std::make_shared<Moo::RenderTarget>(1,1,"horizon");
aoMaterial_=std::make_shared<Moo::Material>();contactShadowMaterial_=std::make_shared<Moo::Material>();
bilateralUpscaleMaterial_=std::make_shared<Moo::Material>();bilateralBlurMaterial_=std::make_shared<Moo::Material>();}
DX::Surface fullDepth{"fullDepth"};
bool ambientOcclusionEnabled()const{return aoEnvironment&&depthQuality_.ambientOcclusionEnabled();}
bool contactShadowsEnabled()const{return activeScreenShadows.enabled&&depthQuality_.contactShadowsEnabled();}
bool select(const Moo::EffectMaterialPtr&,const char*);bool draw(const Moo::EffectMaterialPtr&);
bool blurDepthEffects(const Moo::RenderTargetPtr&,const Moo::RenderTargetPtr&,bool,bool,bool,float,float);
bool fillDepthEffects();
bool prepared()const{return true;}int lightingQuality()const{return depthQuality_.lighting;}
};
StockDeferredBuffers* owner=nullptr;
@PRODUCTION@
bool near(float a,float b){return std::fabs(a-b)<1e-6f;}
int main(){
resolution.value.set_scale(.73f);
for(int quality=0;quality!=5;++quality)for(int shadow=0;shadow!=5;++shadow)
for(bool enabled:{false,true}){
StockDeferredBuffers owner;owner.depthQuality_.lighting=quality;owner.depthQuality_.shadows=shadow;
owner.aoEnvironment=enabled;activeScreenShadows.enabled=enabled;steps=0;failAt=0;passes.clear();
assert(owner.fillDepthEffects());assert(Moo::rc().stack.empty()&&Moo::rc().target=="scene");
assert(Moo::rc().w==1279&&Moo::rc().h==719&&Moo::rc().scene);
unsigned index=0;const auto& copy=passes[index++];
assert(copy.technique=="COPY_DEPTH"&&copy.target=="lowDepth"&&!copy.scene&&copy.halfTexel);
assert(copy.width==639&&copy.height==359);
if(enabled&&quality<3){const auto& ao=passes[index++];assert(ao.technique==(quality<2?"HBAO":"SSAO"));
assert(ao.target=="half"&&ao.scene&&ao.halfTexel);
assert(ao.parameters.at("g_lowQuality")[0]==float(quality>0));
assert(ao.parameters.at("g_enableTAA")[0]==0.f&&ao.parameters.at("g_taaSampleIndex")[0]==0.f);
assert(near(ao.parameters.at("g_packedParams0")[0],quality==2?.065f:.1f));
assert(near(ao.parameters.at("g_packedParams0")[1],quality==1?1.f:2.f));
assert(near(ao.parameters.at("g_packedParams1")[0],quality>0?.65f:1.f));
assert(ao.textures.at("g_noiseMapSSAO")==std::string(quality<2?"horizon":"sphere"));}
if(enabled&&shadow<2){const auto& contact=passes[index++];assert(contact.technique=="TRACE"&&contact.target=="half");
assert(contact.parameters.at("g_lowQuality")[0]==float(shadow>0));
assert(contact.parameters.at("g_ssShadowsParams")[0]==1.5f&&contact.parameters.at("g_enableTAA")[0]==0.f);}
if(quality<2){const auto& up=passes[index++];assert(up.technique=="BILATERAL_UPSCALE"&&up.target=="full");
assert(up.parameters.at("g_useBilateralUpscale")[0]==float(quality==0));
const auto& uv=up.parameters.at("g_halfResTextureDRRParams");assert(near(uv[0],466.f/639)&&near(uv[1],262.f/359));}
assert(passes.size()==index+2);
const auto& horizontal=passes[index++];const auto& vertical=passes[index++];
assert(horizontal.technique=="HORIZONTAL_PASS"&&vertical.technique=="VERTICAL_PASS");
assert(horizontal.target==(quality<2?"fullScratch":"halfScratch"));
assert(vertical.target==(quality<2?"full":"half"));
assert(horizontal.parameters.at("g_useBilateralFilter")[0]==float(quality<2));
assert(horizontal.parameters.at("g_sharpness")[0]==(quality<2?40.f:0.f));
assert(horizontal.parameters.at("g_processOnlyOpaque")[0]==float(quality<2));
assert(horizontal.textures.at("g_srcMap")==std::string(quality<2?"full":"half"));
assert(vertical.textures.at("g_srcMap")==std::string(quality<2?"fullScratch":"halfScratch"));
}
// Every checked API failure must unwind the target stack and stop the frame.
StockDeferredBuffers effectOwner;effectOwner.depthQuality_.shadows=0;activeScreenShadows.enabled=true;
steps=0;failAt=0;assert(effectOwner.fillDepthEffects());const unsigned operations=steps;
for(unsigned failure=1;failure<=operations;++failure){
steps=0;failAt=failure;passes.clear();assert(!effectOwner.fillDepthEffects());
assert(Moo::rc().stack.empty()&&Moo::rc().target=="scene"&&Moo::rc().w==1279&&Moo::rc().h==719&&Moo::rc().scene);
}
failAt=0;owner=&effectOwner;
auto sun=std::make_shared<Moo::Material>();
for(int quality=0;quality!=5;++quality)for(bool importance:{false,true}){
effectOwner.depthQuality_.lighting=quality;sun->binding.importancePresent=importance;
passes.clear();assert(drawStockDeferredSunLight(sun));const auto& draw=passes.back();
assert(draw.technique=="SUN_ST4__FL_ORIG"&&draw.target=="scene");
assert(draw.parameters.at("g_enableSSAO")[0]==float(quality<3));
assert(draw.parameters.at("g_enableSSshadows")[0]==float(quality<3));
assert(draw.parameters.at("g_enableSSR")[0]==0.f);
if(importance)assert(draw.parameters.at("g_importanceSamplerEnabled")[0]==0.f);
}
steps=0;failAt=0;assert(drawStockDeferredSunLight(sun));const unsigned sunOperations=steps;
for(unsigned failure=1;failure<=sunOperations;++failure){
steps=0;failAt=failure;passes.clear();assert(!drawStockDeferredSunLight(sun)&&passes.empty());
}
owner=nullptr;passes.clear();assert(!drawStockDeferredSunLight(sun)&&passes.empty());
}
""".replace('@PRODUCTION@',production)
        with tempfile.TemporaryDirectory(prefix='actual-depth-effects-') as directory:
            path = Path(directory)
            (path/'probe.cpp').write_text(host)
            result = subprocess.run(['c++','-std=c++14','-I',str(ROOT/'include'),str(path/'probe.cpp'),'-o',str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_non_temporal_projection_semantics_follow_the_view(self):
        source = (ROOT/'bigworld/stock_deferred_buffers.cpp').read_text()
        start = source.index('class SceneProjectionConstant')
        production = source[start:source.index('\nclass TargetScope',start)]
        host = r"""
#include <cassert>
#define SUCCEEDED(hr) ((hr)>=0)
using D3DXHANDLE=const char*;
struct Matrix{float values[16];};
struct ID3DXEffect;
namespace Moo{
struct EffectConstantValue{virtual ~EffectConstantValue(){};virtual bool operator()(ID3DXEffect*,D3DXHANDLE)=0;};
struct RenderContext{Matrix p,vp;const Matrix& projection()const{return p;}const Matrix& viewProjection()const{return vp;}};
RenderContext& rc(){static RenderContext context;return context;}
}
struct ID3DXEffect{Matrix value;bool fail=false;int SetMatrix(D3DXHANDLE,const Matrix* input){value=*input;return fail?-1:0;}};
@PRODUCTION@
int main(){
SceneProjectionConstant projection(false),viewProjection(true);ID3DXEffect effect;
for(unsigned frame=0;frame!=3;++frame){
for(unsigned i=0;i!=16;++i){Moo::rc().p.values[i]=float(frame*32+i);Moo::rc().vp.values[i]=float(frame*32+i+16);}
assert(projection(&effect,"JitteredProjection"));for(unsigned i=0;i!=16;++i)assert(effect.value.values[i]==Moo::rc().p.values[i]);
assert(viewProjection(&effect,"JitteredViewProjection"));for(unsigned i=0;i!=16;++i)assert(effect.value.values[i]==Moo::rc().vp.values[i]);
}
effect.fail=true;assert(!projection(&effect,"JitteredProjection"));assert(!viewProjection(&effect,"JitteredViewProjection"));
}
""".replace('@PRODUCTION@',production)
        with tempfile.TemporaryDirectory(prefix='actual-scene-projection-') as directory:
            path = Path(directory)
            (path/'probe.cpp').write_text(host)
            result = subprocess.run(['c++','-std=c++14',str(path/'probe.cpp'),'-o',str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def verify_actual_ssao_settings_owner(self, root):
        header = (root/'lib/romp/ssao_settings.hpp').read_text()
        source = (root/'lib/romp/ssao_settings.cpp').read_text()
        header = '\n'.join(line for line in header.splitlines() if not line.startswith('#include'))
        source = '\n'.join(line for line in source.splitlines() if not line.startswith('#include'))
        provider = (root/'lib/romp/ssao_support.cpp').read_text()
        start = provider.index('    class SSAOParamsConstant')
        end = provider.index('\n    };',start)+7
        provider = provider[start:end]
        host = r'''
#include <cassert>
#include <cstddef>
#include <limits>
#include <map>
#include <memory>
#include <string>
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
#define BW_GUARD
#define MF_ASSERT(value) assert(value)
#define SUCCEEDED(hr) ((hr)>=0)
struct Vector2{float x,y;Vector2(float a=0,float b=0):x(a),y(b){}};
struct DataSection{
std::map<std::string,float> numbers;std::map<std::string,Vector2> vectors;
void delChildren(){numbers.clear();vectors.clear();}
bool readBool(const char* n,bool fallback){return numbers.count(n)?numbers[n]!=0:fallback;}
float readFloat(const char* n,float fallback){return numbers.count(n)?numbers[n]:fallback;}
Vector2 readVector2(const char* n,Vector2 fallback){return vectors.count(n)?vectors[n]:fallback;}
void writeBool(const char* n,bool value){numbers[n]=value;}
void writeFloat(const char* n,float value){numbers[n]=value;}
void writeVector2(const char* n,Vector2 value){vectors[n]=value;}
};
struct DataSectionPtr:std::shared_ptr<DataSection>{using std::shared_ptr<DataSection>::shared_ptr;
bool exists()const{return bool(*this);}};
@HEADER@
@SOURCE@
struct Vector4{float v[4];Vector4(float a,float b,float c,float d):v{a,b,c,d}{}};
struct Matrix{float values[16];void setZero(){for(auto& value:values)value=0;}
void row(unsigned index,Vector4 value){for(unsigned i=0;i<4;++i)values[index*4+i]=value.v[i];}};
struct ID3DXEffect{Matrix captured;bool fail=false;
int SetMatrix(const char*,const Matrix* value){captured=*value;return fail?-1:0;}};
using D3DXHANDLE=const char*;
namespace Moo{struct EffectConstantValue{virtual ~EffectConstantValue(){};virtual bool operator()(ID3DXEffect*,D3DXHANDLE)=0;};}
struct SSAOSupport{SSAOSettings config;bool active=false;
bool enable()const{return active;}const SSAOSettings& settings()const{return config;}};
@PROVIDER@
int main(){
static_assert(sizeof(SSAOSettings)==48,"Original settings storage width");
static_assert(offsetof(SSAOSettings,m_localLights)==40,"Original local-light influence offset");
static_assert(offsetof(SSAOSettings,m_terrainAOGammaCorrection)==44,"Original terrain gamma offset");
SSAOSettings settings;assert(settings.m_enabled&&settings.m_radius==.1f&&settings.m_fade==2.f);
assert(settings.m_influences.m_speedtree.y==1.f&&settings.m_localLights==.75f&&settings.m_terrainAOGammaCorrection==1.f);
assert(!settings.load(DataSectionPtr()));
DataSectionPtr input(new DataSection);input->numbers={{"radius",.3f},{"redius",999.f},{"fadeNear",6.f},{"fade",999.f},
{"enable",0.f},{"amplify",1.7f},{"influences/localLights",.8f},{"terrainAOGammaCorrection",1.2f}};
input->vectors.emplace("influences/speedtree",Vector2(2.f,3.f));
assert(settings.load(input)&&!settings.m_enabled&&settings.m_radius==.3f&&settings.m_fade==6.f);
assert(settings.m_amplify==1.7f&&settings.m_localLights==.8f&&settings.m_terrainAOGammaCorrection==1.2f);
assert(settings.m_influences.m_speedtree.x==2.f&&settings.m_influences.m_speedtree.y==3.f);
DataSectionPtr output(new DataSection);assert(settings.save(output));
assert(output->numbers["radius"]==.3f&&output->numbers["fadeNear"]==6.f);
assert(!output->numbers.count("redius")&&!output->numbers.count("fade"));
assert(output->numbers["influences/localLights"]==.8f&&output->numbers["terrainAOGammaCorrection"]==1.2f);
SSAOSupport ssao;SSAOParamsConstant constant(ssao);ID3DXEffect effect;
assert(constant(&effect,"SSAOParams"));for(float v:effect.captured.values)assert(v==0.f);
ssao.active=true;assert(constant(&effect,"SSAOParams"));
assert(effect.captured.values[0]==1.f&&effect.captured.values[1]==1.f);
assert(effect.captured.values[2]==.75f&&effect.captured.values[3]==1.f);
assert(effect.captured.values[4]==1.f&&effect.captured.values[5]==0.f);
ssao.config.m_terrainAOGammaCorrection=99.f;assert(constant(&effect,"SSAOParams")&&effect.captured.values[3]==1.f);
ssao.config.m_terrainAOGammaCorrection=-1.f;assert(constant(&effect,"SSAOParams")&&effect.captured.values[3]==.25f);
ssao.config.m_terrainAOGammaCorrection=std::numeric_limits<float>::quiet_NaN();
assert(constant(&effect,"SSAOParams")&&effect.captured.values[3]==.25f);
ssao.active=false;ssao.config.m_localLights=.3f;
assert(constant(&effect,"SSAOParams")&&effect.captured.values[2]==.75f);
effect.fail=true;assert(!constant(&effect,"SSAOParams"));
}
'''.replace('@HEADER@',header).replace('@SOURCE@',source).replace('@PROVIDER@',provider)
        with tempfile.TemporaryDirectory(prefix='actual-ssao-settings-') as directory:
            path = Path(directory)
            (path/'probe.cpp').write_text(host)
            result = subprocess.run(['c++','-std=c++14',str(path/'probe.cpp'),'-o',str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_environment_material_reader_and_publication(self):
        source = (ROOT / 'bigworld/stock_deferred_buffers.cpp').read_text()
        def function(signature):
            start = source.index(signature)
            brace = source.index('{',start)
            depth = 1
            end = brace+1
            while depth:
                depth += (source[end]=='{')-(source[end]=='}')
                end += 1
            return source[start:end]
        bodies = '\n'.join(function(name) for name in ('bool setConversions(',
            'void loadStockDeferredMaterialSettings(', 'void activateStockDeferredMaterialSettings(',
            'void loadStockScreenSpaceShadowSettings(', 'void activateStockScreenSpaceShadowSettings(',
            'bool setStockDeferredDecalConversion('))
        host = r'''
#include "wot/stock_deferred_buffers.hpp"
#include <cassert>
#include <map>
#include <memory>
#include <string>
#include <vector>
#define SUCCEEDED(hr) ((hr)>=0)
struct Vector4{float x,y,z,w;Vector4(float a,float b,float c,float d):x(a),y(b),z(c),w(d){}};
struct DataSection{
std::map<std::string,std::shared_ptr<DataSection>> sections;
std::map<std::string,bool> bools;std::map<std::string,float> floats;
std::map<std::string,Vector4> vectors;
std::shared_ptr<DataSection> openSection(const char* name){auto at=sections.find(name);return at==sections.end()?nullptr:at->second;}
bool readBool(const char* n,bool fallback){auto at=bools.find(n);return at==bools.end()?fallback:at->second;}
float readFloat(const char* n,float fallback){auto at=floats.find(n);return at==floats.end()?fallback:at->second;}
Vector4 readVector4(const char* n,Vector4 fallback){auto at=vectors.find(n);return at==vectors.end()?fallback:at->second;}
};
using DataSectionPtr=std::shared_ptr<DataSection>;
struct ID3DXEffect{
bool fail=false;std::map<std::string,std::vector<float>> values;
int SetFloatArray(const char* name,const float* data,unsigned count){if(fail)return -1;values[name]={data,data+count};return 0;}
};
wot::StockDeferredMaterialSettings activeSettings;
wot::StockScreenSpaceShadowSettings activeScreenShadows;
const char* const categoryNames[]={"Terrain","Flora","SpeedTree","Objects","Decal","Overlay"};
@BODY@
int main(){
wot::StockDeferredMaterialSettings settings;
assert(!settings.sceneConversionEnabled());ID3DXEffect effect;
assert(setStockDeferredDecalConversion(&effect));const auto original=effect.values;
loadStockDeferredMaterialSettings(nullptr,settings);assert(!settings.sceneConversionEnabled());
auto environment=std::make_shared<DataSection>();auto pbs=std::make_shared<DataSection>();
environment->sections["PBSConversions"]=pbs;
loadStockDeferredMaterialSettings(environment,settings);
assert(!settings.categories[0].enabled&&settings.categories[1].enabled&&!settings.categories[2].enabled);
assert(settings.categories[3].enabled&&settings.categories[4].enabled&&!settings.categories[5].enabled);
auto decal=std::make_shared<DataSection>();pbs->sections["Decal"]=decal;
decal->bools["EnabledConversion"]=false;
decal->vectors.emplace("AlbedoParams",Vector4(.1f,.2f,.3f,.4f));
decal->vectors.emplace("GlossParams",Vector4(.5f,.6f,.7f,.8f));
decal->vectors.emplace("MetallicParams",Vector4(.9f,1.f,1.1f,1.2f));
pbs->floats["ObjectsAlbedoConversion"]=.45f;
loadStockDeferredMaterialSettings(environment,settings);
assert(settings.objects_albedo_multiplier==.45f&&!settings.categories[4].enabled);
assert(setStockDeferredDecalConversion(&effect)&&effect.values==original);
activateStockDeferredMaterialSettings(settings);
assert(setStockDeferredDecalConversion(&effect)&&effect.values["g_albedoConversions"][2]==.3f);
assert(effect.values["g_glossConversions"][0]==.5f&&effect.values["g_metallicConversions"][3]==1.2f);
effect.fail=true;assert(!setStockDeferredDecalConversion(&effect));assert(!setStockDeferredDecalConversion(nullptr));
activateStockDeferredMaterialSettings(wot::StockDeferredMaterialSettings());effect.fail=false;
assert(setStockDeferredDecalConversion(&effect)&&effect.values==original);
wot::StockDeferredLightingSelection lighting;assert(lighting.quality==0&&lighting.transmissionType()==2);
lighting.quality=2;assert(lighting.transmissionType()==1);
lighting.quality=0;assert(lighting.transmissionType()==1);
lighting.transmission=9;assert(lighting.transmissionType()==2);
lighting.transmission=-3;assert(lighting.transmissionType()==0);
wot::StockScreenSpaceShadowSettings shadows;
loadStockScreenSpaceShadowSettings(environment,shadows);
assert(shadows.enabled&&shadows.amplify==1.5f&&shadows.distance_near==.5f&&shadows.distance_far==50.f&&shadows.noise==1.f);
auto section=std::make_shared<DataSection>();environment->sections["SSShadows"]=section;
section->bools["enable"]=false;section->floats={{"amplify",2.f},{"distanceNear",.8f},{"distanceFar",60.f},{"noise",.4f}};
loadStockScreenSpaceShadowSettings(environment,shadows);
assert(!shadows.enabled&&shadows.amplify==2.f&&shadows.distance_near==.8f&&shadows.distance_far==60.f&&shadows.noise==.4f);
assert(activeScreenShadows.enabled&&activeScreenShadows.amplify==1.5f);
activateStockScreenSpaceShadowSettings(shadows);assert(!activeScreenShadows.enabled&&activeScreenShadows.amplify==2.f);
activateStockScreenSpaceShadowSettings(wot::StockScreenSpaceShadowSettings());assert(activeScreenShadows.enabled);
}
'''.replace('@BODY@',bodies)
        with tempfile.TemporaryDirectory(prefix='actual-deferred-material-settings-') as directory:
            path = Path(directory)
            (path/'probe.cpp').write_text(host)
            result = subprocess.run(['c++','-std=c++14','-I',str(ROOT/'include'),str(path/'probe.cpp'),'-o',str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_public_shader_layout_and_resource_idempotence(self):
        path = ROOT / 'bigworld/patch_stock_deferred_buffers.py'
        spec = importlib.util.spec_from_file_location('deferred_resources', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = Path(os.environ.get('WOT_PUBLIC_RESOURCES',
            '/Users/peng/.cache/wot-independent-client/builds/c4009598/client/engine-res'))
        if not (source / 'shaders/std_effects/write_g_buffer.fxh').exists():
            self.skipTest('Pinned public shader resources are required')
        with tempfile.TemporaryDirectory(prefix='deferred-layout-resources-') as directory:
            root = Path(directory)
            for path in (source / 'shaders').rglob('*'):
                if path.suffix in ('.fx', '.fxh'):
                    destination = root / path.relative_to(source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
            module.patch_resources(root)
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            module.patch_resources(root)
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})
            writer = (root / 'shaders/std_effects/write_g_buffer.fxh').read_text()
            reader = (root / 'shaders/std_effects/read_g_buffer.fxh').read_text()
            self.assertNotIn('packFloatToVec3(depth', writer)
            self.assertNotIn('unpackFloatFromVec3(tex2D(g_GBufferChannel0Sml', reader)
            self.assertIn('texture stockLinearDepthMap : HierarchicalZMap;', reader)
            self.assertIn('return tex2D(g_GBufferChannel1Sml, uv * stockSceneDRR.xy).rgb * 2.f - 1.f;', reader)
            self.assertIn('o.color1.rgb = nn * .5f + .5f;', writer)
            self.assertIn('g_buffer_markLegacyMaterial(o);', (root / 'shaders/std_effects/lightonly.fxh').read_text())
            decal = (root / 'shaders/std_effects/deferred_decal.fx').read_text()
            self.assertNotIn('cartesianToSpherical(normalize(mul(nn, TBN)))', decal)
            self.assertEqual(decal.count('stockConvertLegacyDecal(oColor.xyz,'), 2)
            self.assertEqual(decal.count('COLORWRITEENABLE2 = 0x07;'), 2)
            self.assertEqual(decal.count('float4 material : COLOR2;'), 1)

    def test_actual_decal_conversion_body_against_original_shader_instructions(self):
        # Interpret the scalar/vector arithmetic tail of original PS11. Texture
        # sampling and projection remain GPU acceptance, not a host claim.
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for the original shader oracle')
        with zipfile.ZipFile(Path(client)/'res/packages/shaders.pkg') as package:
            with zipfile.ZipFile(io.BytesIO(package.read('shaders/std_effects/wg_deferred_decal.10.dx9.fxo'))) as archive:
                original = archive.read('effect')
        decoder = r'''
#include "wot/stock_effect_binding.hpp"
#include <cassert>
#include <iostream>
#include <iterator>
int main(){std::vector<unsigned char> data((std::istreambuf_iterator<char>(std::cin)),{});
auto effect=std::make_shared<wot::StockEffectBinary>();std::string error;
assert(wot::decode_stock_dx9_effect(data.data(),data.size(),*effect,error));
wot::StockEffectUniformPool pool;wot::StockEffectUniformContext context(pool);wot::StockEffectBinding binding(effect,context);
unsigned disabled=0;bool changed=false;
for(unsigned b=0;b<effect->uniform_buffers.size();++b)for(unsigned u=0;u<effect->uniform_buffers[b].uniforms.size();++u)
if(effect->uniform_buffers[b].uniforms[u].name=="g_pbsReady"){
assert(binding.set_uniform(b,u,&disabled,4,std::nullopt,error));changed=true;}
assert(changed);bool selected=false;
for(unsigned i=0;i<effect->techniques.size();++i)if(effect->techniques[i].name=="PBS"){
wot::StockDx9PassSelection pass;assert(binding.select_pass(i,0,pass,error));assert(pass.pixel_shader&&*pass.pixel_shader==11);
const auto& code=effect->shaders[*pass.pixel_shader].bytecode;std::cout.write((const char*)code.data(),code.size()*4);selected=true;}
assert(selected);}
'''
        with tempfile.TemporaryDirectory(prefix='original-decal-shader-') as directory:
            path = Path(directory)
            (path/'decoder.cpp').write_text(decoder)
            sources = ['stock_shader_binary.cpp','stock_shader_runtime.cpp','stock_effect_binding.cpp']
            result = subprocess.run(['c++','-std=c++17','-I',str(ROOT/'include'),str(path/'decoder.cpp')]+
                [str(ROOT/'src'/name) for name in sources]+['-o',str(path/'decoder')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(path/'decoder')],input=original,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            words = struct.unpack('<%dI' % (len(result.stdout)//4),result.stdout)
        f32 = lambda x: struct.unpack('<f', struct.pack('<f', x))[0]
        def kind(token):
            return ((token >> 28) & 7) | ((token >> 8) & 24)
        def oracle(row):
            registers = {}
            registers[(0,4)] = row[:4]
            registers[(0,2)] = [0.,0.,0.,row[4]]
            for number in range(3):
                registers[(2,9+number)] = row[5+number*4:9+number*4]
            instructions = []
            index = 1
            while index < len(words):
                token = words[index];op = token & 65535
                if op == 65535:
                    break
                if op == 65534:
                    index += 1+((token >> 16)&32767)
                    continue
                length = (token >> 24)&15
                operands = words[index+1:index+1+length]
                if op == 81:
                    registers[(kind(operands[0]),operands[0]&2047)] = list(struct.unpack('<4f', struct.pack('<4I', *operands[1:])))
                if index >= 957:
                    instructions.append((op, operands))
                index += 1+length
            def read(token):
                value = registers.get((kind(token),token&2047), [0.]*4)
                output = [value[(token >> (16+2*i))&3] for i in range(4)]
                modifier = (token >> 24)&15
                self.assertIn(modifier, (0,1))
                return [-x for x in output] if modifier else output
            for op, operands in instructions:
                if op == 0:
                    continue
                values = [read(t) for t in operands[1:]]
                if op == 1: result = values[0]
                elif op == 2: result = [f32(a+b) for a,b in zip(*values)]
                elif op == 4: result = [f32(a*b+c) for a,b,c in zip(*values)]
                elif op == 5: result = [f32(a*b) for a,b in zip(*values)]
                elif op == 8: result = [f32(sum(a*b for a,b in zip(values[0][:3],values[1][:3])))]*4
                elif op == 10: result = [min(a,b) for a,b in zip(*values)]
                elif op == 11: result = [max(a,b) for a,b in zip(*values)]
                elif op == 14: result = [f32(2.**x) for x in values[0]]
                elif op == 15: result = [f32(math.log2(abs(x))) if x else float('-inf') for x in values[0]]
                elif op == 18: result = [f32(a*b+(1.-a)*c) for a,b,c in zip(*values)]
                elif op == 32: result = [f32(abs(a)**b) for a,b in zip(*values)]
                else: self.fail('Unexpected original PS11 arithmetic opcode: %d' % op)
                destination = operands[0]
                target = registers.setdefault((kind(destination),destination&2047), [0.]*4)
                for component in range(4):
                    if destination & (1 << (16+component)):
                        target[component] = result[component]
            return registers[(8,2)][:3]+registers[(8,0)][:3]
        source = (ROOT / 'bigworld/stock_deferred_legacy_decal.fxh').read_text()
        constants = '\n'.join(line for line in source.splitlines() if line.startswith('float4 g_'))
        body = source[source.index('void stockConvertLegacyDecal('):source.rindex('#endif')]
        body = body.replace('inout float3', 'float3&').replace('out float3', 'float3&')
        host = r'''
#include <algorithm>
#include <cmath>
#include <iostream>
struct float3 {float x,y,z;float3(float a,float b,float c):x(a),y(b),z(c){}float3():float3(0,0,0){} };
struct float4 {float x,y,z,w;float4(float a,float b,float c,float d):x(a),y(b),z(c),w(d){} };
float3 operator+(float3 a,float3 b){return {a.x+b.x,a.y+b.y,a.z+b.z};}
float3 operator-(float3 a,float3 b){return {a.x-b.x,a.y-b.y,a.z-b.z};}
float3 operator-(float3 a,float b){return {a.x-b,a.y-b,a.z-b};}
float3 operator+(float a,float3 b){return {a+b.x,a+b.y,a+b.z};}
float3 operator*(float a,float3 b){return {a*b.x,a*b.y,a*b.z};}
float3 pow(float3 a,float b){return {std::pow(a.x,b),std::pow(a.y,b),std::pow(a.z,b)};}
float3 max(float3 a,float b){return {std::max(a.x,b),std::max(a.y,b),std::max(a.z,b)};}
float dot(float3 a,float3 b){return a.x*b.x+a.y*b.y+a.z*b.z;}
using std::min;using std::pow;
@CONSTANTS@
@BODY@
int main(){float row[17];while(std::cin.read((char*)row,sizeof(row))){
float3 albedo(row[0],row[1],row[2]),material;
g_albedoConversions=float4(row[5],row[6],row[7],row[8]);
g_glossConversions=float4(row[9],row[10],row[11],row[12]);
g_metallicConversions=float4(row[13],row[14],row[15],row[16]);
stockConvertLegacyDecal(albedo,row[4],material);
float output[]={albedo.x,albedo.y,albedo.z,material.x,material.y,material.z};
std::cout.write((char*)output,sizeof(output));}return !std::cin.eof();}
'''.replace('@CONSTANTS@', constants).replace('@BODY@', body)
        generator = random.Random(1513)
        rows = [[generator.uniform(.0001,1.) for _ in range(5)]+[.05,.95,.7,.3,.05,.75,.5,2.,.02,.6,2.,1.5] for _ in range(512)]
        rows += [[1.,.25,.01,1.,.8]+[.1,.8,1.2,.1,.03,.6,1.5,1.,.01,.7,1.4,.5]]
        rows = [list(struct.unpack('<17f',struct.pack('<17f',*row))) for row in rows]
        with tempfile.TemporaryDirectory(prefix='actual-decal-conversion-') as directory:
            path = Path(directory)
            (path/'probe.cpp').write_text(host)
            result = subprocess.run(['c++','-std=c++14','-ffp-contract=off',str(path/'probe.cpp'),'-o',str(path/'probe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(path/'probe')],input=b''.join(struct.pack('<17f',*row) for row in rows),capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            actual = struct.unpack('<%df' % (len(result.stdout)//4),result.stdout)
            self.assertEqual(len(actual),len(rows)*6)
            for index,row in enumerate(rows):
                for component,(expected,value) in enumerate(zip(oracle(row),actual[index*6:index*6+6])):
                    self.assertAlmostEqual(expected,value,delta=2e-5,msg=str((index,component,expected,value)))

    def test_all_related_patch_orders_and_second_build(self):
        names = ('stock_deferred_buffers', 'stock_deferred_lighting',
                 'stock_vehicle_traces', 'stock_dynamic_resolution', 'stock_vehicle_visuals')
        modules = {}
        files = set()
        for name in names:
            path = ROOT / 'bigworld' / ('patch_' + name + '.py')
            spec = importlib.util.spec_from_file_location('combined_' + name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules[name] = module
            files.update(re.findall(r"['\"]((?:lib|client)/[^'\"]+\.(?:cpp|hpp|ipp))['\"]", path.read_text()))
        archive = Path(os.environ.get('WOT_PUBLIC_ENGINE_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.is_file():
            self.skipTest('Pristine public engine archive is required')
        contents = {}
        with tarfile.open(archive) as source:
            members = {member.name: member for member in source.getmembers()}
            prefix = next(iter(members)).split('/')[0] + '/programming/bigworld/'
            for relative in files:
                member = members.get(prefix + relative)
                if member:
                    contents[relative] = source.extractfile(member).read()
                else:
                    self.assertTrue(relative.startswith(('lib/stock_', 'lib/wot/stock_')), relative)
        for sequence in itertools.permutations(names):
            with self.subTest(order=sequence), tempfile.TemporaryDirectory(prefix='deferred-owner-order-') as directory:
                root = Path(directory)
                for name, data in contents.items():
                    destination = root / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                def apply(name):
                    module = modules[name]
                    function = getattr(module, 'apply', None) or getattr(module, 'patch', None) or module.patch_engine
                    function(root)
                for name in sequence:
                    apply(name)
                if sequence == names:
                    self.verify_actual_ssao_settings_owner(root)
                cpp = (root / 'lib/moo/deferred_pipeline.cpp').read_text()
                self.assertEqual(cpp.count('m_stockDeferredBuffers(new StockDeferredBuffers(*this))'), 1)
                self.assertEqual(cpp.count('if (!m_stockDeferredBuffers->begin())'), 1)
                self.assertEqual(cpp.count('if (!m_stockDeferredBuffers->end())'), 1)
                self.assertLess(cpp.index('if (!m_stockDeferredBuffers->prepare())'), cpp.index('    m_decalsManager->draw();'))
                self.assertLess(cpp.index('    m_decalsManager->draw();'), cpp.index('    if (!drawStockVehicleTraces())'))
                self.assertLess(cpp.index('    if (!drawStockVehicleTraces())'), cpp.index('    m_dynamicShadowManager->receive();'))
                self.assertNotIn('    m_SSAOSupport->resolve();',cpp)
                self.assertLess(cpp.index('m_spt(new SpeedTreeOptimizer())'), cpp.index('m_stockDeferredBuffers(new'))
                env = (root / 'lib/romp/enviro_minder.cpp').read_text()
                for method in ('loadStockDeferredMaterials(pDS, loadFromExternal);',
                               'loadStockDeferredLighting(pDS, loadFromExternal);'):
                    self.assertEqual(env.count(method), 1)
                world = (root / 'client/world_app.cpp').read_text()
                self.assertLess(world.index('GlobalEmbodiments::tick( dGameTime );'), world.index('tickStockVehicleTraces();'))
                self.assertLess(world.index('tickStockVehicleAudioTasks();'), world.index('tickStockVehicleTraces();'))
                before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                for name in reversed(sequence):
                    apply(name)
                after = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                self.assertEqual(before.keys(), after.keys())
                for name in before:
                    self.assertTrue(before[name] == after[name], str(name) + ' changed on the second build')


if __name__ == '__main__':
    unittest.main()
