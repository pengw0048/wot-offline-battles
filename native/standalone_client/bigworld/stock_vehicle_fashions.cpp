#include "pch.hpp"
#include "stock_vehicle_fashions.hpp"
#include "app.hpp"

#include "duplo/py_material_fashion.hpp"
#include "duplo/pymodel.hpp"
#include "model/model.hpp"
#include "model/super_model.hpp"
#include "moo/complex_effect_material.hpp"
#include "moo/effect_property.hpp"
#include "moo/managed_effect.hpp"
#include "moo/texture_manager.hpp"
#include "resmgr/xml_section.hpp"
#include "resmgr/bwresource.hpp"
#include "pyscript/script_math.hpp"

#include <cmath>
#include <memory>
#include <set>
#include <vector>

BW_BEGIN_NAMESPACE
int StockVehicleFashions_token = 1;

namespace
{
// Exact #1513 00a36b60. The public renderer publishes a distance LOD rather
// than the compiled renderer's inverse screen area. Both the authored
// thresholds and that real distance are projected through this same kernel.
float vehicleScreenArea( const BoundingBox & bounds, float distance )
{
    const Vector3 & low = bounds.minBounds();
    const Vector3 & high = bounds.maxBounds();
    const bool invalid = high.x < low.x || high.y < low.y || high.z < low.z;
    const float width = invalid ? 0.f : high.x - low.x;
    const float height = invalid ? 0.f : high.y - low.y;
    const float depth = invalid ? 0.f : high.z - low.z;
    float diameter = depth <= height ? height : depth;
    if (diameter <= width)
        diameter = width;
    const float radiusSquared = diameter * 0.5f * diameter * 0.5f;
    float z = (high.z + low.z) * 0.5f + distance;
    z *= z;
    float denominator = z - radiusSquared;
    if (denominator <= radiusSquared * 0.05f)
        denominator = radiusSquared * 0.05f;
    float numerator = ((high.y + low.y) * 0.25f * (high.y + low.y) +
        (low.x + high.x) * 0.25f * (low.x + high.x) + z) - radiusSquared;
    if (numerator <= radiusSquared * 0.1f)
        numerator = radiusSquared * 0.1f;
    const float inverse = 1.f / denominator;
    return sqrtf( numerator * inverse ) * radiusSquared * 1.7671458f * inverse;
}

struct VehicleTrackTextureState
{
    float left = 0.f;
    float right = 0.f;

    // 00af21a0 uses a signed fractional remainder. The discontinuity guard
    // tests the signed left delta, not its magnitude or the right delta.
    void update( float leftDelta, float rightDelta, float scale, unsigned hidden )
    {
        if (leftDelta > 100.f)
        {
            left = right = 0.f;
            return;
        }
        if (!(hidden & 1))
        {
            left += scale * leftDelta;
            left = left > 0.f ? left - floorf( left ) : left + floorf( -left );
        }
        if (!(hidden & 2))
        {
            right += scale * rightDelta;
            right = right > 0.f ? right - floorf( right ) : right + floorf( -right );
        }
    }
};

bool excludedFromCamouflage( const BW::string & material )
{
    // 005c0239 configures system/scriptsConfigXML; 00972a47 appends
    // /camouflage/excludeMaterials to that same public App config value.
    // 00af05c0 reads item strings. 00af0f60 treats only a leading '*' as
    // substring matching. Keep the rules in the original resource.
    DataSectionPtr section = BWResource::openSection(
        s_scriptsConfigXML.value() + "/camouflage/excludeMaterials" );
    if (!section)
        return false;
    BW::vector<DataSectionPtr> items;
    section->openSections( "item", items );
    for (size_t index = 0; index < items.size(); ++index)
    {
        const BW::string pattern = items[index]->asString();
        if ((!pattern.empty() && pattern[0] == '*' &&
                material.find( pattern.substr( 1 ) ) != BW::string::npos) ||
            material == pattern)
            return true;
    }
    return false;
}

Moo::ComplexEffectMaterialPtr cloneMaterial( const Moo::ComplexEffectMaterialPtr & source )
{
    Moo::ComplexEffectMaterialPtr result = new Moo::ComplexEffectMaterial( *source );
    // EffectMaterial's public copy constructor shares EffectPropertyPtr.
    // Clone the properties before any UV/alpha/handler write can occur.
    Moo::EffectMaterial::Properties & properties = result->baseMaterial()->properties();
    for (Moo::EffectMaterial::Properties::iterator it = properties.begin();
        it != properties.end(); ++it)
        if (it->second)
            it->second = it->second->clone();
    return result;
}

Moo::ComplexEffectMaterialPtr materialVariant(
    const Moo::ComplexEffectMaterialPtr & source, const BW::string & suffix )
{
    const Moo::ManagedEffectPtr & effect = source->baseMaterial()->pEffect();
    if (!effect)
        return Moo::ComplexEffectMaterialPtr();
    BW::string resource = effect->resourceID();
    if (resource.size() >= suffix.size() &&
        resource.compare( resource.size() - suffix.size(), suffix.size(), suffix ) == 0)
        return cloneMaterial( source );
    const BW::string::size_type extension = resource.find_last_of( '.' );
    if (extension == BW::string::npos)
        return Moo::ComplexEffectMaterialPtr();
    resource.erase( extension );
    resource += suffix;
    DataSectionPtr section = new XMLSection( "material" );
    source->baseMaterial()->save( section );
    section->writeString( "fx", resource );
    section->writeString( "identifier", source->identifier() );
    Moo::ComplexEffectMaterialPtr result = new Moo::ComplexEffectMaterial();
    if (!result->load( section ))
        return Moo::ComplexEffectMaterialPtr();
    return result;
}

Vector4 packedVehicleColour( uint32 colour )
{
    // Both native handlers use byte order B, G, R, A in shader vectors.
    const float scale = 0.003921569f;
    return Vector4( static_cast<float>( colour & 255U ) * scale,
        static_cast<float>( (colour >> 8) & 255U ) * scale,
        static_cast<float>( (colour >> 16) & 255U ) * scale,
        static_cast<float>( colour >> 24 ) * scale );
}

class VehicleMaterialHandlerState
{
public:
    virtual ~VehicleMaterialHandlerState() {}
    virtual void apply( Moo::EffectMaterial & material ) const = 0;
};

struct VehicleCamoState : public VehicleMaterialHandlerState
{
    Moo::BaseTexturePtr tint;
    Moo::BaseTexturePtr exclusion;
    Vector4 tiling = Vector4( 0.f, 0.f, 0.f, 0.f );
    Vector4 weights = Vector4( 0.f, 0.f, 0.f, 0.f );
    uint32 colours[4] = { 0, 0, 0, 0 };

    virtual void apply( Moo::EffectMaterial & material ) const
    {
        if (tint)
            material.setProperty( "tintMap", tint, false );
        if (exclusion)
            material.setProperty( "exclusionMap", exclusion, false );
        material.setProperty( "tintTiling", tiling, false );
        Moo::BaseTexturePtr ids;
        Moo::EffectPropertyPtr idProperty = material.getProperty( "colorIdMap" );
        if (idProperty)
            idProperty->getTexture( ids );
        material.setProperty( "g_useOldCamoDetail", !ids, false );
        static const char * names[] = { "colorTint1", "colorTint2", "colorTint3", "colorTint4" };
        for (size_t index = 0; index < 4; ++index)
        {
            Vector4 colour = packedVehicleColour( colours[index] );
            colour.w = weights[index];
            material.setProperty( names[index], colour, false );
        }
    }
};

struct VehicleRepaintState : public VehicleMaterialHandlerState
{
    bool enabled = false;
    Vector4 base = Vector4( 0.f, 0.f, 0.f, 0.f );
    Vector4 colours[3] = { Vector4::zero(), Vector4::zero(), Vector4::zero() };
    Vector4 metallic = Vector4( 0.f, 0.f, 0.f, 0.f );
    Vector4 gloss = Vector4( 0.f, 0.f, 0.f, 0.f );
    float fading = 0.f;
    float quality = 1.f;

    virtual void apply( Moo::EffectMaterial & material ) const
    {
        material.setProperty( "g_useRepaint", enabled, false );
        material.setProperty( "g_baseColor", base, false );
        if (!enabled)
            return;
        static const char * names[] = { "g_repaintColor1", "g_repaintColor2", "g_repaintColor3" };
        for (size_t index = 0; index < 3; ++index)
            material.setProperty( names[index], colours[index], false );
        material.setProperty( "g_repaintMetallic",
            Vector4( metallic.x, metallic.y, metallic.z, quality ), false );
        material.setProperty( "g_repaintGloss",
            Vector4( gloss.x, gloss.y, gloss.z, fading ), false );
    }
};

struct VehicleMaterialHandlerEntry
{
    PyObjectPtr object;
    std::shared_ptr<VehicleMaterialHandlerState> state;
};

struct VehicleMaterialBinding
{
    Moo::Visual::PrimitiveGroup * group;
    Moo::ComplexEffectMaterialPtr original;
    Moo::ComplexEffectMaterialPtr normal;
    Moo::ComplexEffectMaterialPtr alpha;
    int trackSide;
    Vector4 authoredUV;
};

struct VehicleFashionState
{
    WeakPyPtr<PyModel> model;
    std::vector<VehicleMaterialBinding> bindings;
    std::vector<std::pair<Moo::Visual::PrimitiveGroup *, Moo::ComplexEffectMaterialPtr> > saved;
    std::vector<VehicleMaterialHandlerEntry> handlers;
    Vector4ProviderPtr movement;
    BoundingBox bounds;
    BW::string leftMaterial;
    BW::string rightMaterial;
    VehicleTrackTextureState tracks;
    float textureScale = 0.f;
    float trackDistance = 80.f;
    float splineDistance = 80.f;
    float alpha = 1.f;
    float crashCoefficient[2] = { 0.f, 0.f };
    float authoredCrashCoefficient[2] = { 0.f, 0.f };
    unsigned hiddenTracks = 0;
    bool vehicle = false;
    bool crashed = false;
    bool trackConfigured = false;
    bool tracksBeyondLod = false;
    bool alphaInitialised = false;
    bool camouflage = false;

    bool rebuild()
    {
        PyModel * owner = const_cast<PyModel *>( model.get() );
        if (!owner || !owner->pSuperModel())
            return true;
        std::vector<VehicleMaterialBinding> next;
        std::set<Moo::Visual::PrimitiveGroup *> seen;
        bool foundLeft = leftMaterial.empty();
        bool foundRight = rightMaterial.empty();
        bool foundCrash[2] = { false, false };
        float originalCrash[2] = { 0.f, 0.f };
        SuperModel & super = *owner->pSuperModel();
        super.localBoundingBox( bounds );
        for (int part = 0; part < super.nModels(); ++part)
        {
            for (Model * level = super.topModel( part ).getObject(); level; level = level->parent())
            {
                BW::vector<Moo::Visual::PrimitiveGroup *> groups;
                level->gatherMaterials( "*", groups );
                for (size_t index = 0; index < groups.size(); ++index)
                {
                    Moo::Visual::PrimitiveGroup * group = groups[index];
                    if (!group || !group->material_ || !seen.insert( group ).second)
                        continue;
                    VehicleMaterialBinding binding;
                    binding.group = group;
                    binding.original = group->material_;
                    const BW::string & name = binding.original->identifier();
                    binding.trackSide = name == leftMaterial ? 1 : name == rightMaterial ? 2 : 0;
                    if (binding.trackSide == 1)
                        foundLeft = true;
                    if (binding.trackSide == 2)
                        foundRight = true;
                    binding.normal = camouflage && !excludedFromCamouflage( name ) ?
                        materialVariant( binding.original, "_colourised.fx" ) :
                        cloneMaterial( binding.original );
                    // Original af0f60 skips a material whose colourised effect
                    // cannot be loaded. Preserve that original material.
                    if (!binding.normal)
                        binding.normal = cloneMaterial( binding.original );
                    if (trackConfigured && binding.trackSide != 0 && !crashed)
                    {
                        Moo::EffectPropertyPtr uv = binding.normal->baseMaterial()->getProperty(
                            "vTransform_diffuse" );
                        if (!uv || !uv->getVector( binding.authoredUV ))
                        {
                            PyErr_Format( PyExc_ValueError,
                                "Track material '%s' has no vector vTransform_diffuse", name.c_str() );
                            return false;
                        }
                    }
                    if (crashed && binding.trackSide != 0 && !foundCrash[binding.trackSide - 1])
                        foundCrash[binding.trackSide - 1] = binding.original->baseMaterial()->floatProperty(
                            originalCrash[binding.trackSide - 1], "crash_coefficient" );
                    if (alphaInitialised)
                    {
                        binding.alpha = materialVariant( binding.normal, "_modalpha.fx" );
                        if (binding.alpha)
                        {
                            binding.alpha->baseMaterial()->setProperty( "srcBlend", 4, false );
                            binding.alpha->baseMaterial()->setProperty( "destBlend", 5, false );
                        }
                    }
                    next.push_back( binding );
                }
            }
        }
        if (trackConfigured && (!foundLeft || !foundRight))
        {
            PyErr_SetString( PyExc_ValueError, "A configured track material is absent from the vehicle model" );
            return false;
        }
        authoredCrashCoefficient[0] = originalCrash[0];
        authoredCrashCoefficient[1] = originalCrash[1];
        bindings.swap( next );
        return true;
    }

    void undress()
    {
        for (size_t index = saved.size(); index > 0; --index)
            saved[index - 1].first->material_ = saved[index - 1].second;
        saved.clear();
    }

    void dress()
    {
        this->undress();
        for (size_t index = 0; index < bindings.size(); ++index)
        {
            VehicleMaterialBinding & binding = bindings[index];
            saved.push_back( std::make_pair( binding.group, binding.group->material_ ) );
            if (binding.trackSide != 0 && (hiddenTracks & static_cast<unsigned>( binding.trackSide )))
            {
                // #1513's invisible material pointer (fashion+188) is NULL.
                // patch_vehicle_fashions adds the public renderer's matching
                // primitive skip before dereferencing that material.
                binding.group->material_ = NULL;
                continue;
            }
            Moo::ComplexEffectMaterialPtr material = alpha != 1.f && binding.alpha ?
                binding.alpha : binding.normal;
            Moo::EffectMaterialPtr effect = material->baseMaterial();
            if (binding.alpha && alpha != 1.f)
                effect->setProperty( "wgOutputAlphaModifier", alpha, false );
            if (trackConfigured && !crashed && binding.trackSide != 0)
                effect->setProperty( "vTransform_diffuse", tracksBeyondLod ? binding.authoredUV :
                    Vector4( 0.f, 1.f, 0.f, binding.trackSide == 1 ? tracks.left : tracks.right ), false );
            if (trackConfigured && crashed && binding.trackSide != 0)
                effect->setProperty( "crash_coefficient", crashCoefficient[binding.trackSide - 1], false );
            for (size_t handler = 0; handler < handlers.size(); ++handler)
                handlers[handler].state->apply( *effect );
            binding.group->material_ = material;
        }
    }
};

class VehicleMaterialFashion : public MaterialFashion
{
public:
    explicit VehicleMaterialFashion( const std::shared_ptr<VehicleFashionState> & state ) :
        state_( state ) {}
    virtual void dress( SuperModel & ) { state_->dress(); }
    virtual void undress( SuperModel & ) { state_->undress(); }
private:
    std::shared_ptr<VehicleFashionState> state_;
};
}

class StockVehicleMaterialHandler : public PyObjectPlus
{
    Py_Header( StockVehicleMaterialHandler, PyObjectPlus )
public:
    StockVehicleMaterialHandler( const std::shared_ptr<VehicleMaterialHandlerState> & state,
        PyTypeObject * type ) : PyObjectPlus( type ), state_( state ) {}
    const std::shared_ptr<VehicleMaterialHandlerState> & state() const { return state_; }
protected:
    std::shared_ptr<VehicleMaterialHandlerState> state_;
};
PY_TYPEOBJECT( StockVehicleMaterialHandler )
PY_BEGIN_METHODS( StockVehicleMaterialHandler )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( StockVehicleMaterialHandler )
PY_END_ATTRIBUTES()

class PyCamoHandler : public StockVehicleMaterialHandler
{
    Py_Header( PyCamoHandler, StockVehicleMaterialHandler )
public:
    PyCamoHandler() : StockVehicleMaterialHandler( std::make_shared<VehicleCamoState>(), &s_type_ ) {}
    PY_METHOD_DECLARE( py_setCamoParams )
    static PyCamoHandler * New() { return new PyCamoHandler(); }
    PY_AUTO_FACTORY_DECLARE( PyCamoHandler, END )
};
PyObject * PyCamoHandler::py_setCamoParams( PyObject * args )
{
    PyObject * object;
    if (!PyArg_ParseTuple( args, "O:setCamoParams", &object ))
        return NULL;
    if (!PyTuple_Check( object ) || PyTuple_GET_SIZE( object ) != 8)
    {
        PyErr_SetString( PyExc_TypeError, "CamoParams must be a tuple with eight arguments" );
        return NULL;
    }
    BW::string tint;
    BW::string exclusion;
    VehicleCamoState next;
    if (Script::setData( PyTuple_GET_ITEM( object, 0 ), tint ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( object, 1 ), exclusion ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( object, 2 ), next.tiling ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( object, 3 ), next.weights ) != 0)
        return NULL;
    for (size_t index = 0; index < 4; ++index)
        if (Script::setData( PyTuple_GET_ITEM( object, index + 4 ), next.colours[index] ) != 0)
            return NULL;
    Moo::TextureManager * manager = Moo::TextureManager::instance();
    if (!manager && (!tint.empty() || !exclusion.empty()))
    {
        PyErr_SetString( PyExc_RuntimeError, "Camouflage textures require an active TextureManager" );
        return NULL;
    }
    if (!tint.empty())
        next.tint = manager->get( tint, true, true, true, "Art/File Textures/" );
    if (!exclusion.empty())
        next.exclusion = manager->get( exclusion, true, true, true, "Art/File Textures/" );
    if ((!tint.empty() && !next.tint) || (!exclusion.empty() && !next.exclusion))
    {
        PyErr_SetString( PyExc_RuntimeError, "An original camouflage texture could not be loaded" );
        return NULL;
    }
    *std::static_pointer_cast<VehicleCamoState>( state_ ) = next;
    Py_RETURN_NONE;
}
PY_TYPEOBJECT( PyCamoHandler )
PY_BEGIN_METHODS( PyCamoHandler )
    PY_METHOD( setCamoParams )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( PyCamoHandler )
PY_END_ATTRIBUTES()
PY_FACTORY( PyCamoHandler, BigWorld )

class PyRepaintHandler : public StockVehicleMaterialHandler
{
    Py_Header( PyRepaintHandler, StockVehicleMaterialHandler )
public:
    PyRepaintHandler() : StockVehicleMaterialHandler( std::make_shared<VehicleRepaintState>(), &s_type_ ) {}
    PY_METHOD_DECLARE( py_setRepaintParams )
    static PyRepaintHandler * New() { return new PyRepaintHandler(); }
    PY_AUTO_FACTORY_DECLARE( PyRepaintHandler, END )
};
PyObject * PyRepaintHandler::py_setRepaintParams( PyObject * args )
{
    PyObject * object;
    if (!PyArg_ParseTuple( args, "O:setRepaintParams", &object ))
        return NULL;
    if (!PyTuple_Check( object ) || PyTuple_GET_SIZE( object ) != 7)
    {
        PyErr_SetString( PyExc_TypeError, "RepaintParams must be a tuple with seven arguments" );
        return NULL;
    }
    VehicleRepaintState next;
    uint32 base;
    if (Script::setData( PyTuple_GET_ITEM( object, 0 ), next.enabled ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( object, 1 ), base ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( object, 3 ), next.metallic ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( object, 4 ), next.gloss ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( object, 5 ), next.fading ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( object, 6 ), next.quality ) != 0)
        return NULL;
    PyObjectPtr colours( PySequence_Fast( PyTuple_GET_ITEM( object, 2 ),
        "Repaint colours must be a sequence" ), PyObjectPtr::STEAL_REFERENCE );
    if (!colours)
        return NULL;
    if (PySequence_Fast_GET_SIZE( colours.get() ) < 3)
    {
        PyErr_SetString( PyExc_ValueError, "RepaintParams requires at least three colours" );
        return NULL;
    }
    next.base = packedVehicleColour( base );
    for (size_t index = 0; index < 3; ++index)
    {
        uint32 colour;
        if (Script::setData( PySequence_Fast_GET_ITEM( colours.get(), index ), colour ) != 0)
            return NULL;
        next.colours[index] = packedVehicleColour( colour );
    }
    *std::static_pointer_cast<VehicleRepaintState>( state_ ) = next;
    Py_RETURN_NONE;
}
PY_TYPEOBJECT( PyRepaintHandler )
PY_BEGIN_METHODS( PyRepaintHandler )
    PY_METHOD( setRepaintParams )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( PyRepaintHandler )
PY_END_ATTRIBUTES()
PY_FACTORY( PyRepaintHandler, BigWorld )

class WGBaseFashion : public PyMaterialFashion
{
    Py_Header( WGBaseFashion, PyMaterialFashion )
public:
    explicit WGBaseFashion( PyTypeObject * type = &s_type_ ) :
        PyMaterialFashion( type ), state_( std::make_shared<VehicleFashionState>() ),
        fashion_( new VehicleMaterialFashion( state_ ) ) {}
    virtual ~WGBaseFashion() { this->disowned(); }
    virtual const MaterialFashionPtr materialFashion() const { return fashion_; }
    virtual MaterialFashionPtr materialFashion() { return fashion_; }
    virtual PyFashion * makeCopy( PyModel * owner, const char * )
    {
        if (state_->model.get() && state_->model.get() != owner)
        {
            PyErr_SetString( PyExc_ValueError, "A vehicle fashion is already attached to another model" );
            return NULL;
        }
        state_->model = WeakPyPtr<PyModel>( owner );
        if (!state_->rebuild())
        {
            state_->model = WeakPyPtr<PyModel>();
            return NULL;
        }
        Py_INCREF( this );
        return this;
    }
    virtual void disowned()
    {
        state_->undress();
        state_->bindings.clear();
        state_->model = WeakPyPtr<PyModel>();
    }
    PY_METHOD_DECLARE( py_initAlphaMaterials )
    PY_METHOD_DECLARE( py_setAlpha )
    PY_METHOD_DECLARE( py_setCamouflage )
    PY_METHOD_DECLARE( py_removeCamouflage )
    PY_METHOD_DECLARE( py_addMaterialHandler )
    static WGBaseFashion * New() { return new WGBaseFashion(); }
    PY_AUTO_FACTORY_DECLARE( WGBaseFashion, END )
protected:
    std::shared_ptr<VehicleFashionState> state_;
private:
    MaterialFashionPtr fashion_;
};

PyObject * WGBaseFashion::py_initAlphaMaterials( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":initAlphaMaterials" ))
        return NULL;
    const bool old = state_->alphaInitialised;
    state_->alphaInitialised = true;
    if (!state_->rebuild())
    {
        state_->alphaInitialised = old;
        return NULL;
    }
    Py_RETURN_NONE;
}
PyObject * WGBaseFashion::py_setAlpha( PyObject * args )
{
    float value;
    if (!PyArg_ParseTuple( args, "f:setAlpha", &value ))
        return NULL;
    state_->alpha = value;
    Py_RETURN_NONE;
}
PyObject * WGBaseFashion::py_setCamouflage( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":setCamouflage" ))
        return NULL;
    const bool old = state_->camouflage;
    state_->camouflage = true;
    if (!state_->rebuild())
    {
        state_->camouflage = old;
        return NULL;
    }
    Py_RETURN_NONE;
}
PyObject * WGBaseFashion::py_removeCamouflage( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":removeCamouflage" ))
        return NULL;
    const bool old = state_->camouflage;
    state_->camouflage = false;
    if (!state_->rebuild())
    {
        state_->camouflage = old;
        return NULL;
    }
    Py_RETURN_NONE;
}
PyObject * WGBaseFashion::py_addMaterialHandler( PyObject * args )
{
    PyObject * object;
    if (!PyArg_ParseTuple( args, "O:addMaterialHandler", &object ))
        return NULL;
    if (!StockVehicleMaterialHandler::Check( object ))
    {
        PyErr_SetString( PyExc_TypeError, "addMaterialHandler requires an original native material handler" );
        return NULL;
    }
    VehicleMaterialHandlerEntry entry;
    entry.object = object;
    entry.state = static_cast<StockVehicleMaterialHandler *>( object )->state();
    state_->handlers.push_back( entry );
    Py_RETURN_NONE;
}
PY_TYPEOBJECT( WGBaseFashion )
PY_BEGIN_METHODS( WGBaseFashion )
    PY_METHOD( initAlphaMaterials )
    PY_METHOD( setAlpha )
    PY_METHOD( setCamouflage )
    PY_METHOD( removeCamouflage )
    PY_METHOD( addMaterialHandler )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( WGBaseFashion )
PY_END_ATTRIBUTES()
PY_FACTORY( WGBaseFashion, BigWorld )

class WGVehicleFashion : public WGBaseFashion
{
    Py_Header( WGVehicleFashion, WGBaseFashion )
public:
    explicit WGVehicleFashion( bool crashed ) : WGBaseFashion( &s_type_ )
    {
        state_->vehicle = true;
        state_->crashed = crashed;
    }
    virtual void tick( float, float lod )
    {
        if (!state_->trackConfigured || state_->crashed || !state_->model.get())
            return;
        state_->tracksBeyondLod =
            1.f / vehicleScreenArea( state_->bounds, lod ) >
            1.f / vehicleScreenArea( state_->bounds, state_->trackDistance );
        if (state_->tracksBeyondLod)
            return;
        Vector4 value( 0.f, 0.f, 0.f, 0.f );
        Vector4ProviderPtr provider = state_->movement;
        if (provider)
            provider->output( value );
        state_->tracks.update( value.y, value.z, state_->textureScale, state_->hiddenTracks );
    }
    Vector4ProviderPtr movementInfo() const { return state_->movement; }
    void movementInfo( Vector4ProviderPtr value ) { state_->movement = value; }
    PY_RW_ACCESSOR_ATTRIBUTE_DECLARE( Vector4ProviderPtr, movementInfo, movementInfo )
    PY_METHOD_DECLARE( py_setTracks )
    PY_METHOD_DECLARE( py_setLods )
    PY_METHOD_DECLARE( py_hideTracks )
    PY_METHOD_DECLARE( py_setCrashEffectCoeff )
    PY_FACTORY_DECLARE()
};
PyObject * WGVehicleFashion::pyNew( PyObject * args )
{
    int crashed;
    if (!PyArg_ParseTuple( args, "i:WGVehicleFashion", &crashed ))
        return NULL;
    return new WGVehicleFashion( crashed > 0 );
}
PyObject * WGVehicleFashion::py_setTracks( PyObject * args )
{
    const char * left;
    const char * right;
    float scale;
    if (!PyArg_ParseTuple( args, "ssf:setTracks", &left, &right, &scale ))
        return NULL;
    const BW::string oldLeft = state_->leftMaterial;
    const BW::string oldRight = state_->rightMaterial;
    const float oldScale = state_->textureScale;
    const bool oldConfigured = state_->trackConfigured;
    state_->leftMaterial = left;
    state_->rightMaterial = right;
    state_->textureScale = scale;
    state_->trackConfigured = true;
    if (!state_->rebuild())
    {
        state_->leftMaterial = oldLeft;
        state_->rightMaterial = oldRight;
        state_->textureScale = oldScale;
        state_->trackConfigured = oldConfigured;
        return NULL;
    }
    Py_RETURN_NONE;
}
PyObject * WGVehicleFashion::py_setLods( PyObject * args )
{
    float tracks;
    float spline;
    if (!PyArg_ParseTuple( args, "ff:setLods", &tracks, &spline ))
        return NULL;
    if (!(tracks >= 0.f) || !(spline >= 0.f))
    {
        // aede90 reports this error and returns None after disabling tracks.
        state_->trackConfigured = false;
        PyErr_SetString( PyExc_ValueError,
            "setLods - one or more parameters is invalid. They should be great or equal 0." );
        PyErr_Print();
        Py_RETURN_NONE;
    }
    state_->trackDistance = tracks;
    state_->splineDistance = spline;
    Py_RETURN_NONE;
}
PyObject * WGVehicleFashion::py_hideTracks( PyObject * args )
{
    PyObject * left;
    PyObject * right;
    if (!PyArg_ParseTuple( args, "OO:hideTracks", &left, &right ))
        return NULL;
    const int hideLeft = PyObject_IsTrue( left );
    if (hideLeft < 0)
        return NULL;
    const int hideRight = PyObject_IsTrue( right );
    if (hideRight < 0)
        return NULL;
    state_->hiddenTracks = (hideLeft ? 1U : 0U) | (hideRight ? 2U : 0U);
    Py_RETURN_NONE;
}
PyObject * WGVehicleFashion::py_setCrashEffectCoeff( PyObject * args )
{
    float coefficient;
    if (!PyArg_ParseTuple( args, "f:setCrashEffectCoeff", &coefficient ))
        return NULL;
    state_->crashCoefficient[0] = coefficient == -1.f ?
        state_->authoredCrashCoefficient[0] : coefficient;
    state_->crashCoefficient[1] = coefficient == -1.f ?
        state_->authoredCrashCoefficient[1] : coefficient;
    Py_RETURN_NONE;
}
PY_TYPEOBJECT( WGVehicleFashion )
PY_BEGIN_METHODS( WGVehicleFashion )
    PY_METHOD( setTracks )
    PY_METHOD( setLods )
    PY_METHOD( hideTracks )
    PY_METHOD( setCrashEffectCoeff )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( WGVehicleFashion )
    PY_ATTRIBUTE( movementInfo )
PY_END_ATTRIBUTES()
PY_FACTORY( WGVehicleFashion, BigWorld )

bool isStockVehicleFashion( PyObject * object )
{
    return object == Py_None || WGBaseFashion::Check( object );
}
bool setStockVehiclePartFashion( PyModel * model, PyObject * object )
{
    if (!model || !isStockVehicleFashion( object ))
    {
        PyErr_SetString( PyExc_TypeError, "setupFashions requires native vehicle fashions or None" );
        return false;
    }
    PyObjectPtr previous( PyObject_GetAttrString( model, "_stockVehicleFashion" ),
        PyObjectPtr::STEAL_REFERENCE );
    if (!previous)
    {
        if (!PyErr_ExceptionMatches( PyExc_AttributeError ))
            return false;
        PyErr_Clear();
        if (object == Py_None)
            return true;
    }
    else if (previous.get() == object)
        return true;
    // PyModel::setMaterial disowns its old value after makeCopy. Do not
    // reinsert an identical wrapper and accidentally disown its new binding.
    if (previous && object != Py_None &&
        PyObject_SetAttrString( model, "_stockVehicleFashion", Py_None ) != 0)
        return false;
    if (PyObject_SetAttrString( model, "_stockVehicleFashion", object ) == 0)
        return true;
    // A failed load must not leave the compound's published fashion pointer
    // claiming an old binding that the public model has already disowned.
    if (previous)
    {
        PyObject * errorType;
        PyObject * errorValue;
        PyObject * errorTrace;
        PyErr_Fetch( &errorType, &errorValue, &errorTrace );
        if (PyObject_SetAttrString( model, "_stockVehicleFashion", previous.get() ) != 0)
            PyErr_Print();
        PyErr_Restore( errorType, errorValue, errorTrace );
    }
    return false;
}
BW_END_NAMESPACE
