#include "pch.hpp"
#include "stock_vehicle_filter.hpp"

#include "cstdmf/debug.hpp"
#include "cstdmf/debug_message_file_logger.hpp"
#include "cstdmf/main_loop_task.hpp"

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

class StockVehicleFilterStatistics : public MainLoopTask
{
public:
    StockVehicleFilterStatistics()
    {
        // Script/App advances EntityManager and its attached native filters.
        // Sample their real extrapolation state before the GUI is updated.
        MainLoopTasks::root().add( this, "StockVehicleFilterStatistics",
            ">Script", "<Camera", NULL );
    }
    void tick( float, float ) override
    {
        tickStockVehicleFilterLagDetection();
    }
};

StockVehicleFilterStatistics s_stockVehicleFilterStatistics;

} // namespace
BW_END_NAMESPACE
