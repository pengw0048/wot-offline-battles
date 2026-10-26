#include "pch.hpp"
#include "client_session.hpp"

#include "connection_control.hpp"
#include "entity_type.hpp"
#include "connection_model/bw_connection.hpp"
#include "connection_model/bw_entities.hpp"
#include "connection_model/bw_entity.hpp"
#include "connection_model/bw_null_connection.hpp"
#include "cstdmf/debug.hpp"
#include "cstdmf/memory_stream.hpp"
#include "pyscript/script.hpp"
#include "space/space_manager.hpp"

DECLARE_DEBUG_COMPONENT2( "StockClientSession", 0 )

BW_BEGIN_NAMESPACE

namespace
{
BWNullConnection * localConnection( SpaceID spaceID )
{
    // ConnectionControl::createLocalSpace always creates a BWNullConnection
    // for this local space. Other connection types are deliberately rejected.
    if (!SpaceManager::instance().isLocalSpace( spaceID ))
    {
        PyErr_SetString( PyExc_ValueError, "Stock session requires a native local space" );
        return NULL;
    }
    BWConnection * connection = ConnectionControl::instance().pSpaceConnection( spaceID );
    if (!connection)
    {
        PyErr_SetString( PyExc_ValueError, "Stock session local space has no connection" );
        return NULL;
    }
    return static_cast<BWNullConnection *>( connection );
}

bool beforeVehicleCreate( EntityID id, void * context )
{
    PyObject * callable = static_cast<PyObject *>( context );
    PyObjectPtr args( Py_BuildValue( "(I)", id ), PyObjectPtr::STEAL_REFERENCE );
    PyObjectPtr result( args ? PyObject_CallObject( callable, args.get() ) : NULL,
        PyObjectPtr::STEAL_REFERENCE );
    if (!result)
        return false;
    // Explicit acceptance is required; an accidentally empty selector cannot
    // silently admit construction before identity/arena state is installed.
    if (result.get() != Py_True)
    {
        PyErr_SetString( PyExc_RuntimeError, "Stock Vehicle identity selector did not return True" );
        return false;
    }
    return true;
}

void rollbackPlayerCreation( BWNullConnection & connection )
{
    PyObject * type = NULL, * value = NULL, * traceback = NULL;
    PyErr_Fetch( &type, &value, &traceback );
    if (connection.pPlayer() && connection.pPlayer()->isInAoI())
        connection.destroyPlayerCellEntity();
    connection.destroyPlayerBaseEntity();
    PyErr_Restore( type, value, traceback );
}

PyObject * standaloneCreateSessionAvatar( SpaceID spaceID,
    const Vector3 & position, const Vector3 & directionRPY,
    const ScriptDict & properties )
{
    BWNullConnection * connection = localConnection( spaceID );
    EntityType * type = EntityType::find( "Avatar" );
    if (!connection)
        return NULL;
    if (!type || !type->pPlayerClass())
    {
        PyErr_SetString( PyExc_RuntimeError, "Original Avatar entity/player type is not loaded" );
        return NULL;
    }
    if (connection->pPlayer())
    {
        PyErr_SetString( PyExc_RuntimeError, "Local space already has a player entity" );
        return NULL;
    }
    // The public createPlayerEntity sequence, using the selected local-space
    // connection instead of the unrelated main offline connection. The engine
    // invokes the original player/entity callbacks in its normal order.
    MemoryOStream stream( 4096 );
    type->prepareBasePlayerStream( stream, properties );
    if (PyErr_Occurred())
        return NULL;
    const EntityID id = connection->createNewPlayerBaseEntity( type->index(), stream );
    if (id == NULL_ENTITY_ID)
    {
        PyErr_SetString( PyExc_RuntimeError, "Native Avatar base creation failed" );
        return NULL;
    }
    stream.reset();
    type->prepareCellPlayerStream( stream, properties );
    if (PyErr_Occurred())
    {
        rollbackPlayerCreation( *connection );
        return NULL;
    }
    if (!connection->giveCellToPlayerBaseEntity( position,
            Direction3D( directionRPY ), stream ))
    {
        PyErr_SetString( PyExc_RuntimeError, "Native Avatar cell creation failed" );
        rollbackPlayerCreation( *connection );
        return NULL;
    }
    return ScriptObject::createFrom( id ).newRef();
}

PyObject * standaloneCreateSessionVehicle( SpaceID spaceID,
    const Vector3 & position, const Vector3 & directionRPY,
    const ScriptDict & properties, const ScriptObject & beforeCreate )
{
    BWNullConnection * connection = localConnection( spaceID );
    EntityType * type = EntityType::find( "Vehicle" );
    if (!connection)
        return NULL;
    if (!type || !connection->pPlayer() || !PyCallable_Check( beforeCreate.get() ))
    {
        PyErr_SetString( PyExc_RuntimeError,
            "Original Vehicle type, native player and identity selector are required" );
        return NULL;
    }
    MemoryOStream stream( 4096 );
    type->prepareCreationStream( stream, properties );
    if (PyErr_Occurred())
        return NULL;
    BWEntity * vehicle = connection->createLocalEntity( type->index(), spaceID,
        NULL_ENTITY_ID, position, Direction3D( directionRPY ), stream,
        &beforeVehicleCreate, beforeCreate.get() );
    if (!vehicle)
    {
        if (!PyErr_Occurred())
            PyErr_SetString( PyExc_RuntimeError, "Native stock Vehicle creation failed" );
        return NULL;
    }
    return ScriptObject::createFrom( vehicle->entityID() ).newRef();
}

bool standaloneDestroySessionVehicle( SpaceID spaceID, EntityID id )
{
    BWNullConnection * connection = localConnection( spaceID );
    if (!connection)
        return false;
    if (!BWEntities::isLocalEntity( id ))
    {
        PyErr_SetString( PyExc_ValueError, "Stock Vehicle is not a client-only identity" );
        return false;
    }
    BWEntity * vehicle = connection->entities().find( id );
    if (vehicle && vehicle->spaceID() != spaceID)
    {
        PyErr_SetString( PyExc_RuntimeError, "Stock Vehicle belongs to another space" );
        return false;
    }
    // find() sees only active entities. destroyLocalEntity's handleEntityLeave
    // also finds pending prerequisites/passengers and returns false only when
    // the id is already absent. Call it even for an unpublished pending entity.
    connection->destroyLocalEntity( id );
    return true;
}

bool standaloneDestroySessionAvatar( SpaceID spaceID, EntityID id )
{
    BWNullConnection * connection = localConnection( spaceID );
    if (!connection)
        return false;
    BWEntity * avatar = connection->pPlayer();
    if (!avatar)
    {
        // A partially failed player startup can still leave local entities
        // pending. This connection is exclusively owned by the stock session.
        connection->clearAllEntities();
        return true;
    }
    if (avatar->entityID() != id)
    {
        PyErr_SetString( PyExc_RuntimeError, "Refusing to destroy another native player" );
        return false;
    }
    if (avatar->isInAoI() && !connection->destroyPlayerCellEntity())
    {
        PyErr_SetString( PyExc_RuntimeError, "Native Avatar cell destruction failed" );
        return false;
    }
    connection->destroyPlayerBaseEntity();
    return true;
}
} // namespace

PY_AUTO_MODULE_FUNCTION( RETOWN, standaloneCreateSessionAvatar,
    ARG( SpaceID, ARG( Vector3, ARG( Vector3, ARG( ScriptDict, END ) ) ) ), BigWorld )
PY_AUTO_MODULE_FUNCTION( RETOWN, standaloneCreateSessionVehicle,
    ARG( SpaceID, ARG( Vector3, ARG( Vector3, ARG( ScriptDict,
        ARG( ScriptObject, END ) ) ) ) ), BigWorld )
PY_AUTO_MODULE_FUNCTION( RETOK, standaloneDestroySessionVehicle,
    ARG( SpaceID, ARG( EntityID, END ) ), BigWorld )
PY_AUTO_MODULE_FUNCTION( RETOK, standaloneDestroySessionAvatar,
    ARG( SpaceID, ARG( EntityID, END ) ), BigWorld )

struct ClientSession::State
{
    State() : stage( IDLE ) {}
    Stage stage;
    BW::string error;
    PyObjectPtr controller;

    bool failed( const char * operation )
    {
        if (error.empty())
        {
            error = operation;
            if (PyErr_Occurred())
            {
                PyObject * type = NULL, * value = NULL, * traceback = NULL;
                PyErr_Fetch( &type, &value, &traceback );
                PyErr_NormalizeException( &type, &value, &traceback );
                PyObject * text = value ? PyObject_Str( value ) : NULL;
                if (text && PyString_Check( text ))
                {
                    error += ": ";
                    error += PyString_AsString( text );
                }
                Py_XDECREF( text );
                PyErr_Restore( type, value, traceback );
            }
        }
        stage = FAILED;
        ERROR_MSG( "ClientSession: %s\n", error.c_str() );
        if (PyErr_Occurred())
            PyErr_Print();
        return false;
    }

    bool call( const char * method, PyObject * args )
    {
        PyObjectPtr function( controller ?
            PyObject_GetAttrString( controller.get(), method ) : NULL,
            PyObjectPtr::STEAL_REFERENCE );
        PyObjectPtr result( function && args ? PyObject_CallObject( function.get(), args ) : NULL,
            PyObjectPtr::STEAL_REFERENCE );
        return result ? true : failed( method );
    }
};

ClientSession::ClientSession() : state_( new State() ) {}
ClientSession::~ClientSession()
{
    if (state_->controller && state_->stage != IDLE)
        WARNING_MSG( "ClientSession: owner must stop the stock session before engine scripting teardown\n" );
    delete state_;
}

bool ClientSession::begin( SpaceID spaceID, const ScriptDict & properties,
    const Vector3 & position, const Vector3 & direction,
    const ScriptObject & server )
{
    if (state_->stage != IDLE)
        return state_->failed( "begin requires an idle session" );
    state_->error.clear();
    state_->stage = AVATAR_LOADING;
    PyObjectPtr module( PyImport_ImportModule( "StandaloneClientSession" ), PyObjectPtr::STEAL_REFERENCE );
    PyObjectPtr factory( module ? PyObject_GetAttrString( module.get(), "ClientSession" ) : NULL,
        PyObjectPtr::STEAL_REFERENCE );
    state_->controller = PyObjectPtr( factory ? PyObject_CallObject( factory.get(), NULL ) : NULL,
        PyObjectPtr::STEAL_REFERENCE );
    if (!state_->controller)
        return state_->failed( "load original client session adapter" );
    PyObjectPtr pyPosition( Script::getData( position ), PyObjectPtr::STEAL_REFERENCE );
    PyObjectPtr pyDirection( Script::getData( direction ), PyObjectPtr::STEAL_REFERENCE );
    PyObjectPtr args( pyPosition && pyDirection && server ?
        Py_BuildValue( "(IOOOO)", spaceID, properties.get(), pyPosition.get(),
            pyDirection.get(), server.get() ) : NULL, PyObjectPtr::STEAL_REFERENCE );
    return state_->call( "begin", args.get() );
}

bool ClientSession::spawnOwnVehicle( const ScriptDict & properties,
    const Vector3 & position, const Vector3 & direction,
    const ScriptObject & server, const ScriptObject & selectVehicle )
{
    PyObjectPtr pyPosition( Script::getData( position ), PyObjectPtr::STEAL_REFERENCE );
    PyObjectPtr pyDirection( Script::getData( direction ), PyObjectPtr::STEAL_REFERENCE );
    PyObjectPtr args( pyPosition && pyDirection && server && selectVehicle ?
        Py_BuildValue( "(OOOOO)", properties.get(), pyPosition.get(),
            pyDirection.get(), server.get(), selectVehicle.get() ) : NULL,
        PyObjectPtr::STEAL_REFERENCE );
    if (!state_->call( "spawnOwnVehicle", args.get() ))
        return false;
    return advance();
}

bool ClientSession::advance()
{
    if (!state_->controller || state_->stage == IDLE || state_->stage == FAILED)
        return state_->stage != FAILED;
    PyObjectPtr args( PyTuple_New( 0 ), PyObjectPtr::STEAL_REFERENCE );
    if (!state_->call( "advance", args.get() ))
        return false;
    PyObjectPtr stage( PyObject_GetAttrString( state_->controller.get(), "stage" ),
        PyObjectPtr::STEAL_REFERENCE );
    if (!stage || !PyString_Check( stage.get() ))
        return state_->failed( "original client session returned no lifecycle stage" );
    const BW::string value = PyString_AsString( stage.get() );
    Stage next;
    if (value == "avatar_loading") next = AVATAR_LOADING;
    else if (value == "vehicle_loading") next = VEHICLE_LOADING;
    else if (value == "battle_loading") next = BATTLE_LOADING;
    else if (value == "active") next = ACTIVE;
    else return state_->failed( "original client session entered an unexpected stage" );
    if (next != state_->stage)
        INFO_MSG( "ClientSession: original lifecycle stage=%s\n", value.c_str() );
    state_->stage = next;
    return true;
}

bool ClientSession::stop()
{
    if (!state_->controller)
    {
        state_->stage = IDLE;
        return true;
    }
    state_->stage = STOPPING;
    PyObjectPtr args( PyTuple_New( 0 ), PyObjectPtr::STEAL_REFERENCE );
    if (!state_->call( "stop", args.get() ))
        return false;
    state_->controller = PyObjectPtr();
    state_->stage = IDLE;
    return true;
}

ClientSession::Stage ClientSession::stage() const { return state_->stage; }
const BW::string & ClientSession::error() const { return state_->error; }
ScriptObject ClientSession::avatar() const
{
    return ScriptObject( state_->controller ?
        PyObject_GetAttrString( state_->controller.get(), "avatar" ) : NULL,
        ScriptObject::FROM_NEW_REFERENCE );
}
ScriptObject ClientSession::vehicle() const
{
    return ScriptObject( state_->controller ?
        PyObject_GetAttrString( state_->controller.get(), "vehicle" ) : NULL,
        ScriptObject::FROM_NEW_REFERENCE );
}

BW_END_NAMESPACE
