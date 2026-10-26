// Execute production load/publication/constant lifetimes with recording owners.
// This host boundary fixture does not prove GPU texture loading or rendering.
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <map>
#include <memory>
#include <string>
#include <vector>
#include <iostream>
#define WOT_STOCK_DEFERRED_LIGHTING_PORTABLE
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
#define SUCCEEDED(value) ((value)>=0)
using D3DXHANDLE = const char*;
struct Vector3 {
    float v[3];
    Vector3(float x=0,float y=0,float z=0):v{x,y,z}{}
    float operator[](unsigned i)const{return v[i];}
};
namespace BW {using string=std::string;}
struct DataSection {
    std::map<std::string,std::string> strings;
    std::map<std::string,Vector3> vectors;
    unsigned luminanceReads=0;
    std::string readString(const char* name){return strings[name];}
    Vector3 readVector3(const char* name,const Vector3& fallback){auto at=vectors.find(name);return at==vectors.end()?fallback:at->second;}
    float readFloat(const char* name,float fallback){assert(std::string(name)=="max_lum");++luminanceReads;return fallback;}
};
using DataSectionPtr=std::shared_ptr<DataSection>;
struct StockDeferredLightingSettings {float sphericalHarmonics[28]={};bool loaded=false;};
namespace BWResource {
std::map<std::string,DataSectionPtr> resources;
bool textureExists=false;
DataSectionPtr openSection(const std::string& name){return resources[name];}
bool fileExists(const char* name){assert(std::string(name)=="system/maps/env_brdf_lut.dds");return textureExists;}
}
std::vector<std::string> errors;
void error(const char* format,...){char buffer[512];va_list args;va_start(args,format);std::vsnprintf(buffer,sizeof(buffer),format,args);va_end(args);errors.push_back(buffer);}
#define ERROR_MSG(...) error(__VA_ARGS__)
struct ID3DXEffect {
    std::vector<float> uniform;
    void* texture=NULL;
    bool fail=false;
    int SetValue(D3DXHANDLE,const void* data,unsigned bytes){
        if(fail)return -1;
        assert(bytes==112);
        uniform.resize(28);
        std::memcpy(uniform.data(),data,bytes);
        return 0;
    }
    int SetTexture(D3DXHANDLE,void* value){if(fail)return -1;texture=value;return 0;}
};
namespace Moo {
struct BaseTexture {
    enum Status {STATUS_READY,STATUS_FAILED_TO_LOAD};
    Status state=STATUS_READY;
    void* gpu=reinterpret_cast<void*>(0x1513);
    void* pTexture(){return gpu;}
    Status status(){return state;}
};
using BaseTexturePtr=std::shared_ptr<BaseTexture>;
struct EffectConstantValue {virtual ~EffectConstantValue(){};virtual bool operator()(ID3DXEffect*,D3DXHANDLE)=0;};
struct EffectVisualContext {
    enum Type {AUTO_CONSTANT_TYPE_PER_FRAME};
    std::map<std::string,std::shared_ptr<EffectConstantValue>> constants;
    unsigned registrations=0,removals=0;
    void registerAutoConstant(Type,const char* name,EffectConstantValue* constant){assert(!constants.count(name));constants[name].reset(constant);++registrations;}
    void unregisterAutoConstant(Type,const char* name){assert(constants.erase(name)==1);++removals;}
};
struct RenderContext {EffectVisualContext context;EffectVisualContext& effectVisualContext(){return context;}};
RenderContext& rc(){static RenderContext context;return context;}
struct TextureManager {
    BaseTexturePtr source=std::make_shared<BaseTexture>();
    unsigned reads=0;
    static TextureManager* instance(){static TextureManager manager;return &manager;}
    BaseTexturePtr get(const char* resource,bool animation,bool placeholder,bool load,const char* allocator){
        assert(std::string(resource)=="system/maps/env_brdf_lut.dds"&&animation&&!placeholder&&load);
        assert(std::string(allocator)=="Art/File Textures/");++reads;return source;
    }
};
}
#include "../bigworld/stock_deferred_lighting.cpp"
int main(){
    auto environment=std::make_shared<DataSection>();auto source=std::make_shared<DataSection>();
    environment->strings["PBS/DEFERRED/REM_SH"]="actual/one.xml";BWResource::resources["actual/one.xml"]=source;
    for(unsigned i=0;i<9;++i)source->vectors["sh"+std::to_string(i)]=Vector3(float(i),float(i+20),float(i+40));
    StockDeferredLightingSettings first,second;
    assert(!loadStockDeferredLightingSettings(NULL,first)&&!first.loaded&&!errors.empty());
    assert(loadStockDeferredLightingSettings(environment,first)&&first.loaded&&source->luminanceReads==1);
    assert(!initialiseStockDeferredLightingConstants()&&Moo::rc().context.constants.empty());
    BWResource::textureExists=true;
    auto manager=Moo::TextureManager::instance();manager->source->state=Moo::BaseTexture::STATUS_FAILED_TO_LOAD;
    assert(!initialiseStockDeferredLightingConstants()&&Moo::rc().context.constants.empty());
    manager->source->state=Moo::BaseTexture::STATUS_READY;manager->source->gpu=NULL;
    assert(!initialiseStockDeferredLightingConstants()&&Moo::rc().context.constants.empty());
    manager->source->gpu=reinterpret_cast<void*>(0x1513);
    assert(initialiseStockDeferredLightingConstants()&&initialiseStockDeferredLightingConstants());
    auto& context=Moo::rc().context;
    assert(context.constants.size()==2&&context.registrations==2&&!context.constants.count("SpeedTreeMaterials"));
    ID3DXEffect effect;
    auto sh=context.constants["REM_SH9"],brdf=context.constants["EnvBRDFLut"];
    assert(!(*sh)(&effect,"SH"));
    activateStockDeferredLightingSettings(first);assert((*sh)(&effect,"SH"));
    assert(effect.uniform==std::vector<float>(first.sphericalHarmonics,first.sphericalHarmonics+28));
    assert((*brdf)(&effect,"BRDF")&&effect.texture==manager->source->gpu);
    BWResource::textureExists=false;
    assert(!initialiseStockDeferredLightingConstants()&&context.constants.size()==2&&!(*brdf)(&effect,"BRDF"));
    BWResource::textureExists=true;
    assert(initialiseStockDeferredLightingConstants()&&context.registrations==2);
    auto other=std::make_shared<DataSection>();other->vectors["sh0"]=Vector3(100,200,300);
    BWResource::resources["actual/two.xml"]=other;environment->strings["PBS/DEFERRED/REM_SH"]="actual/two.xml";
    assert(loadStockDeferredLightingSettings(environment,second)&&second.loaded);
    assert((*sh)(&effect,"SH")&&effect.uniform[0]==first.sphericalHarmonics[0]);
    activateStockDeferredLightingSettings(second);second.sphericalHarmonics[0]=999;
    assert((*sh)(&effect,"SH")&&effect.uniform[0]==100); // Publication owns its copy.
    effect.fail=true;assert(!(*sh)(&effect,"SH")&&!(*brdf)(&effect,"BRDF"));effect.fail=false;
    deactivateStockDeferredLightingSettings();assert(!(*sh)(&effect,"SH"));
    environment->strings.clear();assert(!loadStockDeferredLightingSettings(environment,second)&&!second.loaded);
    activateStockDeferredLightingSettings(second);assert(!(*sh)(&effect,"SH"));
    finaliseStockDeferredLightingConstants();finaliseStockDeferredLightingConstants();
    assert(context.constants.empty()&&context.removals==2&&!brdfLut);
    assert(!(*brdf)(&effect,"BRDF"));
    assert(initialiseStockDeferredLightingConstants());finaliseStockDeferredLightingConstants();
    assert(context.registrations==4&&context.removals==4);
    std::cout<<"Production SH/BRDF load, publication, failures and repeated managed lifecycle passed"<<std::endl;
}
