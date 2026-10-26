// Run the Python owner against real loopback Mercury request/reply sockets.
#include "cstdmf/cstdmf_windows.hpp"
#include "cstdmf/allocator.hpp"
#include "cstdmf/bgtask_manager.hpp"
#include "cstdmf/cstdmf_init.hpp"
#include "cstdmf/log_msg.hpp"
#include "network/endpoint.hpp"
#include "network/event_dispatcher.hpp"
#include "network/interface_element.hpp"
#include "network/interface_table.hpp"
#include "network/network_interface.hpp"
#include "network/udp_bundle.hpp"
#include "network/unpacked_message_header.hpp"
#include "stock_pinger.hpp"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <functional>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <Python.h>

namespace
{
using Clock = std::chrono::steady_clock;
void require( bool value, const char * message )
{
    if (!value) throw std::runtime_error( message );
}
void python( const char * code )
{
    if (PyRun_SimpleString( code ) != 0) throw std::runtime_error( "Python pinger contract failed" );
}
bool expression( const char * code )
{
    PyObject * scope = PyModule_GetDict( PyImport_AddModule( "__main__" ) );
    PyObject * result = PyRun_String( code, Py_eval_input, scope, scope );
    if (!result) { PyErr_Print(); throw std::runtime_error( "Pinger acknowledgement expression failed" ); }
    int accepted = PyObject_IsTrue( result );
    Py_DECREF( result );
    if (accepted < 0) throw std::runtime_error( "Pinger acknowledgement truth conversion failed" );
    return accepted != 0;
}
void until( BW::Mercury::EventDispatcher & dispatcher, BW::TaskManager & tasks,
    const std::function<bool()> & acknowledged, int seconds, const char * message )
{
    const auto deadline = Clock::now() + std::chrono::seconds( seconds );
    while (Clock::now() < deadline)
    {
        tasks.tick();
        dispatcher.processOnce( false );
        if (acknowledged()) return;
        std::this_thread::sleep_for( std::chrono::milliseconds( 1 ) );
    }
    throw std::runtime_error( message );
}
void publish( const char * name, const std::string & value )
{
    PyObject * text = PyString_FromStringAndSize( value.data(), static_cast<Py_ssize_t>( value.size() ) );
    PyObject * scope = PyModule_GetDict( PyImport_AddModule( "__main__" ) );
    require( text && PyDict_SetItemString( scope, name, text ) == 0, "Cannot publish the loopback address" );
    Py_DECREF( text );
}

class Responder : public BW::Mercury::InputMessageHandler, public BW::TimerHandler
{
    struct Pending
    {
        BW::Mercury::Address address;
        BW::Mercury::ReplyID reply;
        BW::TimerHandle timer;
        ~Pending() { timer.cancel(); }
    };
public:
    enum Policy { IMMEDIATE, SLOW_WARMUP, DROP_MIDDLE, DROP_ALL };
    Responder( BW::Mercury::EventDispatcher & dispatcher, Policy policy ) :
        dispatcher_( dispatcher ), network_( &dispatcher, BW::Mercury::NETWORK_INTERFACE_EXTERNAL,
            0, "127.0.0.1" ), policy_( policy )
    {
        require( network_.isGood(), "Cannot create a loopback Mercury responder" );
        BW::Mercury::InterfaceElement message( "ping", 2, BW::Mercury::FIXED_LENGTH_MESSAGE, 1 );
        network_.interfaceTable().serve( message, this );
    }
    ~Responder() { pending_.clear(); }
    std::string host() const
    {
        return "127.0.0.1:" + std::to_string( ntohs( network_.address().port ) );
    }
    void handleMessage( const BW::Mercury::Address & source,
        BW::Mercury::UnpackedMessageHeader & header, BW::BinaryIStream & data ) override
    {
        if (source.ip != htonl( 0x7f000001 ) || header.identifier != 2 ||
            header.length != 1 || header.replyID == BW::Mercury::REPLY_ID_NONE || data.remainingLength() != 1)
        {
            valid = false; data.finish(); return;
        }
        BW::uint8 sample = 255;
        data >> sample;
        if (data.error() || sample > 4) { valid = false; data.finish(); return; }
        samples.push_back( sample );
        received.push_back( Clock::now() );
        if (policy_ == DROP_ALL || (policy_ == DROP_MIDDLE && sample == 2)) return;
        if (policy_ == SLOW_WARMUP && sample == 0)
        {
            auto reply = std::make_unique<Pending>();
            reply->address = source; reply->reply = header.replyID;
            reply->timer = dispatcher_.addOnceOffTimer( 1000000, this, reply.get(), "Delayed ping warmup" );
            pending_.push_back( std::move( reply ) );
        }
        else send( source, header.replyID );
    }
    void handleTimeout( BW::TimerHandle, void * value ) override
    {
        auto entry = std::find_if( pending_.begin(), pending_.end(),
            [value](const std::unique_ptr<Pending> & pending) { return pending.get() == value; } );
        if (entry == pending_.end()) { valid = false; return; }
        (*entry)->timer.cancel();
        send( (*entry)->address, (*entry)->reply );
        pending_.erase( entry );
    }
    bool sequence( unsigned copies ) const
    {
        std::array<unsigned, 5> counts = {};
        for (unsigned sample : samples)
        {
            if (sample > 4 || ++counts[sample] > copies ||
                (sample && counts[sample] > counts[sample - 1])) return false;
        }
        return std::all_of( counts.begin(), counts.end(),
            [copies](unsigned count) { return count == copies; } );
    }
    bool valid = true;
    std::vector<unsigned> samples;
    std::vector<Clock::time_point> received;
private:
    void send( const BW::Mercury::Address & source, BW::Mercury::ReplyID id )
    {
        BW::Mercury::UDPBundle reply;
        reply.startReply( id, BW::Mercury::RELIABLE_NO );
        network_.send( source, reply );
    }
    BW::Mercury::EventDispatcher & dispatcher_;
    BW::Mercury::NetworkInterface network_;
    Policy policy_;
    std::vector<std::unique_ptr<Pending>> pending_;
};

class Barrier : public BW::TimerHandler
{
public:
    explicit Barrier( BW::Mercury::EventDispatcher & dispatcher )
    { timer_ = dispatcher.addOnceOffTimer( 400000, this, NULL, "Pinger completion drain acknowledgement" ); }
    ~Barrier() { timer_.cancel(); }
    void handleTimeout( BW::TimerHandle, void * ) override { timer_.cancel(); fired = true; }
    bool fired = false;
private:
    BW::TimerHandle timer_;
};
struct GateState
{
    std::atomic<bool> entered{ false }, release{ false }, acknowledged{ false }, fenced{ false };
};
class GateTask : public BW::BackgroundTask
{
public:
    explicit GateTask( GateState & state ) : BW::BackgroundTask( "Pinger DNS test gate" ), state_( state ) {}
    void doBackgroundTask( BW::TaskManager & tasks ) override
    {
        state_.entered = true;
        while (!state_.release) std::this_thread::sleep_for( std::chrono::milliseconds( 1 ) );
        tasks.addMainThreadTask( this );
    }
    void doMainThreadTask( BW::TaskManager & ) override { state_.acknowledged = true; }
private:
    GateState & state_;
};
class TaskFence : public BW::BackgroundTask
{
public:
    explicit TaskFence( GateState & state ) : BW::BackgroundTask( "Pinger stale DNS completion fence" ), state_( state ) {}
    void doBackgroundTask( BW::TaskManager & tasks ) override { tasks.addMainThreadTask( this ); }
    void doMainThreadTask( BW::TaskManager & ) override { state_.fenced = true; }
private:
    GateState & state_;
};
}

int main()
{
    BW_SYSTEMSTAGE_MAIN();
    BW::LogMsg::automatedTest( true );
    SetErrorMode( SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX );
    BW::CStdMf runtime;
    BW::initNetwork();
    Py_NoSiteFlag = 1; Py_Initialize();
    int outcome = 1;
    {
        GateState gate;
        BW::Mercury::EventDispatcher dispatcher;
        BW::TaskManager tasks;
        tasks.startThreads( "Pinger probe DNS", 1 );
        try
        {
            Responder fast( dispatcher, Responder::IMMEDIATE );
            Responder warm( dispatcher, Responder::SLOW_WARMUP );
            Responder partial( dispatcher, Responder::DROP_MIDDLE );
            Responder dropped( dispatcher, Responder::DROP_ALL );
            Responder cancelled( dispatcher, Responder::DROP_ALL );
            publish( "fast_host", fast.host() ); publish( "warm_host", warm.host() );
            publish( "partial_host", partial.host() ); publish( "dropped_host", dropped.host() );
            publish( "cancelled_host", cancelled.host() );
            require( BW::initialiseStockPinger( dispatcher, tasks ), "Cannot initialize the native pinger" );
            python( R"PY(
import BigWorld, thread, sys
main_thread=thread.get_ident()
events=[]
def record(rows):
    assert thread.get_ident()==main_thread
    assert type(rows) is tuple
    assert all(type(pair) is tuple and len(pair)==2 and type(pair[0]) is str and type(pair[1]) is int for pair in rows)
    events.append(rows)
p=BigWorld.WGPinger
for arguments in ((),('host',),(('host',),),([u'host'],),(['host',3],)):
    try:p.ping(*arguments)
    except TypeError:pass
    else:raise AssertionError('ping accepted an invalid host collection')
for method in (p.setOnPingCallback,p.clearOnPingCallback):
    try:method(1,2)
    except TypeError:pass
    else:raise AssertionError('callback method accepted an invalid argument count')
def nested(rows):
    record(rows);p.setOnPingCallback(record);p.ping([])
p.setOnPingCallback(nested);p.ping([])
assert events==[(),()]
p.clearOnPingCallback();p.ping([]);assert events==[(),()]
errors=[]
class ErrorSink(object):
    def write(self,text):errors.append(text)
    def flush(self):pass
stderr=sys.stderr;sys.stderr=ErrorSink()
def raises(rows):
    assert thread.get_ident()==main_thread
    raise ValueError('pinger callback fixture')
p.setOnPingCallback(42);p.ping([])
p.setOnPingCallback(raises);p.ping([])
sys.stderr=stderr
assert 'not callable' in ''.join(errors) and 'pinger callback fixture' in ''.join(errors)
p.setOnPingCallback(record);p.ping([]);assert events==[(),(),()]
events[:]=[]
)PY" );
            // Hold the only DNS worker, then complete an old generation after
            // shutdown/reinitialization. The fence is an actual TaskManager ack.
            tasks.addBackgroundTask( new GateTask( gate ) );
            until( dispatcher, tasks, [&] { return gate.entered.load(); }, 3, "DNS worker did not enter its gate" );
            python( "old=p\np.ping([fast_host])\n" );
            BW::finiStockPinger(); BW::finiStockPinger();
            require( BW::initialiseStockPinger( dispatcher, tasks ), "Cannot reinitialize after queued DNS" );
            python( "p=BigWorld.WGPinger\nassert p is not old\np.setOnPingCallback(record)\n"
                "try:old.ping([])\nexcept RuntimeError:pass\nelse:raise AssertionError('Old pinger reached the new owner')\n" );
            tasks.addBackgroundTask( new TaskFence( gate ), BW::TaskManager::MIN );
            gate.release = true;
            until( dispatcher, tasks, [&] { return gate.fenced.load() && gate.acknowledged.load(); }, 3, "Old DNS work did not reach its completion fence" );
            require( fast.samples.empty(), "Stale DNS sent traffic through a reinitialized owner" );
            python( "assert events==[]\n" );

            // Clear a callback while a real batch is pending. A dispatcher
            // timer acknowledges passage beyond its final 200 ms publication.
            python( "p.ping([fast_host])\np.clearOnPingCallback()\n" );
            until( dispatcher, tasks, [&] { return fast.samples.size() == 5; }, 4, "Cleared batch did not finish its five wire requests" );
            {
                Barrier barrier( dispatcher );
                until( dispatcher, tasks, [&] { return barrier.fired; }, 2, "Pinger publication drain did not acknowledge" );
            }
            python( "assert events==[]\n" );
            require( fast.valid && fast.sequence( 1 ), "Cleared batch changed the wire protocol" );
            fast.samples.clear(); fast.received.clear();

            // Cancel a genuine pending request. Further real batches below
            // prove that its shared dispatcher still works after reinit.
            python( "p.setOnPingCallback(record)\nsecond=p\np.ping([cancelled_host])\n" );
            until( dispatcher, tasks, [&] { return !cancelled.samples.empty(); }, 3, "Cancellation test did not observe a real request" );
            BW::finiStockPinger(); BW::finiStockPinger();
            require( BW::initialiseStockPinger( dispatcher, tasks ), "Cannot reinitialize after a pending network request" );
            python( R"PY(
p=BigWorld.WGPinger
assert p is not second
retired=[]
p.setOnPingCallback(lambda rows:retired.append(rows))
p.ping([warm_host,fast_host])
# Selection happens on completion, not when ping is called.
p.setOnPingCallback(record)
p.ping([fast_host,fast_host])
p.ping([partial_host])
p.ping([dropped_host])
)PY" );
            const auto began = Clock::now();
            until( dispatcher, tasks, [&] { return expression( "len(events)==4" ); }, 15, "Real ping batches did not complete" );
            python( R"PY(
assert retired==[] and len(events)==4
mixed=next(rows for rows in events if len(rows)==2 and rows[0][0]!=rows[1][0])
assert tuple(row[0] for row in mixed)==(fast_host,warm_host)
assert all(0<=row[1]<100 for row in mixed)
duplicates=next(rows for rows in events if len(rows)==2 and rows[0][0]==rows[1][0])
assert tuple(row[0] for row in duplicates)==(fast_host,fast_host)
assert all(0<=row[1]<100 for row in duplicates)
assert next(rows for rows in events if len(rows)==1 and rows[0][0]==partial_host)[0][1]>=0
assert next(rows for rows in events if len(rows)==1 and rows[0][0]==dropped_host)==((dropped_host,-1),)
)PY" );
            require( Clock::now() - began >= std::chrono::seconds( 10 ), "Dropped requests did not exercise the real two-second timeout" );
            require( fast.valid && warm.valid && partial.valid && dropped.valid && cancelled.valid,
                "The native pinger changed message id, length, reply flag or loopback endpoint" );
            require( fast.sequence( 3 ) && warm.sequence( 1 ) && partial.sequence( 1 ) && dropped.sequence( 1 ),
                "Five-sample sequence or duplicate/batch independence changed" );
            require( cancelled.samples == std::vector<unsigned>( 1, 0 ), "Cancelled owner sent another request" );
            python( "assert not any(rows and rows[0][0]==cancelled_host for rows in events)\n"
                "p.clearOnPingCallback()\np.ping([])\nassert len(events)==4\n" );
            BW::finiStockPinger(); BW::finiStockPinger();
            require( !PyErr_Occurred(), "Pinger left a Python exception pending" );
            std::cout << "Real Mercury ping wire, five samples, warmup exclusion, timeouts, callback reentry and stale-DNS/network teardown passed" << std::endl;
            outcome = 0;
        }
        catch (const std::exception & error)
        {
            std::cerr << "Native pinger probe failed: " << error.what() << std::endl;
            if (PyErr_Occurred()) PyErr_Print();
        }
        BW::finiStockPinger();
        gate.release = true;
        tasks.stopAll( false, true );
    }
    Py_Finalize();
    BW::finiNetwork();
    BW::LogMsg::fini();
    return outcome;
}
