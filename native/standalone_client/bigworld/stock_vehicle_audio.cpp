#include "pch.hpp"
#include "stock_vehicle_audio.hpp"
#include "stock_vehicle_components.hpp"
#include "stock_detailed_engine_state.hpp"
#include "app.hpp"
#include "pyscript/script_math.hpp"
#include "moo/render_context.hpp"

#include <algorithm>
#include <memory>
#include <vector>

BW_BEGIN_NAMESPACE
int StockVehicleAudio_token = 1;
class DetailedEngineState;
namespace {
class DetailedState;
struct PeriodicEntry {
    std::weak_ptr<DetailedState> state;
    uint64 token;
    float remaining = .25f;
    uint32 readyFrame = 0;
};
std::vector<PeriodicEntry> periodic;
std::vector<std::weak_ptr<DetailedState>> allDetailed;
uint64 nextToken = 0;

class DetailedState : public StockComponentState {
public:
    wot::DetailedEngineStateData data;
    PyObjectPtr speed, rotationSpeed, physicsRPM, physicsGear;
    MatrixProviderPtr matrix;
    PyObjectPtr onStateChanged, onEngineStart, onGearUp;
    float updatePeriod = -1.f;
    bool active = false, delayed = false;
    double deadline = 0.;
    uint32 scheduledFrame = 0;
    uint64 token = 0;
    virtual void activate() {
        if (!valid_ || active) return;
        active = true;
        token = ++nextToken;
        periodic.push_back(PeriodicEntry{
            std::static_pointer_cast<DetailedState>(shared_from_this()), token});
    }
    virtual void deactivate() {
        if (!active) return;
        active = false;
        ++token;
        periodic.erase(std::remove_if(periodic.begin(), periodic.end(),
            [this](const PeriodicEntry& entry) {
                const auto value = entry.state.lock();
                return !value || value.get() == this;
            }), periodic.end());
    }
    virtual void destroy() {
        if (!valid_) return;
        this->deactivate();
        valid_ = false;
        delayed = false;
        speed = NULL; rotationSpeed = NULL; physicsRPM = NULL; physicsGear = NULL;
        matrix = NULL;
        onStateChanged = NULL; onEngineStart = NULL; onGearUp = NULL;
    }
    virtual PyObject* wrap(bool owning);
    bool call(PyObjectPtr callback, PyObject* args) {
        if (!args) return false;
        if (!callback || callback.get() == Py_None) {
            Py_DECREF(args);
            return valid_;
        }
        PyObjectPtr arguments(args, PyObjectPtr::STEAL_REFERENCE);
        PyObjectPtr result(PyObject_CallObject(callback.get(), arguments.get()),
            PyObjectPtr::STEAL_REFERENCE);
        if (!result) {
            ERROR_MSG("DetailedEngineState: stock callback failed\n");
            PyErr_Print();
        }
        return valid_;
    }
    bool scalar(PyObjectPtr producer, float& output) {
        if (!valid_) return false;
        if (!producer) {
            PyErr_SetString(PyExc_ValueError, "DetailedEngineState float link is unavailable");
            return false;
        }
        PyObjectPtr result(PyObject_CallObject(producer.get(), NULL), PyObjectPtr::STEAL_REFERENCE);
        return result && !Script::setData(result.get(), output) && valid_;
    }
    bool refresh(float step) {
        wot::DetailedEngineStateInputs input;
        input.speed = [this](float& value) { return this->scalar(speed, value); };
        input.rotation_speed = [this](float& value) { return this->scalar(rotationSpeed, value); };
        if (physicsRPM)
            input.physics_rpm = [this](float& value) { return this->scalar(physicsRPM, value); };
        if (physicsGear) input.physics_gear = [this](std::int32_t& value) {
            PyObjectPtr producer = physicsGear;
            PyObjectPtr result(PyObject_CallObject(producer.get(), NULL), PyObjectPtr::STEAL_REFERENCE);
            int number;
            if (!result || Script::setData(result.get(), number) || !valid_) return false;
            value = number;
            return true;
        };
        input.forward = [this](std::array<float, 3>& value) {
            MatrixProviderPtr provider = matrix;
            if (!provider || !valid_) {
                PyErr_SetString(PyExc_ValueError, "DetailedEngineState matrix link is unavailable");
                return false;
            }
            Matrix pose;
            provider->matrix(pose);
            if (!valid_) return false;
            const Vector3 forward = pose.applyToUnitAxisVector(2);
            value = {{forward.x, forward.y, forward.z}};
            return true;
        };
        if (onGearUp) input.on_gear_up = [this]() { return this->call(onGearUp, PyTuple_New(0)); };
        return wot::refresh_detailed_engine_state(data, input, step);
    }
    bool changeEngineState(int value) {
        const int previous = data.engine_state;
        data.engine_state = value;
        return this->call(onStateChanged, Py_BuildValue("(ii)", previous, value));
    }
    void changeMode(int value) {
        data.starting = value > 0 && data.mode == 0;
        data.mode = value;
    }
};
}

class DetailedEngineState : public StockComponentWrapper {
    Py_Header(DetailedEngineState, StockComponentWrapper)
public:
    DetailedEngineState(const std::shared_ptr<StockComponentState>& value, bool owning) :
        StockComponentWrapper(value, &s_type_, owning) {}
    std::shared_ptr<DetailedState> state() const {
        auto value = std::dynamic_pointer_cast<DetailedState>(this->component());
        if (!value) PyErr_SetString(PyExc_ValueError, "DetailedEngineState has been destroyed");
        return value;
    }
#define DETAIL_RO(NAME, MEMBER) \
    PyObject* pyGet_##NAME() { auto value = this->state(); \
        return value ? Script::getData(value->data.MEMBER) : NULL; } \
    PY_RO_ATTRIBUTE_SET(NAME)
    DETAIL_RO(rpm, rpm)
    DETAIL_RO(gearNum, gear)
    DETAIL_RO(gearUp, gear_up)
    DETAIL_RO(starting, starting)
    DETAIL_RO(physicLoad, physics_load)
    DETAIL_RO(relativeSpeed, relative_speed)
    DETAIL_RO(engineLoad, engine_load)
    DETAIL_RO(roughnessValue, roughness)
    DETAIL_RO(relativeRPM, relative_rpm)
    DETAIL_RO(rotationSpeed, rotation_speed)
    DETAIL_RO(roatationRelSpeed, relative_rotation_speed)
    DETAIL_RO(gear2, gear2)
    DETAIL_RO(gear3, gear3)
    DETAIL_RO(rpmPhysicRel, physics_rpm)
    DETAIL_RO(rpmPhysicAbs, physics_rpm_absolute)
#undef DETAIL_RO
#define DETAIL_LINK(NAME, MEMBER) \
    PyObject* pyGet_##NAME() { auto value = this->state(); if (!value) return NULL; \
        return Script::getData(value->MEMBER); } \
    int pySet_##NAME(PyObject* object) { auto value = this->state(); if (!value) return -1; \
        if (!object || (object != Py_None && !PyCallable_Check(object))) { \
            PyErr_SetString(PyExc_TypeError, #NAME " requires a callable or None"); return -1; } \
        value->MEMBER = object == Py_None ? NULL : object; return 0; }
    DETAIL_LINK(vehicleSpeedLink, speed)
    DETAIL_LINK(rotationSpeedLink, rotationSpeed)
    DETAIL_LINK(physicRPMLink, physicsRPM)
    DETAIL_LINK(physicGearLink, physicsGear)
    DETAIL_LINK(onStateChanged, onStateChanged)
    DETAIL_LINK(onEngineStart, onEngineStart)
    DETAIL_LINK(onGearUpCbk, onGearUp)
#undef DETAIL_LINK
    PyObject* pyGet_vehicleMatrixLink() {
        const auto value = this->state();
        return value ? Script::getData(value->matrix) : NULL;
    }
    int pySet_vehicleMatrixLink(PyObject* object) {
        const auto value = this->state();
        if (!value || !this->attributeValue(object)) return -1;
        MatrixProviderPtr provider;
        if (Script::setData(object, provider)) return -1;
        value->matrix = provider;
        return 0;
    }
    PyObject* pyGet_updatePeriod() {
        const auto value = this->state();
        return value ? Script::getData(value->updatePeriod) : NULL;
    }
    int pySet_updatePeriod(PyObject* object) {
        const auto value = this->state();
        return value && this->attributeValue(object) ? Script::setData(object, value->updatePeriod) : -1;
    }
    PyObject* pyGet_mode() {
        const auto value = this->state();
        return value ? Script::getData(value->data.mode) : NULL;
    }
    int pySet_mode(PyObject* object) {
        const auto value = this->state(); int mode;
        if (!value || !this->attributeValue(object) || Script::setData(object, mode)) return -1;
        value->changeMode(mode);
        return 0;
    }
    PyObject* pyGet_engineState() {
        const auto value = this->state();
        return value ? Script::getData(value->data.engine_state) : NULL;
    }
    int pySet_engineState(PyObject* object) {
        const auto value = this->state(); int state;
        if (!value || !this->attributeValue(object) || Script::setData(object, state)) return -1;
        value->changeEngineState(state);
        return PyErr_Occurred() ? -1 : 0;
    }
    PY_METHOD_DECLARE(py_refresh)
    PY_METHOD_DECLARE(py_setVehicleParams)
    PY_METHOD_DECLARE(py_startEngineWithDelay)
    PY_METHOD_DECLARE(py_notifyBattleStarted)
    PY_FACTORY_DECLARE()
private:
    bool attributeValue(PyObject* object) {
        if (object) return true;
        PyErr_SetString(PyExc_AttributeError, "DetailedEngineState attributes cannot be deleted");
        return false;
    }
};

PyObject* DetailedState::wrap(bool owning) {
    return new DetailedEngineState(shared_from_this(), owning);
}
PyObject* DetailedEngineState::pyNew(PyObject* args) {
    if (!PyArg_ParseTuple(args, ":DetailedEngineState")) return NULL;
    auto value = std::make_shared<DetailedState>();
    allDetailed.push_back(value);
    return new DetailedEngineState(value, true);
}
PyObject* DetailedEngineState::py_refresh(PyObject* args) {
    float step;
    if (!PyArg_ParseTuple(args, "f:refresh", &step)) return NULL;
    const auto value = this->state();
    if (!value) return NULL;
    if (!value->refresh(step) && PyErr_Occurred()) return NULL;
    Py_RETURN_NONE;
}
PyObject* DetailedEngineState::py_setVehicleParams(PyObject* args) {
    float forward, backward, rotation, climb, rpmMin, rpmMax;
    PyObject* player;
    if (!PyArg_ParseTuple(args, "ffffffO:setVehicleParams", &forward, &backward,
        &rotation, &climb, &rpmMin, &rpmMax, &player)) return NULL;
    bool isPlayer;
    if (Script::setData(player, isPlayer)) return NULL;
    const auto value = this->state();
    if (!value) return NULL;
    auto& data = value->data;
    data.forward_limit = forward; data.backward_limit = backward;
    data.rotation_limit = rotation; data.max_climb_angle = climb;
    data.rpm_min = rpmMin; data.rpm_max = rpmMax;
    data.total_speed_limit = forward + backward;
    data.player = isPlayer;
    Py_RETURN_NONE;
}
PyObject* DetailedEngineState::py_startEngineWithDelay(PyObject* args) {
    float delay;
    if (!PyArg_ParseTuple(args, "f:startEngineWithDelay", &delay)) return NULL;
    const auto value = this->state();
    if (!value) return NULL;
    if (!value->delayed && !(delay < 0.f)) {
        value->deadline = App::instance().getGameTimeFrameStart() + double(delay);
        value->scheduledFrame = Moo::rc().frameTimestamp();
        value->delayed = true;
    }
    Py_RETURN_NONE;
}
PyObject* DetailedEngineState::py_notifyBattleStarted(PyObject* args) {
    if (!PyArg_ParseTuple(args, ":notifyBattleStarted")) return NULL;
    const auto value = this->state();
    if (!value) return NULL;
    if (!value->delayed && value->data.mode == 0)
        value->call(value->onEngineStart, PyTuple_New(0));
    if (PyErr_Occurred()) return NULL;
    if (!value->valid()) Py_RETURN_NONE;
    value->data.starting = false;
    Py_RETURN_NONE;
}

PY_TYPEOBJECT(DetailedEngineState)
PY_BEGIN_METHODS(DetailedEngineState)
    PY_METHOD(refresh)
    PY_METHOD(setVehicleParams)
    PY_METHOD(startEngineWithDelay)
    PY_METHOD(notifyBattleStarted)
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES(DetailedEngineState)
    PY_ATTRIBUTE(rpm)
    PY_ATTRIBUTE(gearNum)
    PY_ATTRIBUTE(gearUp)
    PY_ATTRIBUTE(mode)
    PY_ATTRIBUTE(starting)
    PY_ATTRIBUTE(physicLoad)
    PY_ATTRIBUTE(relativeSpeed)
    PY_ATTRIBUTE(engineLoad)
    PY_ATTRIBUTE(roughnessValue)
    PY_ATTRIBUTE(relativeRPM)
    PY_ATTRIBUTE(engineState)
    PY_ATTRIBUTE(rotationSpeed)
    PY_ATTRIBUTE(roatationRelSpeed)
    PY_ATTRIBUTE(gear2)
    PY_ATTRIBUTE(gear3)
    PY_ATTRIBUTE(rpmPhysicRel)
    PY_ATTRIBUTE(rpmPhysicAbs)
    PY_ATTRIBUTE(updatePeriod)
    PY_ATTRIBUTE(physicRPMLink)
    PY_ATTRIBUTE(physicGearLink)
    PY_ATTRIBUTE(vehicleSpeedLink)
    PY_ATTRIBUTE(rotationSpeedLink)
    PY_ATTRIBUTE(vehicleMatrixLink)
    PY_ATTRIBUTE(onStateChanged)
    PY_ATTRIBUTE(onEngineStart)
    PY_ATTRIBUTE(onGearUpCbk)
PY_END_ATTRIBUTES()
PY_FACTORY(DetailedEngineState, Vehicular)

void tickStockDetailedEngineStates(float frameTime) {
    const uint32 frame = Moo::rc().frameTimestamp();
    for (auto& entry : periodic) {
        const float previous = entry.remaining;
        entry.remaining -= frameTime;
        if (entry.remaining <= 0.f && previous > 0.f) entry.readyFrame = frame;
    }
    const size_t count = std::min<size_t>(3, periodic.size()); // 0x005af650
    const auto better = [](const PeriodicEntry& left, const PeriodicEntry& right) {
        if ((left.remaining <= 0.f) != (right.remaining <= 0.f)) return left.remaining <= 0.f;
        return left.readyFrame < right.readyFrame;
    };
    std::partial_sort(periodic.begin(), periodic.begin() + count, periodic.end(), better);
    const std::vector<PeriodicEntry> work(periodic.begin(), periodic.begin() + count);
    for (const auto& entry : work) {
        if (!(entry.remaining <= 0.f)) break;
        const auto value = entry.state.lock();
        if (!value || !value->valid() || !value->active || value->token != entry.token) continue;
        if (!value->refresh(.25f) && PyErr_Occurred()) PyErr_Print();
        if (!value->valid() || !value->active || value->token != entry.token) continue;
        for (auto& current : periodic) if (current.token == entry.token) {
            current.remaining = value->updatePeriod == -1.f ? .25f : value->updatePeriod;
            break;
        }
    }
}
void tickStockVehicleAudioTasks() {
    // The original TaskHandle survives component deactivation and is cancelled
    // on destruction. Its callback clears the pending handle before re-entry.
    const double now = App::instance().getGameTimeFrameStart();
    const auto tasks = allDetailed;
    for (const auto& weak : tasks) {
        const auto value = weak.lock();
        if (!value || !value->valid() || !value->delayed || now < value->deadline ||
            value->scheduledFrame == Moo::rc().frameTimestamp()) continue;
        value->delayed = false;
        value->data.starting = true;
        if (value->data.mode < 1) value->data.mode = 1;
        if (!value->call(value->onEngineStart, PyTuple_New(0)) && PyErr_Occurred()) PyErr_Print();
    }
    allDetailed.erase(std::remove_if(allDetailed.begin(), allDetailed.end(),
        [](const std::weak_ptr<DetailedState>& value) { return value.expired(); }), allDetailed.end());
}
void finiStockVehicleAudio() {
    const auto states = allDetailed;
    for (const auto& weak : states) if (const auto value = weak.lock()) value->destroy();
    periodic.clear();
    allDetailed.clear();
}
BW_END_NAMESPACE
