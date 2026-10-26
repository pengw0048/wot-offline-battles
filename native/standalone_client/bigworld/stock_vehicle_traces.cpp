#include "pch.hpp"
#include "stock_vehicle_traces.hpp"
#include "stock_vehicle_components.hpp"
#include "stock_vehicle_visuals.hpp"
#include "stock_decal_resources.hpp"
#include "stock_deferred_buffers.hpp"
#include "stock_dynamic_resolution.hpp"
#include "stock_effect_adapter.hpp"
#include "vehicle_appearance.hpp"
#include "wot/stock_vehicle_traces.hpp"
#include "moo/dynamic_decal.hpp"
#include "moo/graphics_settings.hpp"
#include "moo/renderer.hpp"
#include "moo/moo_utilities.hpp"
#include "moo/primitive.hpp"
#include "moo/vertex_buffer.hpp"
#include "moo/vertex_declaration.hpp"
#include "moo/vertices.hpp"
#include "moo/visual.hpp"
#include "moo/visual_manager.hpp"
#include "physics2/collision_callback.hpp"
#include "physics2/collision_obstacle.hpp"
#include "space/client_space.hpp"
#include "space/deprecated_space_helpers.hpp"
#include "math/quat.hpp"
#include "pyscript/script_math.hpp"

#include <algorithm>
#include <cfloat>
#include <map>
#include <memory>
#include <vector>
#include <cstring>

BW_BEGIN_NAMESPACE
int StockVehicleTraces_token = 1;
namespace
{
struct TraceState;
std::vector<std::weak_ptr<TraceState>> activeTraces;
bool vehicleTracesEnabled = true; // Exact #1513 data byte 01a12f3c.

class TraceProjectionHit : public CollisionCallback
{
public:
    bool found=false;
    WorldTriangle hit;
    virtual int operator()(const CollisionObstacle& obstacle,const WorldTriangle& triangle,float)
    {
        // a54420 accepts ordinary models and terrain; it excludes a transparent
        // triangle only for this exact flag combination. Its additional
        // WGMaterialDisabler branch cannot occur before that real type exists;
        // no placeholder fashion or always-visible query is registered here.
        if((triangle.collisionFlags()&0x12u)==2u)return COLLIDE_ALL;
        hit=WorldTriangle(obstacle.transform().applyPoint(triangle.v0()),
            obstacle.transform().applyPoint(triangle.v1()),obstacle.transform().applyPoint(triangle.v2()));
        hit.flags(triangle.flags());found=true;
        return COLLIDE_BEFORE;
    }
};

struct TraceTextureLayout
{
    uint32 key=0,side=0;
    Moo::BaseTexturePtr textures[3];
    wot::StockTraceTextureRectangle rectangle;
};

class TraceRenderer : public Moo::DeviceCallback
{
public:
    wot::StockVehicleTraceManager manager;
    std::vector<TraceTextureLayout> layouts;
    Moo::EffectMaterialPtr material;
    Moo::VisualPtr cube;
    Moo::VertexDeclaration* instanceDeclaration=NULL;
    Moo::VertexBuffer instances;
    unsigned instanceCapacity=0;
    ClientSpacePtr space;

    ~TraceRenderer(){deleteUnmanagedObjects();deleteManagedObjects();}
    void clear(){manager.clear();layouts.clear();}
    void syncSpace()
    {
        ClientSpacePtr current=DeprecatedSpaceHelpers::cameraSpace();
        if(current && current->cleared())current=NULL;
        if(current!=space){clear();space=current;}
    }
    virtual void createManagedObjects()
    {
        Moo::createEffect(material,"shaders/std_effects/wg_deferred_decal.fx");
        cube=Moo::VisualManager::instance()->get("system/models/fx_unite_cube_decal.visual");
        instanceDeclaration=Moo::VertexDeclaration::get("decal_instancing");
    }
    virtual void deleteManagedObjects(){material=NULL;cube=NULL;instanceDeclaration=NULL;}
    virtual void deleteUnmanagedObjects()
    {
        // This is a hand-written material parameter owner. Automatic GBuffer
        // and HZ resources are released across all Bindings by the runtime.
        const auto clearTextures=[](ID3DXEffect* effect)
        {
            if(!effect)return;
            const char* names[]={"g_map1","g_map2","g_map3","g_map4","g_texObjKind"};
            for(const char* name:names)effect->SetTexture(name,NULL);
        };
        if(material && material->pEffect())
        {
            ID3DXEffect* binding=material->properties().existingStockBinding();
            clearTextures(binding);
            ID3DXEffect* current=material->pEffect()->pEffect();
            if(current && current!=binding && !wot::is_stock_effect(current))clearTextures(current);
        }
        instances.release();instanceCapacity=0;
    }
    virtual bool recreateForD3DExDevice() const{return true;}
    bool ready()const{return material && cube && cube->isOK() && cube->nTriangles()!=0 && instanceDeclaration;}
    void setQuality(unsigned option)
    {
        manager.setQuality(option);layouts.clear();
    }
    void tick(float elapsed)
    {
        syncSpace();
        const Vector3 camera=Moo::rc().invView().applyToOrigin();
        manager.tick(elapsed,{camera.x,camera.y,camera.z},Moo::rc().lodZoomFactor());
    }
    int layout(const wot::StockTraceStamp& stamp,int albedo)
    {
        if(albedo==-1)return -1;
        const uint32 side=stamp.right?0x40000000u:0x20000000u;
        const uint32 key=static_cast<uint32>(albedo)|
            ((static_cast<uint32>(stamp.textures.material)<<8|static_cast<uint32>(stamp.textures.normal))<<8);
        for(unsigned i=0;i<layouts.size();++i)
            if(layouts[i].key==key && (layouts[i].side&side))return static_cast<int>(i);
        Renderer* renderer=Renderer::pInstance();
        StockTraceTextureOwner* resources=renderer?renderer->traceTextureOwner():NULL;
        if(!resources)return -1;
        const auto& textures=resources->textures();
        const int ids[]={albedo,stamp.textures.normal,stamp.textures.material};
        TraceTextureLayout value;value.key=key;
        for(unsigned i=0;i!=3;++i)
        {
            auto found=textures.find(static_cast<uint32>(ids[i])|0x60000000u);
            const bool common=found!=textures.end() && found->second;
            if(!common)found=textures.find(static_cast<uint32>(ids[i])|side);
            if(found==textures.end() || !found->second)return -1;
            value.textures[i]=found->second;
            if(i==0)value.side=common?0x60000000u:side;
        }
        value.rectangle.width=value.textures[0]->width();
        value.rectangle.height=value.textures[0]->height();
        value.rectangle.max_x=static_cast<uint16>(value.rectangle.width);
        value.rectangle.max_y=static_cast<uint16>(value.rectangle.height);
        // TraceManager defaults anisotropy to one; a60130's border is zero.
        if(!value.rectangle.width || !value.rectangle.height)return -1;
        layouts.push_back(value);
        return static_cast<int>(layouts.size()-1);
    }
    void submit(uint32 spaceID,const wot::StockTraceStamp& stamp,int group,unsigned& token)
    {
        syncSpace();
        if(!space || space->cleared() || space->id()!=spaceID)return;
        Vector3 start(stamp.position.x,stamp.position.y+1.f,stamp.position.z);
        Vector3 end(stamp.position.x,stamp.position.y-1.f,stamp.position.z);
        TraceProjectionHit query;
        float distance=space->collide(start,end,query);
        if(distance<0.f || !query.found)
        {
            const Vector3 offset(.25f,0.f,.25f);
            query.found=false;
            distance=space->collide(start+offset,end+offset,query);
            if(distance<0.f || !query.found)return;
        }
        // a932a50 uses the original unshifted ray even after the offset retry.
        Vector3 direction=end-start;direction.normalise();
        const Vector3 position=start+direction*distance;
        Vector3 normal=query.hit.normal()*-1.f;normal.normalise();
        if(normal.lengthSquared()==0.f)return;
        Vector3 up(1.f,0.f,0.f);
        if(std::fabs(1.f-std::fabs(normal.x))<.01f)up=Vector3(0.f,1.f,0.f);
        Matrix projection;
        projection.lookAt(position,normal,up);projection.postRotateZ(stamp.yaw);
        if(!projection.invert())return;
        Matrix scale;scale.setScale(stamp.width,stamp.length,.5f);
        projection.preMultiply(scale);
        wot::StockTraceSegment segment;
        segment.layout=layout(stamp,stamp.previous_albedo);
        if(segment.layout==-1)return;
        if(stamp.previous_albedo!=stamp.textures.albedo && stamp.blend!=0.f)
        {
            segment.blend_layout=layout(stamp,stamp.textures.albedo);
            segment.alpha=1.f-stamp.blend;
        }
        else segment.alpha=1.f;
        const Vector3 x=projection.applyToUnitAxisVector(0);
        Vector3 y=projection.applyToUnitAxisVector(1),z=projection.applyToUnitAxisVector(2);
        segment.position={position.x,position.y,position.z};
        segment.scale={x.length(),y.length(),z.length()};
        y.normalise();z.normalise();
        segment.up={z.x,z.y,z.z};segment.direction={y.x,y.y,y.z};
        Matrix orientation;orientation.lookAt(Vector3::zero(),z,y);
        Quaternion rotation;rotation.fromMatrix(orientation);
        segment.rotation={{rotation.x,rotation.y,rotation.z,rotation.w}};
        segment.lifetime=DynamicDecalManager::instance().getLifeTime(group);
        manager.add(token,segment);
    }
    bool drawGeometry(unsigned count)
    {
        auto* device=Moo::rc().device();
        UINT old0=1,old9=1,offset=0,stride=0;
        ComObjectWrap<DX::VertexBuffer> previous;
        ComObjectWrap<DX::VertexDeclaration> declaration;
        if(FAILED(device->GetStreamSourceFreq(0,&old0)) || FAILED(device->GetStreamSourceFreq(9,&old9)) ||
            FAILED(device->GetStreamSource(9,&previous,&offset,&stride)) ||
            FAILED(device->GetVertexDeclaration(&declaration)))return false;
        struct Restore
        {
            DX::Device* device;UINT first,last,offset,stride;
            ComObjectWrap<DX::VertexBuffer>& buffer;
            ComObjectWrap<DX::VertexDeclaration>& declaration;
            ~Restore()
            {
                device->SetStreamSourceFreq(0,first);device->SetStreamSourceFreq(9,last);
                device->SetStreamSource(9,buffer.pComObject(),offset,stride);
                Moo::rc().setVertexDeclaration(declaration.pComObject());
            }
        } restore={device,old0,old9,offset,stride,previous,declaration};
        if(FAILED(device->SetStreamSourceFreq(0,D3DSTREAMSOURCE_INDEXEDDATA|count)) ||
            FAILED(device->SetStreamSourceFreq(9,D3DSTREAMSOURCE_INSTANCEDATA|1)) ||
            FAILED(instances.set(9,0,sizeof(wot::StockTraceGpuInstance))))return false;
        Moo::Visual::ReadGuard guard(cube.getObject());
        for(auto& set:cube->renderSets())for(auto& geometry:set.geometry_)
        {
            if(!geometry.vertices_ || !geometry.primitives_ || FAILED(geometry.vertices_->setVertices(false,false)))return false;
            Moo::VertexDeclaration* combined=Moo::VertexDeclaration::combine(
                const_cast<Moo::VertexDeclaration*>(geometry.vertices_->pDecl()),instanceDeclaration);
            if(!combined || FAILED(Moo::rc().setVertexDeclaration(combined->declaration())) ||
                FAILED(geometry.primitives_->setPrimitives()))return false;
            for(const auto& group:geometry.primitiveGroups_)
                if(FAILED(geometry.primitives_->drawInstancedPrimitiveGroup(group.groupIndex_,count)))return false;
        }
        return true;
    }
    bool draw()
    {
        syncSpace();
        const auto packets=manager.packets(static_cast<unsigned>(layouts.size()));
        if(packets.empty())return true;
        StockDeferredBuffers* buffers=stockDeferredBuffers();
        if(!ready() || !buffers || !buffers->prepared())return false;
        DX::BaseTexture* kinds=buffers->objectKindCopy();
        if(!kinds || !Moo::rc().pushRenderTarget())return false;
        struct RestoreTarget{~RestoreTarget(){Moo::rc().popRenderTarget();}} target;
        IRendererPipeline* pipeline=Renderer::instance().pipeline();
        for(unsigned i=0;i!=3;++i)
            if(FAILED(Moo::rc().setRenderTarget(i,pipeline->gbufferSurface(i))))return false;
        if(FAILED(Moo::rc().setRenderTarget(3,NULL)))return false;
        const auto& size=Moo::rc().backBufferDesc();
        DX::Viewport viewport={0,0,size.Width,size.Height,0.f,1.f};
        if(FAILED(setStockSceneViewport(&viewport,true)))return false;
        std::map<int,std::vector<wot::StockTraceGpuInstance>> batches;
        for(const auto& packet:packets)
            batches[packet.layout].push_back(wot::stockTraceGpuInstance(packet,layouts.at(packet.layout).rectangle));
        for(const auto& batch:batches)
        {
            const auto& textures=layouts.at(batch.first).textures;
            const unsigned count=static_cast<unsigned>(batch.second.size());
            if(count>instanceCapacity)
            {
                instanceCapacity=std::max(count,instanceCapacity*2);
                if(FAILED(instances.create(instanceCapacity*sizeof(wot::StockTraceGpuInstance),
                    D3DUSAGE_DYNAMIC|D3DUSAGE_WRITEONLY,0,D3DPOOL_DEFAULT,"stock/vehicle trace instances")))
                {instanceCapacity=0;return false;}
            }
            void* destination=NULL;
            if(FAILED(instances.lock(0,count*sizeof(wot::StockTraceGpuInstance),&destination,D3DLOCK_DISCARD)))return false;
            std::memcpy(destination,batch.second.data(),count*sizeof(wot::StockTraceGpuInstance));
            if(FAILED(instances.unlock()) || !material->setTechnique("PBS_PARALLAX") ||
                !material->properties().select(*material->pEffect()))return false;
            ID3DXEffect* effect=material->pEffect()->pEffect();
            if(FAILED(effect->SetTexture("g_map1",textures[0]->pTexture())) ||
                FAILED(effect->SetTexture("g_map2",textures[1]->pTexture())) ||
                FAILED(effect->SetTexture("g_map3",textures[2]->pTexture())) ||
                FAILED(effect->SetTexture("g_map4",NULL)) || FAILED(effect->SetTexture("g_texObjKind",kinds)) ||
                FAILED(effect->SetInt("g_anisotropy",1)) || FAILED(effect->SetBool("g_pbsReady",true)) ||
                FAILED(effect->SetBool("g_useStencil",false)) || FAILED(effect->SetBool("g_cameraIsInsideDecal",false)) ||
                FAILED(effect->SetFloat("g_depthMipSkip",0.f)) || !setStockDeferredDecalConversion(effect) ||
                !material->begin())return false;
            bool success=material->numPasses()!=0;
            for(unsigned pass=0;success && pass!=material->numPasses();++pass)
            {
                if(!material->beginPass(pass)){success=false;break;}
                success=drawGeometry(count);success=material->endPass() && success;
            }
            success=material->end() && success;
            if(!success)return false;
        }
        return true;
    }
};
std::unique_ptr<TraceRenderer> traceRenderer;
void submitTraceStamp(uint32 space,const wot::StockTraceStamp& stamp,int group,unsigned& strip)
{
    if(traceRenderer)traceRenderer->submit(space,stamp,group,strip);
}

struct TraceState : StockComponentState
{
    bool active = false;
    WeakPyPtr<PyObjectPlus> compound;
    Vector4ProviderPtr movement;
    PyObjectPtr flying[2];
    PyObjectPtr lod;
    float maxDistance = FLT_MAX;
    unsigned maxPriority = ~0u;
    int group = -1;
    std::map<int,wot::StockTraceTextureSet> textures;
    wot::StockVehicleTraceSide sides[2];
    unsigned strips[2] = {~0u, ~0u};

    TraceState() { sides[1].left = true; }
    virtual void activate()
    {
        if (!valid_ || active) return;
        active = true;
        activeTraces.push_back(std::static_pointer_cast<TraceState>(shared_from_this()));
    }
    virtual void deactivate()
    {
        active = false;
        activeTraces.erase(std::remove_if(activeTraces.begin(),activeTraces.end(),
            [this](const std::weak_ptr<TraceState>& weak){return weak.lock().get()==this;}),activeTraces.end());
    }
    virtual void destroy()
    {
        deactivate();valid_=false;
        compound=WeakPyPtr<PyObjectPlus>();movement=NULL;lod=NULL;
        flying[0]=NULL;flying[1]=NULL;textures.clear();
    }
    virtual PyObject* wrap(bool owning);
    void update()
    {
        if (!valid_ || !active || !vehicleTracesEnabled) return;
        if (lod)
        {
            uint32 priority;float distance;
            if (!sampleStockLodLink(lod.get(),priority,distance) ||
                maxDistance<distance || maxPriority<priority) return;
        }
        PyObjectPtr model(const_cast<PyObject*>(compound.getPyObj()));
        Matrix authored,parent,world;
        uint32 space;BoundingBox bounds;
        if (!model || !stockVehicleCompoundContext(model.get(),space,bounds) ||
            !stockVehicleCompoundNode(model.get(),"",authored,parent,world)) return;
        if (!valid_ || !active || compound.getPyObj()!=model.get()) return;
        Vector4 delta(0,0,0,0);
        Vector4ProviderPtr source=movement;
        if (source) source->output(delta);
        if (!valid_ || !active || movement!=source) return;
        const Vector3 position=world.applyToOrigin();
        const Vector3 right=world.applyToUnitAxisVector(0);
        const Vector3 forward=world.applyToUnitAxisVector(2);
        const bool deferred=Renderer::instance().pipelineType()==IRendererPipeline::TYPE_DEFERRED_SHADING;
        for (unsigned index=0;index!=2;++index)
        {
            bool airborne=false;
            // sides[0] is right; the public API receives left, right links.
            PyObject* link=flying[index==0?1:0].get();
            if (link && !sampleStockBoolLink(link,airborne)) continue;
            sides[index].update({position.x,position.y,position.z},{right.x,right.y,right.z},
                {forward.x,forward.y,forward.z},delta.y,delta.z,airborne,deferred,
                [this,space,index](const wot::StockTraceStamp& stamp)
                {
                    submitTraceStamp(space,stamp,group,strips[index]);
                });
        }
    }
};
class VehicleTracesSetting : public Moo::GraphicsSetting
{
public:
    VehicleTracesSetting() : Moo::GraphicsSetting("VEHICLE_TRACES_ENABLED",
        "Vehicle Traces Enabled",0,false,false)
    {
        addOption("OFF","Off",true);addOption("ON","On",true,true);
    }
    virtual void onOptionSelected(int option) { vehicleTracesEnabled=option!=0; }
};
}
class PyVehicleTraces : public StockComponentWrapper
{
    Py_Header(PyVehicleTraces,StockComponentWrapper)
public:
    PyVehicleTraces(const std::shared_ptr<StockComponentState>& state,bool owning) :
        StockComponentWrapper(state,&s_type_,owning) {}
    std::shared_ptr<TraceState> state() const
    {
        auto value=std::dynamic_pointer_cast<TraceState>(component());
        if (!value) PyErr_SetString(PyExc_ValueError,"VehicleTraces has been destroyed");
        return value;
    }
    PY_FACTORY_DECLARE()
    PY_METHOD_DECLARE(py_setCompound)
    PY_METHOD_DECLARE(py_setMovementInfo)
    PY_METHOD_DECLARE(py_setTrackTraces)
    PY_METHOD_DECLARE(py_setCurrTerrainMatKinds)
    PY_METHOD_DECLARE(py_setFlyingInfo)
    PY_METHOD_DECLARE(py_setLodLink)
    PY_METHOD_DECLARE(py_setLodSettings)
};
PyObject* TraceState::wrap(bool owning) { return new PyVehicleTraces(shared_from_this(),owning); }
PyObject* PyVehicleTraces::pyNew(PyObject* args)
{
    if (!PyArg_ParseTuple(args,":VehicleTraces")) return NULL;
    return new PyVehicleTraces(std::make_shared<TraceState>(),true);
}
PyObject* PyVehicleTraces::py_setCompound(PyObject* args)
{
    PyObject* model;
    if (!PyArg_ParseTuple(args,"O:setCompound",&model)) return NULL;
    auto value=state();if (!value) return NULL;
    uint32 space;BoundingBox bounds;
    if (model!=Py_None && !stockVehicleCompoundContext(model,space,bounds))
    {
        PyErr_SetString(PyExc_TypeError,"setCompound requires a CompoundModel or None");return NULL;
    }
    value->compound=WeakPyPtr<PyObjectPlus>(model==Py_None?NULL:static_cast<PyObjectPlus*>(model));
    Py_RETURN_NONE;
}
PyObject* PyVehicleTraces::py_setMovementInfo(PyObject* args)
{
    PyObject* input;Vector4ProviderPtr provider;
    if (!PyArg_ParseTuple(args,"O:setMovementInfo",&input) || Script::setData(input,provider)) return NULL;
    auto value=state();if (!value) return NULL;
    value->movement=provider;Py_RETURN_NONE;
}
PyObject* PyVehicleTraces::py_setFlyingInfo(PyObject* args)
{
    PyObject *left,*right;
    if (!PyArg_ParseTuple(args,"OO:setFlyingInfo",&left,&right)) return NULL;
    bool sample;
    if ((left!=Py_None&&!sampleStockBoolLink(left,sample)) ||
        (right!=Py_None&&!sampleStockBoolLink(right,sample)))
    {
        PyErr_SetString(PyExc_TypeError,"setFlyingInfo requires native bool DataLinks or None");return NULL;
    }
    auto value=state();if (!value) return NULL;
    value->flying[0]=left==Py_None?NULL:left;
    value->flying[1]=right==Py_None?NULL:right;
    Py_RETURN_NONE;
}
PyObject* PyVehicleTraces::py_setLodLink(PyObject* args)
{
    PyObject* link;
    if (!PyArg_ParseTuple(args,"O:setLodLink",&link)) return NULL;
    uint32 priority;float distance;
    if (link!=Py_None&&!sampleStockLodLink(link,priority,distance))
    {
        PyErr_SetString(PyExc_TypeError,"setLodLink requires a native LodState DataLink or None");return NULL;
    }
    auto value=state();if (!value) return NULL;
    value->lod=link==Py_None?NULL:link;Py_RETURN_NONE;
}
PyObject* PyVehicleTraces::py_setLodSettings(PyObject* args)
{
    float distance;unsigned priority;
    if (!PyArg_ParseTuple(args,"(fI):setLodSettings",&distance,&priority)) return NULL;
    auto value=state();if (!value) return NULL;
    value->maxDistance=distance;value->maxPriority=priority;Py_RETURN_NONE;
}
PyObject* PyVehicleTraces::py_setCurrTerrainMatKinds(PyObject* args)
{
    PyObject *a,*b;int left,right;
    if (!PyArg_ParseTuple(args,"OO:setCurrTerrainMatKinds",&a,&b) ||
        Script::setData(a,left) || Script::setData(b,right)) return NULL;
    auto value=state();if (!value) return NULL;
    value->sides[0].setTerrain(right,value->textures);
    value->sides[1].setTerrain(left,value->textures);
    Py_RETURN_NONE;
}
PyObject* PyVehicleTraces::py_setTrackTraces(PyObject* args)
{
    PyObject *a,*mapping,*c,*d;BW::string group;float offset;Vector2 size;
    if (!PyArg_ParseTuple(args,"OOOO:setTrackTraces",&a,&mapping,&c,&d) ||
        Script::setData(a,group) || Script::setData(c,offset) || Script::setData(d,size)) return NULL;
    auto value=state();if (!value) return NULL;
    if (Renderer::instance().pipelineType()==IRendererPipeline::TYPE_FORWARD_SHADING) Py_RETURN_NONE;
    if (!PyDict_Check(mapping))
    {
        PyErr_SetString(PyExc_TypeError,"setTrackTraces - invalid <decalTextureSet> parameter type.");return NULL;
    }
    std::map<int,wot::StockTraceTextureSet> sets;
    PyObject *key,*entry;Py_ssize_t position=0;
    while (PyDict_Next(mapping,&position,&key,&entry))
    {
        const long kind=PyInt_AsLong(key);
        if (PyErr_Occurred()) return NULL;
        PyObjectPtr sequence(PySequence_Fast(entry,"Trace texture set must be a sequence"),PyObjectPtr::STEAL_REFERENCE);
        if (!sequence) return NULL;
        int ids[3]={-1,-1,-1};
        const Py_ssize_t count=std::min<Py_ssize_t>(3,PySequence_Fast_GET_SIZE(sequence.get()));
        for (Py_ssize_t index=0;index<count;++index)
        {
            ids[index]=static_cast<int>(PyInt_AsLong(PySequence_Fast_GET_ITEM(sequence.get(),index)));
            if (PyErr_Occurred()) return NULL;
            if (ids[index]==-1)
            {
                const char* names[]={"AM","ANM","GMM"};
                PyErr_Format(PyExc_TypeError,"setTrackTraces - invalid %s texture ID.",names[index]);return NULL;
            }
        }
        sets[static_cast<int>(kind)]={ids[0],ids[1],ids[2]};
    }
    const int groupIndex=DynamicDecalManager::instance().getGroupIndex(group);
    if (groupIndex==-1)
    {
        PyErr_Format(PyExc_TypeError,"setTrackTraces - invalid <groupName> parameter. groupName = %s.",group.c_str());return NULL;
    }
    if (size.x<=0.f||size.y<=0.f)
    {
        PyErr_Format(PyExc_TypeError,"setTrackTraces - invalid <trackSize> parameter. trackSize = (%.3f, %.3f).",size.x,size.y);return NULL;
    }
    value->group=groupIndex;value->textures.swap(sets);
    for (auto& side:value->sides)
    {
        side.terrain_kind=-1;side.textures.albedo=-1;
        side.center_offset=offset;side.width=size.x;side.length=size.y;
        side.signed_movement=0.f;side.last_chassis={0.f,0.f,0.f};
    }
    Py_RETURN_NONE;
}
PY_TYPEOBJECT(PyVehicleTraces)
PY_BEGIN_METHODS(PyVehicleTraces)
    PY_METHOD(setCompound)
    PY_METHOD(setMovementInfo)
    PY_METHOD(setTrackTraces)
    PY_METHOD(setCurrTerrainMatKinds)
    PY_METHOD(setFlyingInfo)
    PY_METHOD(setLodLink)
    PY_METHOD(setLodSettings)
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES(PyVehicleTraces)
PY_END_ATTRIBUTES()
PY_FACTORY_NAMED(PyVehicleTraces,"VehicleTraces",Vehicular)

void tickStockVehicleTraces()
{
    const auto snapshot=activeTraces;
    for (const auto& weak:snapshot)
    {
        auto value=weak.lock();
        if (value) value->update();
    }
}
bool initialiseStockVehicleTracesSetting()
{
    auto existing=Moo::GraphicsSetting::getFromLabel("VEHICLE_TRACES_ENABLED");
    if (existing)
    {
        if (dynamic_cast<VehicleTracesSetting*>(existing.getObject())) return true;
        PyErr_SetString(PyExc_RuntimeError,"Vehicle traces setting already has another owner");return false;
    }
    Moo::GraphicsSetting::add(new VehicleTracesSetting);
    return true;
}
bool initialiseStockVehicleTraceRenderer()
{
    if(Renderer::instance().pipelineType()==IRendererPipeline::TYPE_FORWARD_SHADING)return true;
    if(!traceRenderer)
    {
        traceRenderer.reset(new TraceRenderer);
        traceRenderer->createManagedObjects();
        const auto quality=Moo::GraphicsSetting::getFromLabel("DECALS_QUALITY");
        if(quality && quality->activeOption()>=0)
            traceRenderer->setQuality(static_cast<unsigned>(quality->activeOption()));
    }
    return traceRenderer->ready();
}
void finaliseStockVehicleTraceRenderer(){traceRenderer.reset();}
void tickStockVehicleTraceRenderer(float elapsed){if(traceRenderer)traceRenderer->tick(elapsed);}
void setStockVehicleTraceQuality(unsigned option){if(traceRenderer)traceRenderer->setQuality(option);}
bool drawStockVehicleTraces()
{
    if(Renderer::instance().pipelineType()==IRendererPipeline::TYPE_FORWARD_SHADING)return true;
    return traceRenderer && traceRenderer->draw();
}
BW_END_NAMESPACE
