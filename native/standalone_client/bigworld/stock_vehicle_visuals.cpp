#include "pch.hpp"
#include "stock_vehicle_visuals.hpp"
#include "stock_vehicle_filter.hpp"
#include "stock_vehicle_components.hpp"
#include "stock_vehicle_animators.hpp"
#include "stock_vehicle_audio.hpp"
#include "vehicle_appearance.hpp"
#include "stock_scene_collision.hpp"

#include "pyscript/script_math.hpp"
#include "pyscript/py_callback.hpp"
#include "moo/render_context.hpp"
#include "moo/renderer.hpp"

#include <algorithm>
#include <cmath>
#include <functional>
#include <map>
#include <vector>

BW_BEGIN_NAMESPACE
int StockVehicleVisuals_token = 1;

namespace
{
const double TRACK_SCROLL_PERIOD = static_cast<double>( 0.05f );

struct TrackScrollChannel
{
    float external = 0.f;
    float residual = 0.f;

    // Exact #1513 007632d0. This is a visual residual, not track odometry.
    void update( float speed, float elapsed )
    {
        const float value = external;
        float target = value;
        if (fabsf( value ) <= 0.1f)
            target = 0.f;
        else if (fabsf( speed ) >= 0.1f)
        {
            target = value - speed;
            if (value * speed > 0.f && value * target < 0.f)
                target = 0.f;
        }
        if (residual * target <= 0.f)
            residual = 0.f;
        residual = (target - residual) * elapsed * 20.f + residual;
        if (fabsf( residual ) > fabsf( target ))
            residual = target;
        const float next = value - value * elapsed * 10.f;
        external = value * next <= 0.f ? 0.f : next;
    }
};
}

class StockBoolLink : public PyObjectPlus
{
    Py_Header( StockBoolLink, PyObjectPlus )
public:
    explicit StockBoolLink( std::function<bool()> reader ) :
        PyObjectPlus( &s_type_ ), reader_( reader ) {}
    bool sample() const { return reader_(); }
    PY_METHOD_DECLARE( pyCall )
private:
    std::function<bool()> reader_;
};
PyObject * StockBoolLink::pyCall( PyObject * ) { return Script::getData( this->sample() ); }
PY_TYPEOBJECT_WITH_CALL( StockBoolLink )
PY_BEGIN_METHODS( StockBoolLink )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( StockBoolLink )
PY_END_ATTRIBUTES()

class StockFloatLink : public PyObjectPlus
{
    Py_Header( StockFloatLink, PyObjectPlus )
public:
    explicit StockFloatLink( std::function<float()> reader ) :
        PyObjectPlus( &s_type_ ), reader_( reader ) {}
    float sample() const { return reader_(); }
    PY_METHOD_DECLARE( pyCall )
private:
    std::function<float()> reader_;
};
PyObject * StockFloatLink::pyCall( PyObject * ) { return Script::getData( this->sample() ); }
PY_TYPEOBJECT_WITH_CALL( StockFloatLink )
PY_BEGIN_METHODS( StockFloatLink )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( StockFloatLink )
PY_END_ATTRIBUTES()

class StockTranslationLink : public PyObjectPlus
{
    Py_Header( StockTranslationLink, PyObjectPlus )
public:
    explicit StockTranslationLink( MatrixProviderPtr provider ) :
        PyObjectPlus( &s_type_ ), provider_( provider ) {}
    Vector3 sample() const
    {
        MatrixProviderPtr provider = provider_;
        Matrix value;
        provider->matrix( value );
        return value.applyToOrigin();
    }
    PY_METHOD_DECLARE( pyCall )
private:
    MatrixProviderPtr provider_;
};
PyObject * StockTranslationLink::pyCall( PyObject * ) { return Script::getData( this->sample() ); }
PY_TYPEOBJECT_WITH_CALL( StockTranslationLink )
PY_BEGIN_METHODS( StockTranslationLink )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( StockTranslationLink )
PY_END_ATTRIBUTES()

bool sampleStockBoolLink( PyObject * link, bool & value )
{
    if (!link || !StockBoolLink::Check( link ))
        return false;
    value = static_cast<StockBoolLink *>( link )->sample();
    return true;
}
bool isStockFloatLink( PyObject * link )
{
    return StockFloatLink::Check( link );
}
bool sampleStockFloatLink( PyObject * link, float & value )
{
    if (!link || !StockFloatLink::Check( link ))
        return false;
    value = static_cast<StockFloatLink *>( link )->sample();
    return true;
}
bool sampleStockTranslationLink( PyObject * link, Vector3 & value )
{
    if (!link || !StockTranslationLink::Check( link ))
        return false;
    value = static_cast<StockTranslationLink *>( link )->sample();
    return true;
}

class LodCalculator;
namespace
{
struct LodState : public StockComponentState
{
    explicit LodState( PyObject * link, bool useFrustum, uint32 priorityGroup, bool player ) :
        positionLink( link ), frustum( useFrustum ), group( priorityGroup ),
        ownVehicle( player ), active( false ), priority( 0 ), distance( 0.f ) {}
    virtual void activate();
    virtual void deactivate();
    virtual void destroy();
    virtual PyObject * wrap( bool owning );
    PyObjectPtr positionLink;
    bool frustum;
    uint32 group;
    bool ownVehicle;
    bool active;
    uint32 priority;
    float distance;
};
struct LodGroup
{
    std::vector<std::weak_ptr<LodState> > all;
    std::vector<std::weak_ptr<LodState> > active;
};
std::map<uint32, LodGroup> lodGroups;

void LodState::activate()
{
    if (!valid_ || active)
        return;
    active = true;
    lodGroups[group].active.push_back( std::static_pointer_cast<LodState>( this->shared_from_this() ) );
}
void LodState::deactivate()
{
    if (!active)
        return;
    active = false;
    std::vector<std::weak_ptr<LodState> > & values = lodGroups[group].active;
    for (size_t index = 0; index < values.size(); ++index)
        if (values[index].lock().get() == this)
        {
            // Original locality pools exchange a removed active entry with
            // their last active entry (00761040), rather than stable erase.
            values[index] = values.back();
            values.pop_back();
            break;
        }
}
void LodState::destroy()
{
    this->deactivate();
    valid_ = false;
    positionLink = NULL;
}

void updateLodCalculators()
{
    // 0075ea90 refuses the complete pass if a registered group has no active
    // entries. Empty groups whose components have all died are removed first.
    for (std::map<uint32, LodGroup>::iterator it = lodGroups.begin(); it != lodGroups.end(); )
    {
        std::vector<std::weak_ptr<LodState> > & all = it->second.all;
        all.erase( std::remove_if( all.begin(), all.end(),
            []( const std::weak_ptr<LodState> & weak )
            {
                std::shared_ptr<LodState> state = weak.lock();
                return !state || !state->valid();
            } ), all.end() );
        if (all.empty())
            it = lodGroups.erase( it );
        else
            ++it;
    }
    for (std::map<uint32, LodGroup>::const_iterator it = lodGroups.begin(); it != lodGroups.end(); ++it)
        if (it->second.active.empty())
            return;
    if (lodGroups.empty())
        return;

    Matrix cameraWorld;
    if (!cameraWorld.invert( Moo::rc().view() ))
    {
        ERROR_MSG( "Svarog LOD camera view is singular\n" );
        return;
    }
    const Vector3 cameraPosition = cameraWorld.applyToOrigin();
    const Matrix & viewProjection = Moo::rc().viewProjection();
    // WoTLodViewer::factor 006803d0 reads cameraSpace+218. Its writer
    // 009bab1a is fov * .9549296498298645, exactly the public zoomFactor.
    const float zoom = Moo::rc().zoomFactor();
    for (std::map<uint32, LodGroup>::iterator it = lodGroups.begin(); it != lodGroups.end(); ++it)
    {
        std::vector<std::weak_ptr<LodState> > & values = it->second.active;
        for (size_t index = 0; index < values.size(); ++index)
        {
            std::shared_ptr<LodState> state = values[index].lock();
            Vector3 position;
            if (!state || !state->valid() ||
                !sampleStockTranslationLink( state->positionLink.get(), position ))
            {
                ERROR_MSG( "Svarog LOD lost its native position DataLink\n" );
                return;
            }
            const Vector3 difference = cameraPosition - position;
            state->distance = difference.length();
            if (state->frustum)
            {
                Vector4 clip;
                viewProjection.applyPoint( clip, position );
                if (!(clip.x < -clip.w || clip.x > clip.w ||
                      clip.y < -clip.w || clip.y > clip.w ||
                      clip.z < 0.f || clip.z > clip.w))
                    state->distance *= zoom;
            }
        }
        if (it->first != 0)
        {
            // 00760c00 orders the active prefix by distance. Player entries
            // keep priority zero; ordinary entries receive one-based ranks.
            std::sort( values.begin(), values.end(),
                []( const std::weak_ptr<LodState> & first, const std::weak_ptr<LodState> & second )
                {
                    return first.lock()->distance < second.lock()->distance;
                } );
            uint32 priority = 0;
            for (size_t index = 0; index < values.size(); ++index)
            {
                std::shared_ptr<LodState> state = values[index].lock();
                if (!state->ownVehicle)
                    state->priority = ++priority;
            }
        }
    }
}
}

class StockLodLink : public PyObjectPlus
{
    Py_Header( StockLodLink, PyObjectPlus )
public:
    explicit StockLodLink( const std::shared_ptr<LodState> & state ) :
        PyObjectPlus( &s_type_ ), state_( state ) {}
    bool sample( uint32 & priority, float & distance ) const
    {
        if (!state_->valid())
            return false;
        priority = state_->priority;
        distance = state_->distance;
        return true;
    }
private:
    std::shared_ptr<LodState> state_;
};
PY_TYPEOBJECT( StockLodLink )
PY_BEGIN_METHODS( StockLodLink )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( StockLodLink )
PY_END_ATTRIBUTES()

class LodCalculator : public StockComponentWrapper
{
    Py_Header( LodCalculator, StockComponentWrapper )
public:
    LodCalculator( const std::shared_ptr<StockComponentState> & state, bool owning ) :
        StockComponentWrapper( state, &s_type_, owning ) {}
    std::shared_ptr<LodState> state() const
    {
        return std::dynamic_pointer_cast<LodState>( this->component() );
    }
    PyObject * pyGet_priority()
    {
        std::shared_ptr<LodState> value = this->state();
        return value ? Script::getData( value->priority ) : this->missing();
    }
    PyObject * pyGet_lodDistance()
    {
        std::shared_ptr<LodState> value = this->state();
        return value ? Script::getData( value->distance ) : this->missing();
    }
    PyObject * pyGet_lodStateLink()
    {
        std::shared_ptr<LodState> value = this->state();
        return value ? static_cast<PyObject *>( new StockLodLink( value ) ) : this->missing();
    }
    PY_RO_ATTRIBUTE_SET( priority )
    PY_RO_ATTRIBUTE_SET( lodDistance )
    PY_RO_ATTRIBUTE_SET( lodStateLink )
    PY_FACTORY_DECLARE()
private:
    PyObject * missing() const
    {
        PyErr_SetString( PyExc_ValueError, "LodCalculator has been destroyed" );
        return NULL;
    }
};
PyObject * LodState::wrap( bool owning )
{
    return new LodCalculator( this->shared_from_this(), owning );
}
PyObject * LodCalculator::pyNew( PyObject * args )
{
    PyObject * link;
    PyObject * frustumObject;
    PyObject * groupObject;
    PyObject * playerObject;
    if (!PyArg_ParseTuple( args, "OOOO:LodCalculator", &link, &frustumObject, &groupObject, &playerObject ))
        return NULL;
    if (!StockTranslationLink::Check( link ))
    {
        PyErr_SetString( PyExc_TypeError, "LodCalculator requires a Vector3 DataLink" );
        return NULL;
    }
    bool frustum;
    bool player;
    uint32 group;
    if (Script::setData( frustumObject, frustum ) != 0 ||
        Script::setData( groupObject, group ) != 0 ||
        Script::setData( playerObject, player ) != 0)
        return NULL;
    std::shared_ptr<LodState> state = std::make_shared<LodState>( link, frustum, group, player );
    lodGroups[group].all.push_back( state );
    return new LodCalculator( state, true );
}
PY_TYPEOBJECT( LodCalculator )
PY_BEGIN_METHODS( LodCalculator )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( LodCalculator )
    PY_ATTRIBUTE( priority )
    PY_ATTRIBUTE( lodDistance )
    PY_ATTRIBUTE( lodStateLink )
PY_END_ATTRIBUTES()
PY_FACTORY( LodCalculator, Vehicular )

class Suspension;
namespace
{
struct SuspensionNode
{
    BW::string name;
    Matrix authored;
    float minimum;
    float maximum;
    float offset;
    bool left;
};
struct SuspensionState : public StockComponentState
{
    explicit SuspensionState( PyObjectPlus * model ) : compound( model ),
        thickness( 0.f ), maxDistance( 0.f ), maxPriority( 0 ),
        flags( 0 ), active( false ), wasEligible( false ) {}
    virtual void activate();
    virtual void deactivate();
    virtual void destroy();
    virtual PyObject * wrap( bool owning );
    bool addNodes( const std::vector<BW::string> & names, bool left, float minimum, float maximum );
    void update( float elapsed, wot::VehicleCollisionProvider & provider );
    WeakPyPtr<PyObjectPlus> compound;
    std::vector<SuspensionNode> nodes;
    PyObjectPtr lodLink;
    float thickness;
    float maxDistance;
    uint32 maxPriority;
    unsigned flags;
    bool active;
    bool wasEligible;
};
std::vector<std::weak_ptr<SuspensionState> > activeSuspensions;
std::unique_ptr<wot::VehicleCollisionProvider> suspensionCollision;

void SuspensionState::activate()
{
    if (!valid_ || active)
        return;
    active = true;
    activeSuspensions.push_back( std::static_pointer_cast<SuspensionState>( this->shared_from_this() ) );
}
void SuspensionState::deactivate()
{
    if (!active)
        return;
    active = false;
    for (size_t index = 0; index < activeSuspensions.size(); ++index)
        if (activeSuspensions[index].lock().get() == this)
        {
            activeSuspensions[index] = activeSuspensions.back();
            activeSuspensions.pop_back();
            break;
        }
}
void SuspensionState::destroy()
{
    this->deactivate();
    valid_ = false;
    compound = WeakPyPtr<PyObjectPlus>();
    lodLink = NULL;
    nodes.clear();
}

bool SuspensionState::addNodes( const std::vector<BW::string> & names,
    bool left, float minimum, float maximum )
{
    // 007812d0/00781390 explicitly reject the forward renderer (type 1).
    if (!Renderer::pInstance())
    {
        PyErr_SetString( PyExc_RuntimeError, "Suspension requires the renderer" );
        return false;
    }
    if (Renderer::instance().pipelineType() == IRendererPipeline::TYPE_FORWARD_SHADING)
        return true;
    if (maximum < minimum)
        std::swap( minimum, maximum );
    PyObjectPtr model( const_cast<PyObject *>( compound.getPyObj() ) );
    std::vector<SuspensionNode> incoming;
    for (size_t index = 0; index < names.size(); ++index)
    {
        SuspensionNode node;
        node.name = names[index];
        Matrix parent;
        Matrix world;
        if (!model || !stockVehicleCompoundNode( model.get(), node.name, node.authored, parent, world ))
        {
            PyErr_Format( PyExc_ValueError, "Suspension cannot find active compound node %s", node.name.c_str() );
            return false;
        }
        node.left = left;
        node.minimum = minimum;
        node.maximum = maximum;
        node.offset = 0.f; // Exact 00780b10 record+64.
        incoming.push_back( node );
    }
    nodes.insert( nodes.end(), incoming.begin(), incoming.end() );
    return true;
}

void SuspensionState::update( float elapsed, wot::VehicleCollisionProvider & provider )
{
    if (!valid_ || !active || nodes.empty())
        return;
    bool eligible = true;
    if (lodLink)
    {
        uint32 priority;
        float distance;
        if (!static_cast<StockLodLink *>( lodLink.get() )->sample( priority, distance ))
        {
            ERROR_MSG( "Suspension lost its native LOD state\n" );
            this->deactivate();
            return;
        }
        eligible = maxDistance >= distance && maxPriority >= priority;
    }
    const bool resetOffsets = eligible && !wasEligible;
    wasEligible = eligible;
    if (!eligible)
        return;

    PyObjectPtr model( const_cast<PyObject *>( compound.getPyObj() ) );
    uint32 spaceID;
    BoundingBox bounds;
    if (!model || !stockVehicleCompoundContext( model.get(), spaceID, bounds ) || bounds.insideOut())
    {
        ERROR_MSG( "Suspension lost its active compound bounds\n" );
        this->deactivate();
        return;
    }
    // 00781d70 expands only the minimum Y by one before prepare.
    wot::VehicleCollisionBounds queryBounds;
    queryBounds.minimum = { bounds.minBounds().x, bounds.minBounds().y - 1.f, bounds.minBounds().z };
    queryBounds.maximum = { bounds.maxBounds().x, bounds.maxBounds().y, bounds.maxBounds().z };
    if (!provider.prepare( queryBounds, spaceID, UINT32_MAX ))
    {
        ERROR_MSG( "Suspension cannot prepare its ground-query space\n" );
        this->deactivate();
        return;
    }

    std::vector<SuspensionNode> next = nodes;
    std::vector<Matrix> poses;
    unsigned nextFlags = flags | 0xcu;
    for (size_t index = 0; index < next.size(); ++index)
    {
        SuspensionNode & node = next[index];
        Matrix authored;
        Matrix parent;
        Matrix previous;
        if (!stockVehicleCompoundNode( model.get(), node.name, authored, parent, previous ) || !valid_)
        {
            ERROR_MSG( "Suspension lost a compound ground node\n" );
            this->deactivate();
            return;
        }
        Matrix world;
        world.multiply( node.authored, parent );
        const float lift = node.maximum + 0.5f;
        const Vector3 up( world[1].x, world[1].y, world[1].z );
        const Vector3 origin = world.applyToOrigin() + up * lift;
        wot::VehicleCollisionRay ray;
        ray.origin = { origin.x, origin.y, origin.z };
        ray.direction = { -up.x, -up.y, -up.z };
        ray.length = 1.f;
        wot::VehicleCollisionRayHit hit;
        float target = node.minimum;
        if (provider.query_ray( ray, hit ))
        {
            float offset = lift - hit.distance;
            if (offset < node.minimum)
                offset = node.minimum;
            else if (node.maximum < offset)
                offset = node.maximum;
            target = offset + thickness;
            nextFlags &= ~(node.left ? 4u : 8u);
        }
        if (resetOffsets)
            node.offset = 0.f;
        // Original 00781920 caps vertical presentation velocity at +/-1.
        float speed = (target - node.offset) / elapsed;
        if (-1.f <= speed)
        {
            if (1.f <= speed)
                speed = 1.f;
        }
        else
            speed = -1.f;
        float offset = speed * elapsed + node.offset;
        if ((0.f < speed && target < offset) || (speed <= 0.f && offset < target))
            offset = target;
        node.offset = offset;
        Matrix local = node.authored;
        local.translation( local.applyToOrigin() + local.applyVector( Vector3( 0.f, offset, 0.f ) ) );
        poses.push_back( local );
    }
    if (!valid_ || !active)
        return;
    // The compound remains the sole node-local owner. These producers write
    // that state in the original manager order, rather than competing with
    // separate MatrixProviders installed over the same wheel/track node.
    for (size_t index = 0; index < next.size(); ++index)
        if ((nextFlags & (next[index].left ? 1u : 2u)) == 0)
            if (!writeStockVehicleCompoundNodeLocal( model.get(), next[index].name, poses[index] ))
            {
                ERROR_MSG( "Suspension cannot commit its compound ground node\n" );
                this->deactivate();
                return;
            }
    nodes.swap( next );
    flags = nextFlags;
}

void updateSuspensions( float elapsed )
{
    if (activeSuspensions.empty())
        return;
    if (!suspensionCollision)
        suspensionCollision = makeStockGroundCollisionProvider();
    const std::vector<std::weak_ptr<SuspensionState> > snapshot = activeSuspensions;
    for (size_t index = 0; index < snapshot.size(); ++index)
    {
        std::shared_ptr<SuspensionState> state = snapshot[index].lock();
        if (state)
            state->update( elapsed, *suspensionCollision );
    }
}
}

class Suspension : public StockComponentWrapper
{
    Py_Header( Suspension, StockComponentWrapper )
public:
    Suspension( const std::shared_ptr<StockComponentState> & state, bool owning ) :
        StockComponentWrapper( state, &s_type_, owning ) {}
    std::shared_ptr<SuspensionState> state() const
    {
        return std::dynamic_pointer_cast<SuspensionState>( this->component() );
    }
    PY_METHOD_DECLARE( py_addGroundNode )
    PY_METHOD_DECLARE( py_addGroundNodesGroup )
    PY_METHOD_DECLARE( py_setParameters )
    PY_METHOD_DECLARE( py_setLodLink )
    PY_METHOD_DECLARE( py_setLodSettings )
    PY_METHOD_DECLARE( py_stopAnimating )
    PY_FACTORY_DECLARE()
private:
    std::shared_ptr<SuspensionState> requireState() const
    {
        std::shared_ptr<SuspensionState> value = this->state();
        if (!value)
            PyErr_SetString( PyExc_ValueError, "Suspension has been destroyed" );
        return value;
    }
};
PyObject * SuspensionState::wrap( bool owning )
{
    return new Suspension( this->shared_from_this(), owning );
}
PyObject * Suspension::pyNew( PyObject * args )
{
    PyObject * model;
    if (!PyArg_ParseTuple( args, "O:Suspension", &model ))
        return NULL;
    uint32 space;
    BoundingBox bounds;
    if (!stockVehicleCompoundContext( model, space, bounds ))
    {
        PyErr_SetString( PyExc_TypeError, "Suspension requires an active CompoundModel" );
        return NULL;
    }
    return new Suspension( std::make_shared<SuspensionState>( static_cast<PyObjectPlus *>( model ) ), true );
}
PyObject * Suspension::py_addGroundNode( PyObject * args )
{
    PyObject * nameObject;
    PyObject * sideObject;
    float minimum;
    float maximum;
    if (!PyArg_ParseTuple( args, "OOff:addGroundNode", &nameObject, &sideObject, &minimum, &maximum ))
        return NULL;
    BW::string name;
    bool left;
    if (Script::setData( nameObject, name ) != 0 || Script::setData( sideObject, left ) != 0)
        return NULL;
    std::shared_ptr<SuspensionState> value = this->requireState();
    if (!value || !value->addNodes( std::vector<BW::string>( 1, name ), left, minimum, maximum ))
        return NULL;
    Py_RETURN_NONE;
}
PyObject * Suspension::py_addGroundNodesGroup( PyObject * args )
{
    PyObject * namesObject;
    PyObject * sideObject;
    float minimum;
    float maximum;
    if (!PyArg_ParseTuple( args, "OOff:addGroundNodesGroup", &namesObject, &sideObject, &minimum, &maximum ))
        return NULL;
    bool left;
    if (Script::setData( sideObject, left ) != 0)
        return NULL;
    PyObjectPtr sequence( PySequence_Fast( namesObject, "Ground-node names must be a sequence" ),
        PyObjectPtr::STEAL_REFERENCE );
    if (!sequence)
        return NULL;
    std::vector<BW::string> names;
    const Py_ssize_t count = PySequence_Fast_GET_SIZE( sequence.get() );
    for (Py_ssize_t index = 0; index < count; ++index)
    {
        BW::string name;
        if (Script::setData( PySequence_Fast_GET_ITEM( sequence.get(), index ), name ) != 0)
            return NULL;
        names.push_back( name );
    }
    std::shared_ptr<SuspensionState> value = this->requireState();
    if (!value || !value->addNodes( names, left, minimum, maximum ))
        return NULL;
    Py_RETURN_NONE;
}
PyObject * Suspension::py_setParameters( PyObject * args )
{
    float thickness;
    if (!PyArg_ParseTuple( args, "f:setParameters", &thickness ))
        return NULL;
    std::shared_ptr<SuspensionState> value = this->requireState();
    if (!value)
        return NULL;
    value->thickness = thickness;
    Py_RETURN_NONE;
}
PyObject * Suspension::py_setLodLink( PyObject * args )
{
    PyObject * link;
    if (!PyArg_ParseTuple( args, "O:setLodLink", &link ))
        return NULL;
    if (link != Py_None && !StockLodLink::Check( link ))
    {
        PyErr_SetString( PyExc_TypeError, "setLodLink requires a LodState DataLink or None" );
        return NULL;
    }
    std::shared_ptr<SuspensionState> value = this->requireState();
    if (!value)
        return NULL;
    value->lodLink = link == Py_None ? NULL : link;
    Py_RETURN_NONE;
}
PyObject * Suspension::py_setLodSettings( PyObject * args )
{
    float distance;
    unsigned priority;
    if (!PyArg_ParseTuple( args, "(fI):setLodSettings", &distance, &priority ))
        return NULL;
    std::shared_ptr<SuspensionState> value = this->requireState();
    if (!value)
        return NULL;
    value->maxDistance = distance;
    value->maxPriority = priority;
    Py_RETURN_NONE;
}
PyObject * Suspension::py_stopAnimating( PyObject * args )
{
    PyObject * leftObject;
    PyObject * rightObject;
    if (!PyArg_ParseTuple( args, "OO:stopAnimating", &leftObject, &rightObject ))
        return NULL;
    bool left;
    bool right;
    if (Script::setData( leftObject, left ) != 0 || Script::setData( rightObject, right ) != 0)
        return NULL;
    std::shared_ptr<SuspensionState> value = this->requireState();
    if (!value)
        return NULL;
    value->flags = (value->flags & ~3u) | (left ? 1u : 0u) | (right ? 2u : 0u);
    Py_RETURN_NONE;
}
PY_TYPEOBJECT( Suspension )
PY_BEGIN_METHODS( Suspension )
    PY_METHOD( addGroundNode )
    PY_METHOD( addGroundNodesGroup )
    PY_METHOD( setParameters )
    PY_METHOD( setLodLink )
    PY_METHOD( setLodSettings )
    PY_METHOD( stopAnimating )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( Suspension )
PY_END_ATTRIBUTES()
PY_FACTORY( Suspension, Vehicular )

class FlyingInfoProvider;
namespace
{
class FlyingState : public StockComponentState
{
public:
    // Exact native vft 014d7634 uses 00579c00 (RET) for both lifecycle
    // methods. Flying state is computed on demand; it has no ticking job.
    virtual void activate() {}
    virtual void deactivate() {}
    virtual void destroy()
    {
        valid_ = false;
        filter = WeakPyPtr<PyObjectPlus>();
        suspension.reset();
    }
    virtual PyObject * wrap( bool owning );
    WeakPyPtr<PyObjectPlus> filter;
    std::weak_ptr<SuspensionState> suspension;
};
}

class FlyingInfoProvider : public StockComponentWrapper
{
    Py_Header( FlyingInfoProvider, StockComponentWrapper )
public:
    FlyingInfoProvider( const std::shared_ptr<StockComponentState> & state, bool owning ) :
        StockComponentWrapper( state, &s_type_, owning ) {}
    bool sideFlying( bool left ) const
    {
        std::shared_ptr<FlyingState> source = this->state();
        if (!source)
            return false;
        std::shared_ptr<SuspensionState> suspension = source->suspension.lock();
        if (suspension && suspension->valid() && !suspension->nodes.empty())
            return (suspension->flags & (left ? 4u : 8u)) != 0;
        StockVehicleFilterVisualState value;
        if (!source || !readStockVehicleFilterVisualState(
                const_cast<PyObject *>( source->filter.getPyObj() ), value ))
            return false;
        return (left ? value.leftContacts : value.rightContacts) == 0;
    }
    PY_RO_ATTRIBUTE_DECLARE( this->sideFlying( true ), isLeftSideFlying )
    PY_RO_ATTRIBUTE_DECLARE( this->sideFlying( false ), isRightSideFlying )
    PY_RO_ATTRIBUTE_DECLARE( this->sideFlying( true ) && this->sideFlying( false ), isFlying )
    PY_METHOD_DECLARE( py_setData )
    static FlyingInfoProvider * New()
    {
        return new FlyingInfoProvider( std::make_shared<FlyingState>(), true );
    }
    PY_AUTO_FACTORY_DECLARE( FlyingInfoProvider, END )
private:
    std::shared_ptr<FlyingState> state() const
    {
        return std::dynamic_pointer_cast<FlyingState>( this->component() );
    }
};

PyObject * FlyingState::wrap( bool owning )
{
    return new FlyingInfoProvider( this->shared_from_this(), owning );
}

PyObject * FlyingInfoProvider::py_setData( PyObject * args )
{
    PyObject * filter;
    PyObject * suspension;
    if (!PyArg_ParseTuple( args, "OO:setData", &filter, &suspension ))
        return NULL;
    std::shared_ptr<FlyingState> source = this->state();
    if (!source)
    {
        PyErr_SetString( PyExc_ValueError, "FlyingInfoProvider has been destroyed" );
        return NULL;
    }
    source->suspension.reset();
    if (Suspension::Check( suspension ))
        source->suspension = static_cast<Suspension *>( suspension )->state();
    StockVehicleFilterVisualState value;
    source->filter = WeakPyPtr<PyObjectPlus>( readStockVehicleFilterVisualState( filter, value ) ?
        static_cast<PyObjectPlus *>( filter ) : NULL );
    Py_RETURN_NONE;
}
PY_TYPEOBJECT( FlyingInfoProvider )
PY_BEGIN_METHODS( FlyingInfoProvider )
    PY_METHOD( setData )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( FlyingInfoProvider )
    PY_ATTRIBUTE( isFlying )
    PY_ATTRIBUTE( isLeftSideFlying )
    PY_ATTRIBUTE( isRightSideFlying )
PY_END_ATTRIBUTES()
PY_FACTORY( FlyingInfoProvider, Vehicular )

static PyObject * py_createBoolLink( PyObject * args )
{
    PyObject * owner;
    const char * name = "";
    if (!PyArg_ParseTuple( args, "O|s:createBoolLink", &owner, &name ))
        return NULL;
    // 00757d20 resolves only the owner's native property descriptors. It
    // does not create arbitrary Python getattr callbacks.
    const BW::string property( name );
    if (FlyingInfoProvider::Check( owner ) &&
            (property == "isFlying" || property == "isLeftSideFlying" ||
             property == "isRightSideFlying"))
    {
        WeakPyPtr<FlyingInfoProvider> weak( static_cast<FlyingInfoProvider *>( owner ) );
        return new StockBoolLink( [weak, property]()
        {
            const FlyingInfoProvider * source = weak.get();
            if (!source)
                return false;
            if (property == "isFlying")
                return source->sideFlying( true ) && source->sideFlying( false );
            return source->sideFlying( property == "isLeftSideFlying" );
        } );
    }
    StockVehicleFilterVisualState value;
    if (property == "isStrafing" && readStockVehicleFilterVisualState( owner, value ))
    {
        WeakPyPtr<PyObjectPlus> weak( static_cast<PyObjectPlus *>( owner ) );
        return new StockBoolLink( [weak]()
        {
            StockVehicleFilterVisualState state;
            return readStockVehicleFilterVisualState(
                const_cast<PyObject *>( weak.getPyObj() ), state ) && state.strafing;
        } );
    }
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION( createBoolLink, DataLinks )

static PyObject * py_createFloatLink( PyObject * args )
{
    PyObject * owner;
    const char * name = "";
    if (!PyArg_ParseTuple( args, "O|s:createFloatLink", &owner, &name ))
        return NULL;
    const BW::string property( name );
    if (property == "lodDistance" && LodCalculator::Check( owner ))
    {
        std::shared_ptr<LodState> state = static_cast<LodCalculator *>( owner )->state();
        if (state)
            return new StockFloatLink( [state]() { return state->distance; } );
        Py_RETURN_NONE;
    }
    const char * names[] = { "instantaneousSpeed", "instantaneousRotationSpeed",
        "averageSpeed", "averageRotationSpeed", "longitudinalSpeed", "strafeSpeed",
        "angularSpeed", "suspCompressionRate" };
    unsigned field = 0;
    while (field != ARRAY_SIZE( names ) && property != names[field])
        ++field;
    StockVehicleFilterVisualState value;
    if (field == ARRAY_SIZE( names ) || !readStockVehicleFilterVisualState( owner, value ))
        Py_RETURN_NONE;
    WeakPyPtr<PyObjectPlus> weak( static_cast<PyObjectPlus *>( owner ) );
    return new StockFloatLink( [weak, field]()
    {
        StockVehicleFilterVisualState state;
        if (!readStockVehicleFilterVisualState(
                const_cast<PyObject *>( weak.getPyObj() ), state ))
            return 0.f;
        const float fields[] = { state.instantaneousSpeed, state.instantaneousRotationSpeed,
            state.averageSpeed, state.averageRotationSpeed, state.longitudinalSpeed,
            state.strafeSpeed, state.angularSpeed, state.suspensionCompressionRate };
        return fields[field];
    } );
}
PY_MODULE_FUNCTION( createFloatLink, DataLinks )

static PyObject * py_linkMatrixTranslation( PyObject * args )
{
    PyObject * object;
    if (!PyArg_ParseTuple( args, "O:linkMatrixTranslation", &object ))
        return NULL;
    MatrixProviderPtr provider;
    if (Script::setData( object, provider ) != 0)
        return NULL;
    if (!provider)
    {
        PyErr_SetString( PyExc_TypeError, "linkMatrixTranslation requires a MatrixProvider" );
        return NULL;
    }
    return new StockTranslationLink( provider );
}
PY_MODULE_FUNCTION( linkMatrixTranslation, DataLinks )

class PyTrackScroll;
namespace
{
std::vector<WeakPyPtr<PyTrackScroll> > activeTrackScrolls;
}

class PyTrackScroll : public PyObjectPlusWithWeakReference
{
    Py_Header( PyTrackScroll, PyObjectPlusWithWeakReference )
public:
    PyTrackScroll() : PyObjectPlusWithWeakReference( &s_type_ ) {}
    virtual ~PyTrackScroll() { this->deactivate(); }
    void activate();
    void deactivate();
    void tick( double gameTime );
    void setExternal( float left, float right )
    {
        left_.external = externalLeft_ = left;
        right_.external = externalRight_ = right;
        external_ = true;
    }
    float leftScroll() const { return left_.residual; }
    float rightScroll() const { return right_.residual; }
    bool leftContact() const { return leftContact_; }
    bool rightContact() const { return rightContact_; }
    PY_METHOD_DECLARE( py_setData )
    PY_METHOD_DECLARE( py_setFlyingInfo )
    PY_METHOD_DECLARE( py_setMode )
    PY_AUTO_METHOD_DECLARE( RETVOID, activate, END )
    PY_AUTO_METHOD_DECLARE( RETVOID, deactivate, END )
    PY_AUTO_METHOD_DECLARE( RETVOID, setExternal, ARG( float, ARG( float, END ) ) )
    PY_AUTO_METHOD_DECLARE( RETDATA, leftScroll, END )
    PY_AUTO_METHOD_DECLARE( RETDATA, rightScroll, END )
    PY_AUTO_METHOD_DECLARE( RETDATA, leftContact, END )
    PY_AUTO_METHOD_DECLARE( RETDATA, rightContact, END )
    static PyTrackScroll * New() { return new PyTrackScroll(); }
    PY_AUTO_FACTORY_DECLARE( PyTrackScroll, END )
private:
    TrackScrollChannel left_;
    TrackScrollChannel right_;
    WeakPyPtr<PyObjectPlus> filter_;
    PyObjectPtr leftFlying_;
    PyObjectPtr rightFlying_;
    float externalLeft_ = 0.f;
    float externalRight_ = 0.f;
    int mode_ = 0;
    int direction_ = 0;
    bool external_ = false;
    bool leftContact_ = true;
    bool rightContact_ = true;
    bool active_ = false;
    double nextTick_ = 0.;
};

void PyTrackScroll::activate()
{
    if (active_)
        return;
    active_ = true;
    nextTick_ = Script::getTotalGameTime() + TRACK_SCROLL_PERIOD;
    activeTrackScrolls.push_back( WeakPyPtr<PyTrackScroll>( this ) );
}
void PyTrackScroll::deactivate()
{
    active_ = false;
    activeTrackScrolls.erase( std::remove_if( activeTrackScrolls.begin(), activeTrackScrolls.end(),
        [this]( const WeakPyPtr<PyTrackScroll> & item ) { return item.get() == this; } ),
        activeTrackScrolls.end() );
}
void PyTrackScroll::tick( double gameTime )
{
    if (!active_ || gameTime < nextTick_)
        return;
    nextTick_ = gameTime + TRACK_SCROLL_PERIOD;
    StockVehicleFilterVisualState state;
    PyObject * filter = const_cast<PyObject *>( filter_.getPyObj() );
    if (!readStockVehicleFilterVisualState( filter, state ) || !state.attached)
        return;
    left_.update( state.instantaneousSpeed, 0.05f );
    right_.update( state.instantaneousSpeed, 0.05f );
    leftContact_ = !leftFlying_ || !static_cast<StockBoolLink *>( leftFlying_.get() )->sample();
    rightContact_ = !rightFlying_ || !static_cast<StockBoolLink *>( rightFlying_.get() )->sample();
    float leftSpeed = 0.f;
    float rightSpeed = 0.f;
    bool leftLocked = true;
    bool rightLocked = true;
    if (mode_ > 1)
    {
        if (external_)
        {
            leftSpeed = externalLeft_;
            rightSpeed = externalRight_;
        }
        else
        {
            const float speed = direction_ == 1 ? 4.f : -4.f;
            leftLocked = !leftContact_;
            rightLocked = !rightContact_;
            if (leftLocked)
                leftSpeed = speed;
            if (rightLocked)
                rightSpeed = speed;
        }
    }
    setStockVehicleTrackOverrides( filter, leftSpeed, rightSpeed, leftLocked, rightLocked );
}

PyObject * PyTrackScroll::py_setData( PyObject * args )
{
    PyObject * filter;
    if (!PyArg_ParseTuple( args, "O:setData", &filter ))
        return NULL;
    StockVehicleFilterVisualState state;
    filter_ = WeakPyPtr<PyObjectPlus>( readStockVehicleFilterVisualState( filter, state ) ?
        static_cast<PyObjectPlus *>( filter ) : NULL );
    Py_RETURN_NONE;
}
PyObject * PyTrackScroll::py_setFlyingInfo( PyObject * args )
{
    PyObject * left;
    PyObject * right;
    if (!PyArg_ParseTuple( args, "OO:setFlyingInfo", &left, &right ))
        return NULL;
    if ((left != Py_None && !StockBoolLink::Check( left )) ||
        (right != Py_None && !StockBoolLink::Check( right )))
    {
        PyErr_SetString( PyExc_TypeError, "setFlyingInfo requires bool DataLinks or None" );
        return NULL;
    }
    leftFlying_ = left == Py_None ? NULL : left;
    rightFlying_ = right == Py_None ? NULL : right;
    Py_RETURN_NONE;
}
PyObject * PyTrackScroll::py_setMode( PyObject * args )
{
    int mode;
    int direction;
    if (!PyArg_ParseTuple( args, "(ii):setMode", &mode, &direction ))
        return NULL;
    mode_ = mode;
    direction_ = direction;
    Py_RETURN_NONE;
}
PY_TYPEOBJECT( PyTrackScroll )
PY_BEGIN_METHODS( PyTrackScroll )
    PY_METHOD( setData )
    PY_METHOD( setFlyingInfo )
    PY_METHOD( setMode )
    PY_METHOD( activate )
    PY_METHOD( deactivate )
    PY_METHOD( setExternal )
    PY_METHOD( leftScroll )
    PY_METHOD( rightScroll )
    PY_METHOD( leftContact )
    PY_METHOD( rightContact )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( PyTrackScroll )
PY_END_ATTRIBUTES()
PY_FACTORY( PyTrackScroll, BigWorld )

void tickStockVehicleComponents( float frameTime )
{
    updateLodCalculators();
    tickStockDetailedEngineStates( frameTime );
    // Original static factory order: LOD 01493100, Recoil 01493104,
    // Suspension 0149328c. Recoil and Suspension both register phase 2.
    tickStockVehicleRecoil( frameTime );
    updateSuspensions( frameTime );
}

void tickStockVehicleTrackScrolls( double gameTime )
{
    // A snapshot makes deactivation during an update harmless. Weak entries
    // never keep an appearance alive after its original stop/destroy chain.
    const std::vector<WeakPyPtr<PyTrackScroll> > snapshot = activeTrackScrolls;
    for (size_t index = 0; index < snapshot.size(); ++index)
    {
        PyTrackScroll * source = const_cast<PyTrackScroll *>( snapshot[index].get() );
        if (source)
        {
            PyObjectPtr pin( source );
            source->tick( gameTime );
        }
    }
    activeTrackScrolls.erase( std::remove_if( activeTrackScrolls.begin(), activeTrackScrolls.end(),
        []( const WeakPyPtr<PyTrackScroll> & item ) { return item.get() == NULL; } ),
        activeTrackScrolls.end() );
}

namespace
{
class StockVisualScriptShutdown : public Script::FiniTimeJob
{
    virtual void fini()
    {
        activeTrackScrolls.clear();
        lodGroups.clear();
        activeSuspensions.clear();
        suspensionCollision.reset();
    }
} stockVisualScriptShutdown;
}
BW_END_NAMESPACE
