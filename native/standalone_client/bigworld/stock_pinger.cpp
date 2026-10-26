#include "pch.hpp"
#include "stock_pinger.hpp"
#include "cstdmf/bgtask_manager.hpp"
#include "network/endpoint.hpp"
#include "network/event_dispatcher.hpp"
#include "network/network_interface.hpp"
#include "network/udp_bundle.hpp"
#include "pyscript/script.hpp"
#if defined(snprintf)
#undef snprintf
#endif
#if defined(vsnprintf)
#undef vsnprintf
#endif
#if defined(hypot)
#undef hypot
#endif
#include <array>
#include <chrono>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

BW_BEGIN_NAMESPACE
namespace StockPingerDetail {
// #1513 LoginInterface: login=0, probe=1, ping=2 (one-byte fixed body).
// The public engine's login protocol has a different message at index 2.
const Mercury::InterfaceElement pingMessage("ping", 2, Mercury::FIXED_LENGTH_MESSAGE, 1);
struct PythonPinger { PyObject_HEAD PyObject* callback; };
struct Batch {
    explicit Batch(std::size_t count) : remaining(count) { results.reserve(count); }
    std::size_t remaining;
    std::vector<std::pair<std::string, int>> results;
};
class Probe;
class Owner : public std::enable_shared_from_this<Owner> {
public:
    Owner(Mercury::EventDispatcher& dispatcher, TaskManager& tasks, PythonPinger* object)
        : dispatcher(dispatcher), tasks(tasks), object(object) { Py_INCREF(object); }
    ~Owner() { shutdown(); Py_DECREF(object); }
    void shutdown();
    void launch(const std::shared_ptr<Batch>& batch, const std::string& host,
                const Mercury::Address& address);
    void complete(const std::shared_ptr<Batch>& batch, const std::string& host, int milliseconds);
    void publish(const std::shared_ptr<Batch>& batch);
    Mercury::EventDispatcher& dispatcher;
    TaskManager& tasks;
    PythonPinger* object;
    bool active = true;
    std::unique_ptr<Mercury::NetworkInterface> network;
    std::set<std::shared_ptr<Probe>> probes;
};
std::shared_ptr<Owner> owner;

class Probe final : public Mercury::ShutdownSafeReplyMessageHandler,
                    public TimerHandler, public std::enable_shared_from_this<Probe> {
public:
    Probe(const std::shared_ptr<Owner>& owner, std::shared_ptr<Batch> batch,
          std::string host, const Mercury::Address& address)
        : owner_(owner), batch_(std::move(batch)), host_(std::move(host)), address_(address) {
        samples_.fill(-1.0);
    }
    ~Probe() { timer_.cancel(); }
    void cancel() { cancelled_ = true; timer_.cancel(); }
    void send() {
        auto keep = shared_from_this();
        auto context = owner_.lock();
        if (!context || !context->active || cancelled_) return;
        ++sample_;
        Mercury::UDPBundle bundle;
        bundle.startRequest(pingMessage, this, NULL, 2000000, Mercury::RELIABLE_NO);
        bundle << static_cast<uint8>(sample_);
        started_ = std::chrono::steady_clock::now();
        context->network->send(address_, bundle);
    }
    void handleMessage(const Mercury::Address&, Mercury::UnpackedMessageHeader&,
                       BinaryIStream& data, void*) override {
        data.finish();
        received(std::chrono::duration<double>(std::chrono::steady_clock::now() - started_).count());
    }
    void handleException(const Mercury::NubException&, void*) override { received(-1.0); }
    void handleTimeout(TimerHandle, void*) override {
        auto keep = shared_from_this();
        // Release the currently executing timer before the final callback can
        // remove this handler or re-enter shutdown.
        timer_.cancel();
        auto context = owner_.lock();
        if (!context || !context->active || cancelled_) return;
        if (sample_ < 4) { send(); return; }
        // 0067b970 discards the warm-up sample and averages successful replies.
        double total = 0.0;
        unsigned count = 0;
        for (unsigned i = 1; i != samples_.size(); ++i) {
            if (samples_[i] >= 0.0) { total += samples_[i]; ++count; }
        }
        context->probes.erase(keep);
        context->complete(batch_, host_, count ? static_cast<int>(total / count * 1000.0) : -1);
    }
private:
    void received(double seconds) {
        auto keep = shared_from_this();
        auto context = owner_.lock();
        if (!context || !context->active || cancelled_) return;
        samples_[sample_] = seconds;
        // The original delays 200 ms after both replies and timeouts, including
        // the fifth sample, before publishing the host's result.
        timer_ = context->dispatcher.addOnceOffTimer(200000, this, NULL, "Stock login ping");
    }
    std::weak_ptr<Owner> owner_;
    std::shared_ptr<Batch> batch_;
    std::string host_;
    Mercury::Address address_;
    std::array<double, 5> samples_;
    std::chrono::steady_clock::time_point started_;
    int sample_ = -1;
    bool cancelled_ = false;
    TimerHandle timer_;
};

class ResolveTask final : public BackgroundTask {
public:
    ResolveTask(const std::shared_ptr<Owner>& owner, std::shared_ptr<Batch> batch,
                std::string host)
        : BackgroundTask("Stock login ping DNS"), owner_(owner), batch_(std::move(batch)),
          host_(std::move(host)) {}
    void doBackgroundTask(TaskManager& tasks) override {
        std::string hostname = host_;
        unsigned short port = 20013;
        const auto colon = hostname.find(':');
        if (colon != std::string::npos) {
            std::istringstream(hostname.substr(colon + 1)) >> port;
            hostname.resize(colon);
        }
        uint32 ip = 0;
        if (Endpoint::convertAddress(hostname.c_str(), ip) == 0 && ip != 0)
            address_ = Mercury::Address(ip, htons(port));
        // No Python or owner access is allowed on the DNS thread. An expired
        // weak owner safely contains completion after script teardown.
        tasks.addMainThreadTask(this);
    }
    void doMainThreadTask(TaskManager&) override {
        auto context = owner_.lock();
        if (context && context->active) context->launch(batch_, host_, address_);
    }
private:
    std::weak_ptr<Owner> owner_;
    std::shared_ptr<Batch> batch_;
    std::string host_;
    Mercury::Address address_;
};

void Owner::shutdown() {
    if (!active) return;
    active = false;
    Py_CLEAR(object->callback);
    // Invalidate every handler before cancellation synchronously invokes its
    // exception callback. Keep them alive until RequestManager has released it.
    for (const auto& probe : probes) probe->cancel();
    if (network) {
        for (const auto& probe : probes)
            network->cancelRequestsFor(probe.get(), Mercury::REASON_SHUTTING_DOWN);
    }
    // NetworkInterface::prepareForShutdown also drains its shared main
    // dispatcher. The pinger owns only this interface, whose destructor
    // detaches it and releases its already-cancelled request manager.
    probes.clear();
    network.reset();
}
void Owner::launch(const std::shared_ptr<Batch>& batch, const std::string& host,
                   const Mercury::Address& address) {
    if (address.isNone()) { complete(batch, host, -1); return; }
    if (!network)
        network.reset(new Mercury::NetworkInterface(&dispatcher, Mercury::NETWORK_INTERFACE_EXTERNAL));
    if (!network->isGood()) { complete(batch, host, -1); return; }
    auto probe = std::make_shared<Probe>(shared_from_this(), batch, host, address);
    probes.insert(probe);
    probe->send();
}
void Owner::complete(const std::shared_ptr<Batch>& batch, const std::string& host, int milliseconds) {
    if (!active || !batch->remaining) return;
    batch->results.emplace_back(host, milliseconds);
    if (--batch->remaining == 0) publish(batch);
}
void Owner::publish(const std::shared_ptr<Batch>& batch) {
    if (!active || !object->callback) return;
    // 006e15c0 selects the current callback when the whole batch completes.
    PyObject* callback = object->callback;
    Py_INCREF(callback);
    PyObject* result = PyTuple_New(static_cast<Py_ssize_t>(batch->results.size()));
    if (result) {
        for (std::size_t i = 0; i != batch->results.size(); ++i) {
            const auto& value = batch->results[i];
            PyObject* pair = Py_BuildValue("(si)", value.first.c_str(), value.second);
            if (!pair) { Py_CLEAR(result); break; }
            PyTuple_SET_ITEM(result, static_cast<Py_ssize_t>(i), pair);
        }
    }
    if (result) {
        PyObject* returned = PyObject_CallFunctionObjArgs(callback, result, NULL);
        Py_XDECREF(returned);
        Py_DECREF(result);
    }
    if (PyErr_Occurred()) PyErr_Print();
    Py_DECREF(callback);
}
void deallocate(PythonPinger* object) {
    Py_CLEAR(object->callback);
    Py_TYPE(object)->tp_free(reinterpret_cast<PyObject*>(object));
}
PyObject* setCallback(PythonPinger* object, PyObject* args) {
    PyObject* callback;
    if (!PyArg_ParseTuple(args, "O:setOnPingCallback", &callback)) return NULL;
    Py_INCREF(callback);
    PyObject* previous = object->callback;
    object->callback = callback;
    Py_XDECREF(previous);
    Py_RETURN_NONE;
}
PyObject* clearCallback(PythonPinger* object, PyObject* args) {
    if (!PyArg_ParseTuple(args, ":clearOnPingCallback")) return NULL;
    Py_CLEAR(object->callback);
    Py_RETURN_NONE;
}
PyObject* ping(PythonPinger* object, PyObject* args) {
    PyObject* hosts;
    if (!PyArg_ParseTuple(args, "O:ping", &hosts)) return NULL;
    std::vector<std::string> names;
    if (PyList_Check(hosts)) {
        const Py_ssize_t count = PyList_GET_SIZE(hosts);
        names.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t i = 0; i != count; ++i) {
            PyObject* host = PyList_GET_ITEM(hosts, i);
            if (!PyString_Check(host)) break;
            // The exact wrapper copies a C string, including its NUL boundary.
            names.emplace_back(PyString_AS_STRING(host));
        }
    }
    if (!PyList_Check(hosts) || names.size() != static_cast<std::size_t>(PyList_GET_SIZE(hosts))) {
        PyErr_SetString(PyExc_TypeError, "Pinger::ping - wrong parameter type. Must be list of strings");
        return NULL;
    }
    auto context = owner;
    if (!context || !context->active || context->object != object) {
        PyErr_SetString(PyExc_RuntimeError, "The native pinger has stopped");
        return NULL;
    }
    try {
        auto batch = std::make_shared<Batch>(names.size());
        if (names.empty()) context->publish(batch);
        else for (const auto& host : names)
            context->tasks.addBackgroundTask(new ResolveTask(context, batch, host));
    } catch (const std::exception& error) {
        PyErr_SetString(PyExc_RuntimeError, error.what());
        return NULL;
    }
    Py_RETURN_NONE;
}
PyMethodDef methods[] = {
    {"ping", reinterpret_cast<PyCFunction>(ping), METH_VARARGS, NULL},
    {"setOnPingCallback", reinterpret_cast<PyCFunction>(setCallback), METH_VARARGS, NULL},
    {"clearOnPingCallback", reinterpret_cast<PyCFunction>(clearCallback), METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}
};
PyTypeObject type = { PyObject_HEAD_INIT(NULL) 0 };
} // namespace StockPingerDetail

bool initialiseStockPinger(Mercury::EventDispatcher& dispatcher, TaskManager& tasks) {
    using namespace StockPingerDetail;
    if (owner) return true;
    if (!type.tp_name) {
        type.tp_name = const_cast<char*>("BigWorld.WGPinger");
        type.tp_basicsize = sizeof(PythonPinger);
        type.tp_flags = Py_TPFLAGS_DEFAULT;
        type.tp_methods = methods;
        type.tp_dealloc = reinterpret_cast<destructor>(deallocate);
        if (PyType_Ready(&type) < 0) return false;
    }
    PythonPinger* object = reinterpret_cast<PythonPinger*>(type.tp_alloc(&type, 0));
    if (!object) return false;
    PyObject* module = PyImport_AddModule("BigWorld");
    if (!module) { Py_DECREF(object); return false; }
    owner = std::make_shared<Owner>(dispatcher, tasks, object);
    if (PyModule_AddObject(module, "WGPinger", reinterpret_cast<PyObject*>(object)) != 0) {
        owner.reset(); Py_DECREF(object); return false;
    }
    return true;
}
void finiStockPinger() {
    auto context = std::move(StockPingerDetail::owner);
    if (context) context->shutdown();
}
BW_END_NAMESPACE
