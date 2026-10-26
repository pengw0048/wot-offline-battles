#include "pch.hpp"
#include "stock_vehicle_components.hpp"

#include <algorithm>
#include <vector>

BW_BEGIN_NAMESPACE
int StockVehicleComponents_token = 1;

StockComponentWrapper::StockComponentWrapper(
    const std::shared_ptr<StockComponentState> & state, PyTypeObject * type, bool owning ) :
    PyObjectPlusWithWeakReference( type ), owned_( owning ? state : nullptr ),
    view_( state ), owning_( owning )
{
}
StockComponentWrapper::~StockComponentWrapper() { this->clear(); }

std::shared_ptr<StockComponentState> StockComponentWrapper::component() const
{
    std::shared_ptr<StockComponentState> value = view_.lock();
    return value && value->valid() ? value : std::shared_ptr<StockComponentState>();
}

std::shared_ptr<StockComponentState> StockComponentWrapper::transfer()
{
    if (!owning_ || !owned_ || !owned_->valid())
    {
        // 013ef430 explicitly rejects a second transfer of a non-owner.
        PyErr_SetString( PyExc_ValueError, "This component wrapper owns nothing" );
        return std::shared_ptr<StockComponentState>();
    }
    std::shared_ptr<StockComponentState> value = owned_;
    owned_.reset();
    owning_ = false;
    return value;
}

void StockComponentWrapper::clear()
{
    PyObject * errorType;
    PyObject * errorValue;
    PyObject * errorTrace;
    PyErr_Fetch( &errorType, &errorValue, &errorTrace );
    std::shared_ptr<StockComponentState> value = owned_;
    owned_.reset();
    view_.reset();
    // 013edfd0 clears the callback-visible component pointer before invoking
    // deactivate. A reentrant destroy therefore cannot delete it twice.
    if (owning_ && value && value->valid())
    {
        value->deactivate();
        value->destroy();
    }
    owning_ = false;
    PyErr_Restore( errorType, errorValue, errorTrace );
}

PyObject * StockComponentWrapper::py_activate( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":activate" ))
        return NULL;
    std::shared_ptr<StockComponentState> value = this->component();
    if (!value)
    {
        PyErr_SetString( PyExc_ValueError, "Component has been destroyed" );
        return NULL;
    }
    value->activate();
    Py_RETURN_NONE;
}
PyObject * StockComponentWrapper::py_deactivate( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":deactivate" ))
        return NULL;
    std::shared_ptr<StockComponentState> value = this->component();
    if (!value)
    {
        PyErr_SetString( PyExc_ValueError, "Component has been destroyed" );
        return NULL;
    }
    value->deactivate();
    Py_RETURN_NONE;
}
PyObject * StockComponentWrapper::py_destroy( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":destroy" ))
        return NULL;
    this->clear();
    Py_RETURN_NONE;
}
PY_TYPEOBJECT( StockComponentWrapper )
PY_BEGIN_METHODS( StockComponentWrapper )
    PY_METHOD( activate )
    PY_METHOD( deactivate )
    PY_METHOD( destroy )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( StockComponentWrapper )
    PY_ATTRIBUTE( isOwning )
PY_END_ATTRIBUTES()

namespace
{
class ScriptComponentState : public StockComponentState
{
public:
    explicit ScriptComponentState( PyObject * object ) : object_( object ), destroying_( false ) {}
    virtual void activate() { if (valid_) this->call( "activate" ); }
    virtual void deactivate() { if (valid_) this->call( "deactivate" ); }
    virtual void destroy()
    {
        if (!valid_ || destroying_)
            return;
        destroying_ = true;
        // 013ee0c0 calls the script while retaining its reference, then drops
        // that reference. Callback exceptions are reported by the engine.
        this->call( "destroy" );
        valid_ = false;
        object_ = NULL;
    }
    virtual PyObject * wrap( bool owning )
    {
        PyObject * result = object_.get();
        Py_XINCREF( result );
        if (owning)
        {
            // PureScriptComponent has no separate owning Python wrapper.
            // Reclaiming it returns the original script object and releases
            // the temporary native adapter at this call boundary.
            this->deactivate();
            this->destroy();
        }
        if (!result)
            Py_RETURN_NONE;
        return result;
    }
    PyObject * identity() const { return object_.get(); }
private:
    void call( const char * name )
    {
        PyObjectPtr object = object_;
        if (!object)
            return;
        Script::call( PyObject_GetAttrString( object.get(), name ), PyTuple_New( 0 ),
            "Svarog component callback: " );
    }
    PyObjectPtr object_;
    bool destroying_;
};

struct ComponentEntry
{
    BW::string name;
    std::shared_ptr<StockComponentState> component;
    uint64 token;
};

class SystemState : public StockComponentState
{
public:
    SystemState() : nextToken_( 0 ), destroying_( false ) {}
    virtual void activate() { this->visit( true ); }
    virtual void deactivate() { this->visit( false ); }
    virtual void destroy()
    {
        if (!valid_ || destroying_)
            return;
        destroying_ = true;
        std::vector<ComponentEntry> removed;
        removed.swap( entries_ );
        // The owning wrapper first deactivates the entire system. The entry
        // destructor (013efde0 -> 007697e0) then deactivates and deletes each
        // child, in insertion order. Pure Python receives both calls.
        for (size_t index = 0; index < removed.size(); ++index)
        {
            std::shared_ptr<StockComponentState> value = removed[index].component;
            if (value->valid())
            {
                value->deactivate();
                value->destroy();
            }
        }
        valid_ = false;
    }
    virtual PyObject * wrap( bool owning );
    bool add( const std::shared_ptr<StockComponentState> & value, const BW::string & name )
    {
        if (!valid_ || destroying_)
        {
            PyErr_SetString( PyExc_ValueError, "ComponentSystem has been destroyed" );
            return false;
        }
        if (value.get() == this)
        {
            PyErr_SetString( PyExc_ValueError, "A component system cannot own itself" );
            return false;
        }
        ComponentEntry entry = { name, value, ++nextToken_ };
        entries_.push_back( entry );
        // 013efc80 only transfers ownership and stores the name. Adding a
        // component does not activate it or invoke script callbacks.
        return true;
    }
    PyObject * find( const BW::string & name ) const
    {
        for (size_t index = 0; index < entries_.size(); ++index)
            if (entries_[index].name == name)
                return entries_[index].component->wrap( false );
        Py_RETURN_NONE;
    }
    PyObject * remove( PyObject * object )
    {
        BW::string name;
        const bool byName = Script::setData( object, name ) == 0;
        if (!byName)
            PyErr_Clear();
        std::shared_ptr<StockComponentState> native;
        if (!byName && StockComponentWrapper::Check( object ))
            native = static_cast<StockComponentWrapper *>( object )->component();
        for (size_t index = 0; index < entries_.size(); ++index)
        {
            const ComponentEntry & entry = entries_[index];
            ScriptComponentState * script = dynamic_cast<ScriptComponentState *>( entry.component.get() );
            if ((byName && entry.name == name) ||
                (!byName && native && entry.component == native) ||
                (!byName && script && script->identity() == object))
            {
                std::shared_ptr<StockComponentState> removed = entry.component;
                entries_.erase( entries_.begin() + index );
                return removed->wrap( true );
            }
        }
        Py_RETURN_NONE;
    }
private:
    void visit( bool activate )
    {
        // Preserve original insertion order while containing synchronous
        // script removal/destruction of this system or another component.
        const std::vector<ComponentEntry> snapshot = entries_;
        for (size_t index = 0; index < snapshot.size() && valid_ && !destroying_; ++index)
        {
            const uint64 token = snapshot[index].token;
            const bool present = std::find_if( entries_.begin(), entries_.end(),
                [token]( const ComponentEntry & entry ) { return entry.token == token; } ) != entries_.end();
            if (!present || !snapshot[index].component->valid())
                continue;
            if (activate)
                snapshot[index].component->activate();
            else
                snapshot[index].component->deactivate();
        }
    }
    std::vector<ComponentEntry> entries_;
    uint64 nextToken_;
    bool destroying_;
};
}

class StockComponentSystem : public StockComponentWrapper
{
    Py_Header( StockComponentSystem, StockComponentWrapper )
public:
    StockComponentSystem( const std::shared_ptr<StockComponentState> & state, bool owning ) :
        StockComponentWrapper( state, &s_type_, owning ) {}
    PY_METHOD_DECLARE( py_addComponent )
    PY_METHOD_DECLARE( py_findComponent )
    PY_METHOD_DECLARE( py_removeComponent )
    static StockComponentSystem * New()
    {
        return new StockComponentSystem( std::make_shared<SystemState>(), true );
    }
    PY_AUTO_FACTORY_DECLARE( StockComponentSystem, END )
private:
    std::shared_ptr<SystemState> system() const
    {
        std::shared_ptr<SystemState> value = std::dynamic_pointer_cast<SystemState>( this->component() );
        if (!value)
            PyErr_SetString( PyExc_ValueError, "ComponentSystem has been destroyed" );
        return value;
    }
};

PyObject * SystemState::wrap( bool owning )
{
    return new StockComponentSystem( this->shared_from_this(), owning );
}

PyObject * StockComponentSystem::py_addComponent( PyObject * args )
{
    PyObject * object;
    PyObject * nameObject;
    if (!PyArg_ParseTuple( args, "OO:addComponent", &object, &nameObject ))
        return NULL;
    BW::string name;
    if (Script::setData( nameObject, name ) != 0)
        return NULL;
    std::shared_ptr<SystemState> owner = this->system();
    if (!owner)
        return NULL;
    std::shared_ptr<StockComponentState> value;
    if (StockComponentWrapper::Check( object ))
    {
        StockComponentWrapper * wrapper = static_cast<StockComponentWrapper *>( object );
        if (wrapper->component() == owner)
        {
            PyErr_SetString( PyExc_ValueError, "A component system cannot own itself" );
            return NULL;
        }
        value = wrapper->transfer();
        if (!value)
            return NULL;
    }
    else
    {
        // Exact 013eec30 requires these three attributes. Existence is checked
        // here; the engine reports non-callable values when actually called.
        if (!PyObject_HasAttrString( object, "activate" ) ||
            !PyObject_HasAttrString( object, "deactivate" ) ||
            !PyObject_HasAttrString( object, "destroy" ))
            Py_RETURN_NONE;
        value = std::make_shared<ScriptComponentState>( object );
    }
    if (!owner->add( value, name ))
    {
        PyObject * errorType;
        PyObject * errorValue;
        PyObject * errorTrace;
        PyErr_Fetch( &errorType, &errorValue, &errorTrace );
        value->deactivate();
        value->destroy();
        PyErr_Restore( errorType, errorValue, errorTrace );
        return NULL;
    }
    Py_RETURN_NONE;
}
PyObject * StockComponentSystem::py_findComponent( PyObject * args )
{
    PyObject * object;
    if (!PyArg_ParseTuple( args, "O:findComponent", &object ))
        return NULL;
    BW::string name;
    if (Script::setData( object, name ) != 0)
        return NULL;
    std::shared_ptr<SystemState> owner = this->system();
    return owner ? owner->find( name ) : NULL;
}
PyObject * StockComponentSystem::py_removeComponent( PyObject * args )
{
    PyObject * object;
    if (!PyArg_ParseTuple( args, "O:removeComponent", &object ))
        return NULL;
    std::shared_ptr<SystemState> owner = this->system();
    return owner ? owner->remove( object ) : NULL;
}
PY_TYPEOBJECT( StockComponentSystem )
PY_BEGIN_METHODS( StockComponentSystem )
    PY_METHOD( addComponent )
    PY_METHOD( findComponent )
    PY_METHOD( removeComponent )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( StockComponentSystem )
PY_END_ATTRIBUTES()
PY_FACTORY_NAMED( StockComponentSystem, "ComponentSystem", Svarog )
BW_END_NAMESPACE
