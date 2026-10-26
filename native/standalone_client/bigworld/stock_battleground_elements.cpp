#include "pch.hpp"
#include "stock_battleground_elements.hpp"
#include "stock_environment_constants.hpp"
#include "connection_control.hpp"
#include "connection_model/bw_connection.hpp"
#include "duplo/py_material_fashion.hpp"
#include "duplo/pymodel.hpp"
#include "model/model.hpp"
#include "model/super_model.hpp"
#include "moo/complex_effect_material.hpp"
#include "moo/effect_property.hpp"
#include "moo/fullscreen_quad.hpp"
#include "moo/moo_utilities.hpp"
#include "moo/render_target.hpp"
#include "moo/texture_manager.hpp"
#include "moo/fog_helper.hpp"
#include "moo/render_context.hpp"
#include "moo/renderer.hpp"
#include "pyscript/script_math.hpp"
#include "pyscript/py_data_section.hpp"
#include "resmgr/xml_section.hpp"
#include "particle/renderers/sprite_particle_renderer.hpp"
#include "particle/renderers/particle_system_draw_item.hpp"
#include <memory>
#include <set>
#include <vector>

BW_BEGIN_NAMESPACE
int StockBattlegroundElements_token = 1;
namespace
{
struct CloudState;
std::vector<std::weak_ptr<CloudState>> clouds;
std::weak_ptr<CloudState> fogOwner;


wot::StockFogSettings mixFog( const wot::StockFogSettings & a,
    const wot::StockFogSettings & b, float amount )
{
    wot::StockFogSettings result;
    for (unsigned i = 0; i != 41; ++i)
    {
        result.deferred[i] = (b.deferred[i] - a.deferred[i]) * amount + a.deferred[i];
        result.forward[i] = (b.forward[i] - a.forward[i]) * amount + a.forward[i];
    }
    return result;
}

wot::StockFogSettings fogFromSection( DataSectionPtr section )
{
    wot::StockFogSettings result;
    if (!section) return result;
    // The original tuple holds the Fog section, rather than its environment.
    DataSectionPtr root = new XMLSection( "environment" );
    root->newSection( "Fog" )->copy( section );
    loadStockFogSettings( root, result );
    return result;
}

void callSound( PyObject * sound, const char * method )
{
    if (!sound) return;
    PyObjectPtr result( PyObject_CallMethod( sound, const_cast<char *>( method ), NULL ),
        PyObjectPtr::STEAL_REFERENCE );
    if (!result) PyErr_Print();
}

struct CloudState
{
    Vector3 position;
    double startTime = 0;
    float scalar[15] = {};
    DataSectionPtr sections[5];
    Moo::BaseTexturePtr displacement;
    Vector4 windDir, waveSpeed, spreadFreq;
    wot::StockFogSettings originalFog, centerFog, edgeFog, transitionFog;
    Matrix matrix;
    wot::StockTornadoSplitter splitter;
    Vector4 tint = Vector4( 0.f, 0.f, 0.f, 0.f );
    PyObjectPtr callback;
    PyObjectPtr sounds[2];
    MatrixProviderPtr soundMatrices[2];
    bool active = false;
    bool proximity = false;
    bool enableEdgeFogEffects = true;
    float shimmerWeight = 0.f;
    CloudState() { matrix.setIdentity(); }
    ~CloudState() { stop(); }
    bool prepareSounds();
    void stop()
    {
        active = false;
        for (unsigned i = 0; i != 2; ++i) callSound( sounds[i].get(), "stop" );
        const auto owner = fogOwner.lock();
        if (owner.get() == this)
        {
            if (Moo::FogHelper::pInstance()) Moo::FogHelper::pInstance()->stockFogSettings( originalFog );
            fogOwner.reset();

        }
    }
    void update();
};

// The providers own shared native state, so a retained Servo/tint cannot read a
// freed GasCloud. They do not retain the Python GasCloud or its callback.
class CloudMatrix : public MatrixProvider
{
    Py_Header( CloudMatrix, MatrixProvider )
public:
    explicit CloudMatrix( const std::shared_ptr<CloudState> & state ) :
        MatrixProvider( false, &s_type_ ), state_( state ) {}
    virtual void matrix( Matrix & value ) const { value = state_->matrix; }
private:
    std::shared_ptr<CloudState> state_;
};
PY_TYPEOBJECT( CloudMatrix )
PY_BEGIN_METHODS( CloudMatrix )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( CloudMatrix )
PY_END_ATTRIBUTES()

class CloudTintLink : public Vector4Provider
{
    Py_Header( CloudTintLink, Vector4Provider )
public:
    explicit CloudTintLink( const std::shared_ptr<CloudState> & state ) :
        Vector4Provider( false, &s_type_ ), state_( state ) {}
    virtual void output( Vector4 & value ) { value = state_->tint; }
private:
    std::shared_ptr<CloudState> state_;
};
PY_TYPEOBJECT( CloudTintLink )
PY_BEGIN_METHODS( CloudTintLink )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( CloudTintLink )
PY_END_ATTRIBUTES()

bool CloudState::prepareSounds()
{
    PyObjectPtr module( PyImport_ImportModule( "_WWISE" ), PyObjectPtr::STEAL_REFERENCE );
    if (!module) return false;
    const char * keys[] = { "gascloud_close", "gascloud_far" };
    const char * names[] = { "GasCloudSoundClose", "GasCloudSoundFar" };
    for (unsigned i = 0; i != 2; ++i)
    {
        const BW::string event = sections[4] ? sections[4]->readString( keys[i] ) : BW::string();
        if (event.empty()) continue;
        PyMatrix * provider = new PyMatrix();
        soundMatrices[i] = provider;
        PyObjectPtr zero( Script::getData( Vector3::zero() ), PyObjectPtr::STEAL_REFERENCE );
        PyObjectPtr sound( PyObject_CallMethod( module.get(), "WW_getSound", "ssOO",
            event.c_str(), names[i], provider, zero.get() ), PyObjectPtr::STEAL_REFERENCE );
        if (!sound) return false;
        sounds[i] = sound;
    }
    return true;
}

void CloudState::update()
{
    if (!active) return;
    const Vector3 camera = Moo::rc().invView().applyToOrigin();
    BWConnection * connection = ConnectionControl::instance().pConnection();
    const auto sample = wot::sampleStockGasCloud( connection ? connection->serverTime() : 0.0,
        startTime, scalar[1], scalar[2], scalar[3], scalar[4], scalar[5], scalar[6],
        scalar[0], position.x, position.z, camera.x, camera.z );
    matrix.setScale( sample.radius * 2.f, sample.radius * 2.f, sample.radius * 2.f );
    matrix.translation( Vector3( position.x, sample.height, position.z ) );
    tint = Vector4( 1.f, 1.f, 1.f, sample.alpha );
    splitter.prepare( position.x, position.z, sample.radius,
        sample.radius * sample.radius * 1.5f + sample.height, camera.x, camera.y, camera.z );
    Vector3 radial( position.x - camera.x, 0.f, position.z - camera.z );
    radial.normalise();
    for (unsigned i = 0; i != 2; ++i)
    {
        if (soundMatrices[i])
        {
            PyMatrix * provider = static_cast<PyMatrix *>( soundMatrices[i].getObject() );
            provider->setTranslate( Vector3( position.x, camera.y, position.z ) +
                radial * (i ? sample.radius : -sample.radius) );
        }
        if (sample.proximity != proximity)
            callSound( sounds[i].get(), sample.proximity ? "stop" : "play" );
    }
    const bool changed = sample.proximity != proximity;
    proximity = sample.proximity;
    if (fogOwner.lock().get() == this)
    {
        const float climb = sample.alpha;
        auto inner = mixFog( originalFog, centerFog, climb );
        auto outer = edgeFog;
        auto transition = transitionFog;
        const auto defaults = wot::StockFogSettings::defaults( false );
        for (unsigned i = 0; i != 4; ++i)
        {
            const float bound = (i == 0 || i == 2 ? position.x : position.z) +
                (i < 2 ? -sample.radius : sample.radius);
            const float clipped = i < 2 ? std::max( defaults[16 + i], bound ) :
                std::min( defaults[16 + i], bound );
            outer.forward[16 + i] = transition.forward[16 + i] = clipped;
        }
        float centerBlend = 0.f;
        if (sample.proximity && enableEdgeFogEffects)
        {
            Vector3 direction = Moo::rc().invView().applyToUnitAxisVector( 2 );
            direction.y = 0.f;
            direction.normalise();
            const Vector3 towardCenter = position - camera;
            const float length = towardCenter.length();
            const float angle = length > 0.f ? acosf( Math::clamp( -1.f,
                direction.dotProduct( towardCenter ) / length, 1.f ) ) : 0.f;
            if (angle < scalar[7])
            {
                const float distance = Math::clamp( 0.f, -sample.edgeDistance / scalar[9], 1.f );
                const float angular = Math::clamp( 0.f, (angle - scalar[7]) / scalar[8] + 1.f, 1.f );
                centerBlend = (1.f - angular) * (1.f - distance);
                outer = mixFog( outer, transition, centerBlend );
            }
            const float weight = sample.edgeDistance > 0.001f ?
                std::min( 1.f - sample.edgeDistance / scalar[0], 1.f ) : 1.f;
            inner = mixFog( inner, outer, weight );
            shimmerWeight = (1.f - centerBlend) * weight;
        }
        else shimmerWeight = 0.f;
        Moo::FogHelper::pInstance()->stockFogSettings( inner );
    }
    // Publish state before entering Python: callback may stop or release us.
    if (changed && callback)
    {
        PyObjectPtr hold = callback;
        PyObjectPtr result( PyObject_CallFunction( hold.get(), "O", proximity ? Py_True : Py_False ),
            PyObjectPtr::STEAL_REFERENCE );
        if (!result) PyErr_Print();
    }
}
}

class GasCloud : public PyObjectPlus
{
    Py_Header( GasCloud, PyObjectPlus )
public:
    explicit GasCloud( const std::shared_ptr<CloudState> & state ) :
        PyObjectPlus( &s_type_ ), state_( state ), tint_( new CloudTintLink( state ) ) {}
    virtual ~GasCloud() { state_->stop(); state_->callback = NULL; }
    PY_METHOD_DECLARE( py_start )
    PY_METHOD_DECLARE( py_stop )
    PY_METHOD_DECLARE( py_setProximityCallback )
    PyObject * pyGet_cloudMatrixProvider() { return new CloudMatrix( state_ ); }
    PyObject * pyGet_tintColorLink() { return Script::getData( tint_ ); }
    PyObject * pyGet_enableEdgeFogEffects() { return Script::getData( state_->enableEdgeFogEffects ); }
    int pySet_enableEdgeFogEffects( PyObject * value )
    { return Script::setData( value, state_->enableEdgeFogEffects, "enableEdgeFogEffects" ); }
    PY_RO_ATTRIBUTE_SET( cloudMatrixProvider )
    PY_RO_ATTRIBUTE_SET( tintColorLink )
    PY_FACTORY_DECLARE()
private:
    std::shared_ptr<CloudState> state_;
    Vector4ProviderPtr tint_;
};

PyObject * GasCloud::pyNew( PyObject * args )
{
    PyObject * settings;
    if (!PyArg_ParseTuple( args, "O:GasCloud", &settings )) return NULL;
    if (!PyTuple_Check( settings ) || PyTuple_GET_SIZE( settings ) != 21)
    {
        PyErr_SetString( PyExc_TypeError, "GasCloudSettings must contain 21 fields" );
        return NULL;
    }
    const auto state = std::make_shared<CloudState>();
    if (Script::setData( PyTuple_GET_ITEM( settings, 0 ), state->position, "settings.position" ) ||
        Script::setData( PyTuple_GET_ITEM( settings, 1 ), state->startTime, "settings.startTime" )) return NULL;
    for (unsigned i = 0; i != 14; ++i)
        if (Script::setData( PyTuple_GET_ITEM( settings, i + 2 ), state->scalar[i], "GasCloudSettings" )) return NULL;
    for (unsigned i = 0; i != 5; ++i)
    {
        PyObject * object = PyTuple_GET_ITEM( settings, i + 16 );
        if (object == Py_None) continue;
        if (!PyDataSection::Check( object ))
        {
            PyErr_SetString( PyExc_TypeError, "GasCloudSettings requires native DataSections" );
            return NULL;
        }
        state->sections[i] = static_cast<PyDataSection *>( object )->pSection();
    }
    if (!Moo::FogHelper::pInstance())
    {
        PyErr_SetString( PyExc_RuntimeError, "GasCloud requires an active native fog owner" );
        return NULL;
    }
    state->originalFog = Moo::FogHelper::pInstance()->stockFogSettings();
    if (state->sections[2])
    {
        state->centerFog = fogFromSection( state->sections[1] );
        state->edgeFog = fogFromSection( state->sections[2] );
        state->transitionFog = fogFromSection( state->sections[3] );
    }
    else state->centerFog = state->edgeFog = state->transitionFog = state->originalFog;
    if (state->sections[0])
    {
        state->windDir = state->sections[0]->readVector4( "windDir" );
        state->waveSpeed = state->sections[0]->readVector4( "waveSpeed" );
        state->spreadFreq = state->sections[0]->readVector4( "spreadFreq" );
        const BW::string resource = state->sections[0]->readString( "displacement" );
        state->displacement = resource.empty() ? NULL : Moo::TextureManager::instance()->get(
            resource, true, true, true, "Art/File Textures/" );
        if (!state->displacement || state->displacement->status() != Moo::BaseTexture::STATUS_READY)
        {
            PyErr_SetString( PyExc_RuntimeError, "GasCloud displacement texture could not be loaded" );
            return NULL;
        }
    }
    if (!state->prepareSounds()) return NULL;
    clouds.push_back( state );
    return new GasCloud( state );
}
PyObject * GasCloud::py_start( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":start" )) return NULL;
    state_->active = true;
    fogOwner = state_;
    for (unsigned i = 0; i != 2; ++i) callSound( state_->sounds[i].get(), "play" );
    Py_RETURN_NONE;
}
PyObject * GasCloud::py_stop( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":stop" )) return NULL;
    state_->stop();
    Py_RETURN_NONE;
}
PyObject * GasCloud::py_setProximityCallback( PyObject * args )
{
    PyObject * callback;
    if (!PyArg_ParseTuple( args, "O:setProximityCallback", &callback )) return NULL;
    if (!PyCallable_Check( callback ))
    {
        PyErr_SetString( PyExc_TypeError, "setProximityCallback requires a callable" );
        return NULL;
    }
    state_->callback = callback;
    Py_RETURN_NONE;
}
PY_TYPEOBJECT( GasCloud )
PY_BEGIN_METHODS( GasCloud )
    PY_METHOD( start )
    PY_METHOD( stop )
    PY_METHOD( setProximityCallback )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( GasCloud )
    PY_ATTRIBUTE( cloudMatrixProvider )
    PY_ATTRIBUTE( tintColorLink )
    PY_ATTRIBUTE( enableEdgeFogEffects )
PY_END_ATTRIBUTES()
PY_FACTORY( GasCloud, BattlegroundElements )

namespace
{
class CloudMaterial : public MaterialFashion
{
public:
    Vector4ProviderPtr link;
    virtual void dress( SuperModel & super )
    {
        Vector4 value( 1.f, 1.f, 1.f, 1.f );
        if (link) link->output( value );
        for (int part = 0; part < super.nModels(); ++part)
            for (Model * level = super.topModel( part ).getObject(); level; level = level->parent())
            {
                BW::vector<Moo::Visual::PrimitiveGroup *> groups;
                level->gatherMaterials( "*", groups );
                for (auto * group : groups)
                    if (group && group->material_ && group->material_->baseMaterial())
                        group->material_->baseMaterial()->setProperty( "TintlColor", value, false );
            }
    }
};
}
class TintFashion : public PyMaterialFashion
{
    Py_Header( TintFashion, PyMaterialFashion )
public:
    explicit TintFashion( Vector4ProviderPtr link ) : PyMaterialFashion( &s_type_ ), fashion_( new CloudMaterial() )
    { fashion_->link = link; }
    virtual const MaterialFashionPtr materialFashion() const { return fashion_; }
    virtual MaterialFashionPtr materialFashion() { return fashion_; }
    PyObject * pyGet_tintColorLink() { return Script::getData( fashion_->link ); }
    int pySet_tintColorLink( PyObject * value ) { return Script::setData( value, fashion_->link, "tintColorLink" ); }
    PY_FACTORY_DECLARE()
private:
    SmartPointer<CloudMaterial> fashion_;
};
PyObject * TintFashion::pyNew( PyObject * args )
    {
        PyObject * link;
        if (!PyArg_ParseTuple( args, "O:TintFashion", &link )) return NULL;
        Vector4ProviderPtr provider;
        if (Script::setData( link, provider, "tintColorLink" )) return NULL;
        return new TintFashion( provider );
    }
PY_TYPEOBJECT( TintFashion )
PY_BEGIN_METHODS( TintFashion )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( TintFashion )
    PY_ATTRIBUTE( tintColorLink )
PY_END_ATTRIBUTES()
PY_FACTORY( TintFashion, BattlegroundElements )

namespace
{
// #1513 008a4320/008a44d0: a real scene copy and original full-screen shader.
// Managed and default-pool resources follow the public D3D device lifecycle.
class FireStormRenderer : public Moo::DeviceCallback
{
public:
    ~FireStormRenderer() { deleteUnmanagedObjects(); deleteManagedObjects(); }
    virtual void createManagedObjects()
    {
        if (!Moo::createEffect( material_, "shaders/custom/fire_storm_post_effect.fx" ))
            ERROR_MSG( "BattlegroundElements: cannot load original fire storm shader\n" );
    }
    virtual void deleteManagedObjects() { material_ = NULL; }
    virtual void deleteUnmanagedObjects()
    {
        if (material_ && material_->pEffect())
            material_->pEffect()->pEffect()->SetTexture( "inputTexture", NULL );
        scene_ = NULL;
    }
    virtual bool recreateForD3DExDevice() const { return true; }
    bool draw( const CloudState & state )
    {
        if (!material_) createManagedObjects();
        if (!material_ || !state.displacement) return false;
        ComObjectWrap<DX::Surface> source;
        D3DSURFACE_DESC size;
        if (FAILED( Moo::rc().device()->GetRenderTarget( 0, &source ) ) ||
            FAILED( source->GetDesc( &size ) )) return false;
        if (!scene_ || scene_->width() != size.Width || scene_->height() != size.Height ||
            scene_->format() != size.Format)
        {
            deleteUnmanagedObjects();
            scene_ = new Moo::RenderTarget( "Stock fire storm scene" );
            if (!scene_->create( size.Width, size.Height, false, size.Format )) return false;
        }
        ComObjectWrap<DX::Surface> destination;
        if (FAILED( scene_->pSurface( destination ) ) ||
            FAILED( Moo::rc().device()->StretchRect( source.pComObject(), NULL,
                destination.pComObject(), NULL, D3DTEXF_NONE ) ) ||
            !material_->setTechnique( "standard" ) ||
            !material_->properties().select( *material_->pEffect() )) return false;
        ID3DXEffect * effect = material_->pEffect()->pEffect();
        Vector4 wave = state.waveSpeed;
        wave.w = state.shimmerWeight;
        const bool bound = SUCCEEDED( effect->SetVector( "windDir", &state.windDir ) ) &&
            SUCCEEDED( effect->SetVector( "waveSpeed", &wave ) ) &&
            SUCCEEDED( effect->SetVector( "spreadFreq", &state.spreadFreq ) ) &&
            SUCCEEDED( effect->SetTexture( "displacementTexture", state.displacement->pTexture() ) ) &&
            SUCCEEDED( effect->SetTexture( "inputTexture", scene_->pTexture() ) );
        const bool drawn = bound && Moo::rc().fsQuad().drawChecked( *material_, size.Width, size.Height );
        effect->SetTexture( "inputTexture", NULL );
        effect->SetTexture( "displacementTexture", NULL );
        return drawn;
    }
private:
    Moo::EffectMaterialPtr material_;
    Moo::RenderTargetPtr scene_;
};
std::unique_ptr<FireStormRenderer> fireStormRenderer;
struct DeferredCloudParticles
{
    ParticleSystemRendererPtr renderer;
    Matrix world;
    ParticlesPtr particles;
};
std::vector<DeferredCloudParticles> deferredParticles;
const Moo::DrawContext * mainParticlePhase = NULL;

}

bool drawStockBattlegroundParticles( ParticleSystemRenderer * renderer, const Matrix & world,
    Particles::iterator begin, Particles::iterator end )
{
    const auto state = fogOwner.lock();
    // The recovered #1513 splitter is in the generated sprite-quad path. Mesh
    // and connected trail topology retain their renderer's existing ownership.
    if (!mainParticlePhase || !state || !state->active ||
        !dynamic_cast<SpriteParticleRenderer *>( renderer )) return false;
    ParticlesPtr first = new ContiguousParticles();
    ParticlesPtr second = new ContiguousParticles();
    first->reserve( end - begin );
    second->reserve( end - begin );
    for (auto particle = begin; particle != end; ++particle)
    {
        if (!particle->isAlive()) continue;
        Vector3 point = particle->position();
        if (renderer->viewDependent()) point = Moo::rc().invView().applyPoint( point );
        else if (renderer->local()) point = world.applyPoint( point );
        const bool bucket = state->splitter.classify( point.x, point.y, point.z );
        (bucket ? second : first)->addParticle( *particle, false );
    }
    if (first->size()) renderer->realDraw( world, first->begin(), first->end() );
    if (second->size())
    {
        DeferredCloudParticles draw;
        draw.renderer = renderer;
        draw.world = world;
        draw.particles = second;
        deferredParticles.push_back( draw );
    }
    return true;
}

void beginStockBattlegroundParticles( const Moo::DrawContext * context )
{
    const auto state = fogOwner.lock();
    mainParticlePhase = state && state->active ? context : NULL;
}
bool stockBattlegroundParticlePhase( const Moo::DrawContext * context )
{
    return mainParticlePhase && mainParticlePhase == context;
}
bool isStockBattlegroundParticleItem( Moo::DrawContext::UserDrawItem * item )
{
    const auto particle = dynamic_cast<ParticleSystemDrawItem *>( item );
    return particle && particle->isBattlegroundSprite();
}
void drawStockBattlegroundParticleItem( Moo::DrawContext::UserDrawItem * item )
{
    ParticleSystemDrawItem * particle = static_cast<ParticleSystemDrawItem *>( item );
    particle->drawBattlegroundSplit();
}
void finishStockBattlegroundParticles()
{
    mainParticlePhase = NULL;
    std::vector<DeferredCloudParticles> pending;
    pending.swap( deferredParticles );
    for (const auto & draw : pending)
        draw.renderer->realDraw( draw.world, draw.particles->begin(), draw.particles->end() );
}

bool drawStockBattlegroundElements()
{
    const auto state = fogOwner.lock();
    bool result = true;
    if (state && state->active && state->enableEdgeFogEffects && state->shimmerWeight > 0.f &&
        Renderer::instance().pipelineType() == IRendererPipeline::TYPE_DEFERRED_SHADING)
    {
        if (!fireStormRenderer) fireStormRenderer.reset( new FireStormRenderer() );
        result = fireStormRenderer->draw( *state );
    }
    return result;
}

void tickStockBattlegroundElements()
{
    std::vector<std::shared_ptr<CloudState>> live;
    for (auto iterator = clouds.begin(); iterator != clouds.end(); )
    {
        const auto state = iterator->lock();
        if (!state) iterator = clouds.erase( iterator );
        else { live.push_back( state ); ++iterator; }
    }
    for (const auto & state : live) state->update();
}
void finiStockBattlegroundElements()
{
    for (const auto & weak : clouds)
        if (const auto state = weak.lock())
        {
            state->stop();
            state->callback = NULL;
            for (unsigned i = 0; i != 2; ++i)
            {
                state->sounds[i] = NULL;
                state->soundMatrices[i] = NULL;
            }
        }
    clouds.clear();
    mainParticlePhase = NULL;
    deferredParticles.clear();
    fireStormRenderer.reset();
}
BW_END_NAMESPACE
