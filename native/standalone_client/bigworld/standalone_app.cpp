#include "pch.hpp"

#include "app_config.hpp"
#include "camera_app.hpp"
#include "client_camera.hpp"
#include "connection_control.hpp"

#include "camera/free_camera.hpp"
#include "chunk/chunk_manager.hpp"
#include "chunk_scene_adapter/client_chunk_space_adapter.hpp"
#include "cstdmf/command_line.hpp"
#include "cstdmf/debug.hpp"
#include "cstdmf/main_loop_task.hpp"
#include "moo/render_context.hpp"
#include "resmgr/datasection.hpp"
#include "space/client_space.hpp"
#include "space/deprecated_space_helpers.hpp"
#include "space/space_manager.hpp"

DECLARE_DEBUG_COMPONENT2( "Standalone", 0 )

BW_BEGIN_NAMESPACE

namespace
{

// Scene ownership stays native. The personality module only satisfies the
// public engine's scripting lifecycle; it does not create a stock WoT client.
class StandaloneApp : public MainLoopTask
{
public:
    StandaloneApp() : spaceID_( 0 ), mapped_( false ), ready_( false )
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

        // Use the same native camera-space wiring as BigWorld.cameraSpaceID.
        DeprecatedSpaceHelpers::cameraSpace( space_ );
        ChunkManager::instance().camera( Moo::rc().invView(),
            ClientChunkSpaceAdapter::getChunkSpace( space_ ) );

        INFO_MSG( "StandaloneApp: native application started; space=%u, geometry=%s\n",
            spaceID_, path_.empty() ? "<unmapped>" : path_.c_str() );
        return true;
    }

    virtual void tick( float /* dGameTime */, float /* dRenderTime */ )
    {
        if (mapped_ && !ready_ && space_ && space_->loadStatus() >= 1.f)
        {
            ready_ = true;
            INFO_MSG( "StandaloneApp: native geometry loading completed: %s\n",
                path_.c_str() );
        }
    }

    virtual void fini()
    {
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
        if (spaceID_ != 0)
        {
            ConnectionControl::instance().clearLocalSpace( spaceID_ );
            spaceID_ = 0;
        }
    }

private:
    SpaceID spaceID_;
    ClientSpacePtr space_;
    BW::string path_;
    bool mapped_;
    bool ready_;
};

StandaloneApp s_standaloneApp;

} // namespace

BW_END_NAMESPACE
