#include "pch.hpp"

#include "app_config.hpp"
#include "camera_app.hpp"
#include "client_camera.hpp"
#include "connection_control.hpp"
#include "projectile_scene.hpp"
#include "vehicle_scene.hpp"

#include "camera/free_camera.hpp"
#include "chunk/chunk_manager.hpp"
#include "chunk_scene_adapter/client_chunk_space_adapter.hpp"
#include "cstdmf/command_line.hpp"
#include "cstdmf/debug.hpp"
#include "cstdmf/debug_message_file_logger.hpp"
#include "cstdmf/main_loop_task.hpp"
#include "duplo/pymodel.hpp"
#include "moo/render_context.hpp"
#include "resmgr/bwresource.hpp"
#include "resmgr/datasection.hpp"
#include "space/client_space.hpp"
#include "space/deprecated_space_helpers.hpp"
#include "space/entity_embodiment.hpp"
#include "space/space_manager.hpp"

#include <cmath>
#include <sstream>

DECLARE_DEBUG_COMPONENT2( "Standalone", 0 )

BW_BEGIN_NAMESPACE

namespace
{

class StandaloneLog : public MainLoopTask
{
public:
    StandaloneLog() : logger_( NULL )
    {
        MainLoopTasks::root().add( this, "StandaloneLog", "<Device", NULL );
    }

    virtual bool init()
    {
        logger_ = new DebugMessageFileLogger();
        logger_->config( "standalone.log", DebugMessageFileLogger::SERVERITY_ALL,
            "", DebugMessageFileLogger::SOURCE_ALL,
            DebugMessageFileLogger::OVERWRITE, false );
        logger_->enable( true );
        INFO_MSG( "StandaloneLog: native startup logging enabled\n" );
        return true;
    }

    virtual void fini()
    {
        delete logger_;
        logger_ = NULL;
    }

private:
    DebugMessageFileLogger * logger_;
};

StandaloneLog s_standaloneLog;

// Scene ownership stays native. The personality module only satisfies the
// public engine's scripting lifecycle; it does not create a stock WoT client.
class StandaloneApp : public MainLoopTask
{
public:
    StandaloneApp() : spaceID_( 0 ), mapped_( false ), ready_( false ),
        cameraPlaced_( false )
    {
        // Ordering rules refer to siblings. A top-level task runs after the
        // complete Finale group, and is finalised before it disconnects.
        MainLoopTasks::root().add( this, "WotStandalone", ">Finale", NULL );
    }

    virtual bool init()
    {
        // MainLoopTasks continues initialising siblings after a failed init.
        // Do not enter native creation after Device or World failed earlier.
        if (!ConnectionControl::pInstance() || !CameraApp::pInstance() ||
            !SpaceManager::instance().isReady())
        {
            ERROR_MSG( "StandaloneApp: engine startup did not create the scene services\n" );
            return false;
        }
        DataSectionPtr config = AppConfig::instance().pRoot();
        path_ = config->readString( "standalone/space", "" );
        CommandLine commandLine( ::GetCommandLineA() );
        const char * requestedSpace = commandLine.getFullParam( "--space" );
        if (requestedSpace)
        {
            if (requestedSpace[0] == '\0' || requestedSpace[0] == '-')
            {
                ERROR_MSG( "StandaloneApp: --space requires a resource directory\n" );
                return false;
            }
            path_ = requestedSpace;
        }
        BW::string modelPath = config->readString( "standalone/model", "" );
        const char * requestedModel = commandLine.getFullParam( "--model" );
        if (requestedModel)
        {
            if (requestedModel[0] == '\0' || requestedModel[0] == '-')
            {
                ERROR_MSG( "StandaloneApp: --model requires a model resource path\n" );
                return false;
            }
            modelPath = requestedModel;
        }
        BW::string assemblyPath = config->readString( "standalone/assembly", "" );
        const char * requestedAssembly = commandLine.getFullParam( "--assembly" );
        if (requestedAssembly)
        {
            if (requestedAssembly[0] == '\0' || requestedAssembly[0] == '-')
            {
                ERROR_MSG( "StandaloneApp: --assembly requires an XML resource path\n" );
                return false;
            }
            assemblyPath = requestedAssembly;
        }
        Vector3 modelPosition = config->readVector3(
            "standalone/modelPosition", Vector3( 0.f, 0.f, 0.f ) );
        const char * requestedPosition = commandLine.getFullParam( "--model-position" );
        if (requestedPosition)
        {
            std::istringstream input( requestedPosition );
            char separator1 = 0;
            char separator2 = 0;
            if (!(input >> modelPosition.x >> separator1 >> modelPosition.y >>
                    separator2 >> modelPosition.z) ||
                separator1 != ',' || separator2 != ',' ||
                !(input >> std::ws).eof())
            {
                ERROR_MSG( "StandaloneApp: --model-position requires x,y,z\n" );
                return false;
            }
        }
        if (!std::isfinite( modelPosition.x ) ||
            !std::isfinite( modelPosition.y ) ||
            !std::isfinite( modelPosition.z ))
        {
            ERROR_MSG( "StandaloneApp: model position must be finite\n" );
            return false;
        }
        spaceID_ = ConnectionControl::instance().createLocalSpace();
        space_ = SpaceManager::instance().space( spaceID_ );
        if (!space_)
        {
            ERROR_MSG( "StandaloneApp: native local space creation failed\n" );
            this->fini();
            return false;
        }

        if (!path_.empty())
        {
            Matrix identity = Matrix::identity;
            // A mapping ID is local to this private space. Do not patch the
            // engine's binary format version or reinterpret rejected data.
            mapped_ = space_->addMapping( 1, identity, path_ );
            if (!mapped_)
            {
                ERROR_MSG( "StandaloneApp: native geometry mapping failed: %s\n",
                    path_.c_str() );
                this->fini();
                return false;
            }
        }

        BaseCameraPtr camera( new FreeCamera(),
            PyObjectPtr::STEAL_REFERENCE );
        Matrix view;
        view.lookAt( config->readVector3( "standalone/cameraPosition",
            Vector3( 0.f, 25.f, 0.f ) ), Vector3( 0.f, 0.f, 1.f ),
            Vector3( 0.f, 1.f, 0.f ) );
        camera->set( view );
        camera->spaceID( spaceID_ );
        CameraApp::instance().clientCamera().camera( camera );
        camera_ = camera;

        // Use the same native camera-space wiring as BigWorld.cameraSpaceID.
        DeprecatedSpaceHelpers::cameraSpace( space_ );
        ChunkManager::instance().camera( Moo::rc().invView(),
            ClientChunkSpaceAdapter::getChunkSpace( space_ ) );

        if (!modelPath.empty() && !this->loadModel( modelPath, modelPosition ))
        {
            this->fini();
            return false;
        }
        if (!assemblyPath.empty() &&
            !this->loadAssembly( assemblyPath, modelPosition ))
        {
            this->fini();
            return false;
        }

        INFO_MSG( "StandaloneApp: native application started; space=%u, geometry=%s\n",
            spaceID_, path_.empty() ? "<unmapped>" : path_.c_str() );
        return true;
    }

    virtual void tick( float dGameTime, float /* dRenderTime */ )
    {
        vehicle_.advance( dGameTime );
        projectiles_.setAssemblyTransform( vehicle_.transform() );
        if (vehicle_.supported() && camera_)
        {
            const Vector3 origin = vehicle_.transform().applyToOrigin();
            Matrix view;
            if (!cameraPlaced_)
            {
                const Vector3 eye = origin + Vector3( 8.f, 5.f, -12.f );
                view.lookAt( eye, origin + Vector3( 0.f, 1.f, 0.f ) - eye,
                    Vector3( 0.f, 1.f, 0.f ) );
                cameraPlaced_ = true;
            }
            else
            {
                // Follow translation while preserving mouse aim and free-camera
                // offsets. Arrow keys still allow inspecting the real scene.
                Matrix inverseView = camera_->invView();
                inverseView.translation( inverseView.applyToOrigin() +
                    origin - cameraOrigin_ );
                view.invert( inverseView );
            }
            camera_->set( view );
            cameraOrigin_ = origin;
        }
        projectiles_.advance( dGameTime );
        for (EntityEmbodiments::iterator model = models_.begin();
            model != models_.end(); ++model)
        {
            // Match GlobalEmbodiments::tick. The native space owns the
            // attachment's tick, animation update, visibility, and draw pass.
            (*model)->move( dGameTime );
        }
        if (mapped_ && !ready_ && space_ && space_->loadStatus() >= 1.f)
        {
            ready_ = true;
            INFO_MSG( "StandaloneApp: native geometry loading completed: %s\n",
                path_.c_str() );
        }
    }

    virtual void fini()
    {
        vehicle_.clear();
        projectiles_.clear();
        for (EntityEmbodiments::iterator model = models_.begin();
            model != models_.end(); ++model)
        {
            (*model)->leaveSpace();
        }
        models_.clear();
        if (space_ && DeprecatedSpaceHelpers::cameraSpace() == space_)
        {
            DeprecatedSpaceHelpers::cameraSpace( NULL );
            ChunkManager::instance().camera( Moo::rc().invView(), NULL );
        }
        if (space_ && mapped_)
        {
            space_->delMapping( 1 );
        }
        space_ = NULL;
        mapped_ = false;
        ready_ = false;
        camera_ = NULL;
        cameraPlaced_ = false;
        if (spaceID_ != 0)
        {
            ConnectionControl::instance().clearLocalSpace( spaceID_ );
            spaceID_ = 0;
        }
    }

private:
    bool loadModel( const BW::string & resource, const Vector3 & position )
    {
        if (!std::isfinite( position.x ) || !std::isfinite( position.y ) ||
            !std::isfinite( position.z ))
        {
            ERROR_MSG( "StandaloneApp: model position must be finite: %s\n",
                resource.c_str() );
            return false;
        }
        PyObjectPtr args( Py_BuildValue( "(s)", resource.c_str() ),
            PyObjectPtr::STEAL_REFERENCE );
        if (!args)
        {
            PyErr_Print();
            return false;
        }
        // This native factory constructs the resource's real SuperModel and
        // fails when a requested model cannot be loaded.
        ScriptObject object( PyModel::pyNew( args.get() ),
            ScriptObject::FROM_NEW_REFERENCE );
        if (!object)
        {
            ERROR_MSG( "StandaloneApp: model resource could not be loaded: %s\n",
                resource.c_str() );
            PyErr_Print();
            return false;
        }
        IEntityEmbodimentPtr model =
            SpaceManager::instance().createEntityEmbodiment( object );
        if (!model)
        {
            ERROR_MSG( "StandaloneApp: model could not become a scene attachment: %s\n",
                resource.c_str() );
            PyErr_Print();
            return false;
        }
        Matrix transform;
        transform.setTranslate( position );
        model->worldTransform( transform );
        model->enterSpace( space_ );
        models_.push_back( model );
        INFO_MSG( "StandaloneApp: native model attached: %s at (%g,%g,%g)\n",
            resource.c_str(), position.x, position.y, position.z );
        return true;
    }

    bool loadAssembly( const BW::string & resource, const Vector3 & origin )
    {
        DataSectionPtr assembly = BWResource::openSection( resource );
        BW::vector<DataSectionPtr> components;
        if (assembly)
        {
            assembly->openSections( "component", components );
        }
        if (components.empty())
        {
            ERROR_MSG( "StandaloneApp: assembly has no model components: %s\n",
                resource.c_str() );
            return false;
        }
        EntityEmbodiments assemblyModels;
        for (size_t index = 0; index < components.size(); ++index)
        {
            DataSectionPtr component = components[index];
            BW::string model = component->readString( "model", "" );
            if (model.empty() || !component->openSection( "position" ))
            {
                ERROR_MSG( "StandaloneApp: assembly component needs model and position: %s [%s]\n",
                    resource.c_str(), component->readString( "name", "" ).c_str() );
                return false;
            }
            // Positions are cumulative offsets extracted from the real vehicle
            // descriptor. Only the explicitly supplied scene origin is added.
            Vector3 position = origin + component->readVector3( "position" );
            if (!this->loadModel( model, position ))
            {
                return false;
            }
            assemblyModels.push_back( models_.back() );
        }
        INFO_MSG( "StandaloneApp: native model assembly attached: %s (%u components)\n",
            resource.c_str(), static_cast<uint32>( components.size() ) );
        return vehicle_.configure( assembly, origin,
            mapped_ ? space_ : ClientSpacePtr(), assemblyModels ) &&
            projectiles_.configure( assembly, origin,
                mapped_ ? space_ : ClientSpacePtr() );
    }

    SpaceID spaceID_;
    ClientSpacePtr space_;
    EntityEmbodiments models_;
    ProjectileScene projectiles_;
    VehicleScene vehicle_;
    BaseCameraPtr camera_;
    Vector3 cameraOrigin_;
    BW::string path_;
    bool mapped_;
    bool ready_;
    bool cameraPlaced_;
};

StandaloneApp s_standaloneApp;

} // namespace

BW_END_NAMESPACE
