#include "pch.hpp"
#include "stock_vehicle_audio.hpp"
#include "stock_vehicle_components.hpp"
#include "stock_detailed_engine_state.hpp"
#include "stock_audio.hpp"
#include "stock_audio_source.hpp"
#include "vehicle_appearance.hpp"
#include <map>
#include <string>
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
static void finiStockVehicleAuditions();
static void finiStockFrictionAuditions();
void finiStockVehicleAudio() {
    finiStockFrictionAuditions();
    finiStockVehicleAuditions();
    const auto states = allDetailed;
    for (const auto& weak : states) if (const auto value = weak.lock()) value->destroy();
    periodic.clear();
    allDetailed.clear();
}

namespace {
class VehicleAuditionState;
struct AuditionEntry {
    std::weak_ptr<VehicleAuditionState> state;
    uint64 token;
    float remaining = .25f;
    uint32 readyFrame = 0;
};
std::vector<AuditionEntry> auditions;
std::vector<std::weak_ptr<VehicleAuditionState>> allAuditions;

class VehicleAuditionState : public StockComponentState {
public:
    int entityID = 0;
    bool player = false, focused = false, active = false, started = false;
    bool siegeSettled = false, shifted = false;
    float power = 0.f, weight = 0.f, rotationLimit = 0.f, updatePeriod = -1.f;
    float lastSpeed = 0.f, scrollSpeed = 0.f;
    double lastTime = 0.;
    uint64 token = 0;
    StockAudioVoice engine, tracks;
    StockAudioSource gun;
    std::weak_ptr<DetailedState> detailed;
    PyObjectPtr materials, speed[2], scroll[4], underwater, flying, inWater;
    BW::string siegeOn, siegeOff, surface;

    void both(const char* name, float value) {
        setStockAudioSourceRTPC(stockAudioVoiceSource(engine), name, value);
        setStockAudioSourceRTPC(stockAudioVoiceSource(tracks), name, value);
    }
    void start() {
        if (!stockAudioReady() || started) return;
        playStockAudioVoice(engine);
        playStockAudioVoice(tracks);
        started = true;
    }
    virtual void activate() {
        if (!valid_ || active) return;
        if (!stockAudioReady()) { this->deactivate(); return; }
        const auto detail = detailed.lock();
        if (!detail || !detail->valid()) {
            PyErr_SetString(PyExc_ValueError, "VehicleAudition requires its original DetailedEngineState");
            return;
        }
        active = true;
        token = ++nextToken;
        auditions.push_back(AuditionEntry{
            std::static_pointer_cast<VehicleAuditionState>(shared_from_this()), token});
        if (player) setStockAudioGlobalRTPC("RTPC_ext_engine_power", power);
        this->both("RTPC_ext_vehicle_weight", weight * .001f);
        this->both("RTPC_ext_engine_state", 0.f);
        this->both("RTPC_ext_physic_rpm_rel", 0.f);
        this->both("RTPC_ext_physic_rpm_abs", 0.f);
        setStockAudioSourceRTPC(stockAudioVoiceSource(engine), "RTPC_ext_turbine", 0.f);
        if (detail->data.mode) this->start();
    }
    virtual void deactivate() {
        active = false;
        ++token;
        auditions.erase(std::remove_if(auditions.begin(), auditions.end(),
            [this](const AuditionEntry& entry) {
                const auto value = entry.state.lock();
                return !value || value.get() == this;
            }), auditions.end());
        stopStockAudioVoice(engine);
        stopStockAudioVoice(tracks);
        stopStockAudioSource(gun);
        started = false;
    }
    virtual void destroy() {
        if (!valid_) return;
        this->deactivate();
        valid_ = false;
        detailed.reset(); materials = NULL;
        for (auto& value : speed) value = NULL;
        for (auto& value : scroll) value = NULL;
        underwater = NULL; flying = NULL; inWater = NULL;
        engine.reset(); tracks.reset(); gun.reset();
    }
    virtual PyObject* wrap(bool owning);
    bool current(uint64 epoch) const { return valid_ && active && token == epoch; }
    PyObject* sample(PyObjectPtr callback, uint64 epoch) {
        if (!this->current(epoch)) return NULL;
        if (!callback) {
            PyErr_SetString(PyExc_ValueError, "VehicleAudition data link is unavailable");
            return NULL;
        }
        PyObject* result = PyObject_CallObject(callback.get(), NULL);
        if (!this->current(epoch)) { Py_XDECREF(result); return NULL; }
        return result;
    }
    bool scalar(PyObjectPtr callback, float& value, uint64 epoch) {
        PyObjectPtr result(this->sample(callback, epoch), PyObjectPtr::STEAL_REFERENCE);
        return result && !Script::setData(result.get(), value);
    }
    bool boolean(PyObjectPtr callback, bool& value, uint64 epoch) {
        PyObjectPtr result(this->sample(callback, epoch), PyObjectPtr::STEAL_REFERENCE);
        return result && !Script::setData(result.get(), value);
    }
    bool updateSurface(uint64 epoch) {
        PyObjectPtr result(this->sample(materials, epoch), PyObjectPtr::STEAL_REFERENCE);
        BW::vector<BW::string> names;
        if (!result || Script::setData(result.get(), names)) return false;
        bool water;
        if (!this->boolean(inWater, water, epoch)) return false;
        // #1513's audio material table is separate from physical terrain support.
        // Its strict maximum scan uses std::map order, including zero-count ties.
        std::map<BW::string, std::pair<size_t, float>> counts;
        counts["ground"] = std::make_pair(0, .1f);
        counts["stone"] = std::make_pair(0, 1.f);
        counts["wood"] = std::make_pair(0, .5f);
        counts["snow"] = std::make_pair(0, .3f);
        counts["sand"] = std::make_pair(0, 0.f);
        counts["water"] = std::make_pair(0, .2f);
        if (water) counts["water"].first = names.size();
        else for (const auto& name : names) {
            const auto found = counts.find(name);
            if (found != counts.end()) ++found->second.first;
        }
        float hardness = 0.f;
        size_t maximum = 0;
        BW::string selected = "ground";
        for (const auto& item : counts) {
            hardness += float(item.second.first) * item.second.second;
            if (item.second.first > maximum) {
                selected = item.first;
                maximum = item.second.first;
            }
        }
        if (!names.empty()) hardness /= float(names.size());
        setStockAudioSourceRTPC(stockAudioVoiceSource(tracks), "RTPC_ext_hardness", hardness);
        if (selected != surface) {
            setStockAudioSourceSwitch(stockAudioVoiceSource(tracks),
                "SWITCH_ext_surfaceType", "SWITCH_ext_surfaceType_" + selected);
            surface = selected;
        }
        return true;
    }
    bool update() {
        const auto detail = detailed.lock();
        if (!detail || !detail->valid()) {
            PyErr_SetString(PyExc_ValueError, "VehicleAudition DetailedEngineState is unavailable");
            return false;
        }
        const uint64 epoch = token;
        // Keep the original state owner alive across Python producer re-entry.
        const auto& d = detail->data;
        const float rpm = wot::detailed_engine_clamp(d.rpm, 0.f, 100.f);
        this->both("RTPC_ext_rpm", rpm);
        this->both("RTPC_ext_engine_load", float(d.engine_load));
        if (!player) {
            setStockAudioSourceRTPC(stockAudioVoiceSource(engine), "RTPC_ext_turbine_npc", rpm);
            setStockAudioSourceVolume(stockAudioVoiceSource(engine), d.engine_load ? 1.f : 0.f);
        }
        if (focused) setStockAudioGlobalRTPC("RTPC_ext_engine_load_global", float(d.engine_load));
        bool submerged;
        if (!this->boolean(underwater, submerged, epoch)) return false;
        this->both("RTPC_ext_submersion", submerged ? 1.f : 0.f);
        if (focused) setStockAudioGlobalState("STATE_underwater",
            submerged ? "STATE_underwater_on" : "STATE_underwater_off");
        this->both("RTPC_ext_speed_abs", wot::detailed_engine_clamp(d.speed, -10.f, 30.f));
        this->both("RTPC_ext_speed_rel", wot::detailed_engine_clamp(d.relative_speed, -1.f, 1.f));
        this->both("RTPC_ext_speed_rel2", d.relative_speed);
        this->both("RTPC_ext_rot_speed_abs", wot::detailed_engine_clamp(d.rotation_speed, -1.f, 1.f));
        this->both("RTPC_ext_rot_speed_rel", d.relative_rotation_speed);
        this->both("RTPC_ext_move", std::fabs(d.speed) > 1.f ? 100.f : 0.f);
        this->both("RTPC_ext_physic_load", d.physics_load);
        if (focused) {
            setStockAudioGlobalRTPC("RTPC_ext_physic_load_global", d.physics_load);
            setStockAudioGlobalRTPC("RTPC_ext_speed_rel_global", wot::detailed_engine_clamp(d.relative_speed, -1.f, 1.f));
            setStockAudioGlobalRTPC("RTPC_ext_speed_abs_global", d.speed);
            if (!player) {
                this->both("RTPC_ext_gear_2", float(d.gear2));
                this->both("RTPC_ext_gear_3", float(d.gear3));
                this->both("RTPC_ext_gear_num", wot::detailed_engine_clamp(float(d.gear), 0.f, 4.f));
            } else {
                this->both("RTPC_ext_physic_rpm_rel", d.physics_rpm);
                setStockAudioSourceRTPC(stockAudioVoiceSource(engine), "RTPC_ext_turbine",
                    d.engine_state == 3 ? 0.f : d.physics_rpm * 100.f);
                this->both("RTPC_ext_physic_rpm_abs", d.physics_rpm_absolute);
                this->both("RTPC_ext_physic_gear", float(d.gear));
                this->both("RTPC_ext_engine_state", uint32(d.gear) - 1u < 126u ? 1.f : 0.f);
                for (int gear = 1; gear <= 7; ++gear) if (gear != d.gear) {
                    char name[48];
                    bw_snprintf(name, sizeof(name), "RTPC_ext_physic_gear_%d", gear);
                    this->both(name, 0.f);
                }
                if (shifted) {
                    // The native table has exactly seven slots. Reject a missing
                    // producer value instead of indexing outside that table.
                    if (d.gear < 1 || d.gear > 7) {
                        PyErr_SetString(PyExc_ValueError, "VehicleAudition shifted gear has no native RTPC slot");
                        return false;
                    }
                    char name[48];
                    bw_snprintf(name, sizeof(name), "RTPC_ext_physic_gear_%d", d.gear);
                    this->both(name, 100.f);
                    shifted = false;
                }
            }
        }
        const double now = App::instance().getGameTimeFrameStart();
        const double dt = now - lastTime;
        const float acceleration = lastTime > 0. && dt > 0. ?
            wot::detailed_engine_clamp((d.speed - lastSpeed) / float(dt), -1.5f, 1.5f) : 0.f;
        lastTime = now; lastSpeed = d.speed;
        this->both("RTPC_ext_acc_abs", acceleration);
        bool airborne;
        if (!this->boolean(flying, airborne, epoch)) return false;
        setStockAudioSourceRTPC(stockAudioVoiceSource(tracks), "RTPC_ext_flying", airborne ? 1.f : 0.f);
        if (!focused) return true;
        if (!this->updateSurface(epoch)) return false;
        float slideA, slideB, speedA, speedB;
        if (!this->scalar(scroll[2], slideA, epoch) || !this->scalar(scroll[3], slideB, epoch)) return false;
        this->both("RTPC_ext_slide_friction", wot::detailed_engine_clamp(std::fmax(slideA, slideB) * .2f, 0.f, 1.f));
        if (!this->scalar(speed[0], speedA, epoch) || !this->scalar(speed[1], speedB, epoch)) return false;
        setStockAudioSourceRTPC(stockAudioVoiceSource(tracks), "RTPC_ext_friction",
            std::fmax(std::fmin(std::fabs(speedA) * .8f, .5f), std::fmin(std::fabs(speedB) * .4f, 1.f)));
        setStockAudioGlobalRTPC("RTPC_ext_roughness_global", std::fabs(d.roughness));
        this->both("RTPC_ext_roughness_abs", std::fabs(d.roughness));
        this->both("RTPC_ext_roughness2", d.roughness);
        this->both("RTPC_ext_roughness_eng", d.roughness);
        setStockAudioSourceRTPC(stockAudioVoiceSource(tracks), "RTPC_ext_treads_sum_affect",
            (std::fabs(d.roughness) + std::fabs(d.rotation_speed / rotationLimit)) * .33f +
            (wot::detailed_engine_clamp(d.physics_load, .5f, 1.f) - .5f) * .66f);
        if (player) {
            // 0x793660 deliberately samples the second scroll producer first.
            float first, second;
            if (!this->scalar(scroll[1], second, epoch) || !this->scalar(scroll[0], first, epoch)) return false;
            scrollSpeed = (std::fmax(std::fabs(first), std::fabs(second)) - scrollSpeed) * updatePeriod * 5.f + scrollSpeed;
            scrollSpeed = std::fmax(0.f, scrollSpeed);
            this->both("RTPC_ext_speed_scroll", scrollSpeed);
        }
        return true;
    }
    void camera(const BW::string& name, int attachedID) {
        if (!stockAudioReady() || name != "postmortem") return;
        const bool next = entityID == attachedID;
        if (focused != next) {
            const BW::string value = next ? "SWITCH_ext_postMortem_pc" : "SWITCH_ext_postMortem_npc";
            setStockAudioSourceSwitch(stockAudioVoiceSource(engine), "SWITCH_ext_postMortem", value);
            setStockAudioSourceSwitch(stockAudioVoiceSource(tracks), "SWITCH_ext_postMortem", value);
        }
        focused = next;
    }
    bool transition(int previous, int next) {
        if (!stockAudioReady()) return true;
        const auto detail = detailed.lock();
        if (!detail || !detail->valid()) {
            PyErr_SetString(PyExc_ValueError, "VehicleAudition DetailedEngineState is unavailable");
            return false;
        }
        if (!detail->data.mode && next == 1) previous = 3;
        if (previous < 0 || previous > 3 || next < 0 || next > 3) {
            PyErr_SetString(PyExc_ValueError, "VehicleAudition engine state is outside the native transition table");
            return false;
        }
        const StockAudioSource source = stockAudioVoiceSource(engine);
        const int index = previous * 4 + next;
        if (index == 2) playStockAudioSource(source, "eng_broken");
        else if (index == 3 || index == 7 || index == 11) playStockAudioSource(source, "eng_stopping");
        else if (index == 4 || index == 8) playStockAudioSource(source, "eng_repairing_broken");
        else if (index == 12) {
            playStockAudioSource(source, "eng_restoring");
            playStockAudioSource(source, "eng_repairing_broken");
        } else if (index == 13 || index == 14) {
            playStockAudioSource(source, "eng_restoring");
            playStockAudioSource(source, "eng_still_broken");
        }
        return true;
    }
};
}

class VehicleAudition : public StockComponentWrapper {
    Py_Header(VehicleAudition, StockComponentWrapper)
public:
    VehicleAudition(const std::shared_ptr<StockComponentState>& value, bool owning) :
        StockComponentWrapper(value, &s_type_, owning) {}
    std::shared_ptr<VehicleAuditionState> state() const {
        const auto value = std::dynamic_pointer_cast<VehicleAuditionState>(this->component());
        if (!value) PyErr_SetString(PyExc_ValueError, "VehicleAudition has been destroyed");
        return value;
    }
    PY_FACTORY_DECLARE()
    PY_METHOD_DECLARE(py_attachToModel)
    PY_METHOD_DECLARE(py_getSoundObject)
    PY_METHOD_DECLARE(py_onCameraChanged)
    PY_METHOD_DECLARE(py_onEngineStart)
    PY_METHOD_DECLARE(py_onEngineStateChanged)
    PY_METHOD_DECLARE(py_onEngineGearUp)
    PY_METHOD_DECLARE(py_onSiegeStateChanged)
    PY_METHOD_DECLARE(py_setDetailedEngineState)
    PY_METHOD_DECLARE(py_setEffectMaterialsInfo)
    PY_METHOD_DECLARE(py_setSpeedInfo)
    PY_METHOD_DECLARE(py_setTrackScrollInfo)
    PY_METHOD_DECLARE(py_setIsUnderwaterInfo)
    PY_METHOD_DECLARE(py_setIsFlyingInfo)
    PY_METHOD_DECLARE(py_setIsInWaterInfo)
    PY_METHOD_DECLARE(py_setWeaponEnergy)
    PY_METHOD_DECLARE(py_setSiegeSoundEvents)
    PY_METHOD_DECLARE(py_setUpdatePeriod)
    bool links(PyObject* args, PyObjectPtr* destinations, size_t count) {
        if (!PyTuple_Check(args) || PyTuple_GET_SIZE(args) != Py_ssize_t(count)) {
            PyErr_SetString(PyExc_TypeError, "VehicleAudition data link argument count is incorrect");
            return false;
        }
        for (size_t i = 0; i < count; ++i)
            if (PyTuple_GET_ITEM(args, i) != Py_None && !PyCallable_Check(PyTuple_GET_ITEM(args, i))) {
                PyErr_SetString(PyExc_TypeError, "VehicleAudition data links require callable producers or None");
                return false;
            }
        for (size_t i = 0; i < count; ++i) {
            PyObject* object = PyTuple_GET_ITEM(args, i);
            destinations[i] = object == Py_None ? NULL : object;
        }
        return true;
    }
};

PyObject* VehicleAuditionState::wrap(bool owning) { return new VehicleAudition(shared_from_this(), owning); }
PyObject* VehicleAudition::pyNew(PyObject* args) {
    int id;
    PyObject* playerObject; PyObject* dataObject; PyObject* engineObject; PyObject* tracksObject;
    if (!PyArg_ParseTuple(args, "iOOOO:VehicleAudition", &id, &playerObject, &dataObject, &engineObject, &tracksObject)) return NULL;
    bool player;
    BW::string engineName, tracksName;
    Vector3 data;
    if (Script::setData(playerObject, player) || Script::setData(dataObject, data) ||
        Script::setData(engineObject, engineName) || Script::setData(tracksObject, tracksName)) return NULL;
    auto value = std::make_shared<VehicleAuditionState>();
    value->entityID = id; value->player = player; value->focused = player;
    value->power = data.x; value->weight = data.y; value->rotationLimit = data.z;
    if (value->rotationLimit > -.0004f && value->rotationLimit < .0004f) value->rotationLimit = .001f;
    char name[64];
    bw_snprintf(name, sizeof(name), "engine%d", id);
    value->engine = createStockAudioVoice(engineName, name);
    bw_snprintf(name, sizeof(name), "tracks%d", id);
    value->tracks = createStockAudioVoice(tracksName, name);
    bw_snprintf(name, sizeof(name), "gun%d", id);
    value->gun = createStockAudioSource(name, true);
    allAuditions.push_back(value);
    return new VehicleAudition(value, true);
}
PyObject* VehicleAudition::py_attachToModel(PyObject* args) {
    PyObject* compound;
    if (!PyArg_ParseTuple(args, "O:attachToModel", &compound)) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    Matrix local, parent, world;
    if (!stockVehicleCompoundNode(compound, "hull", local, parent, world)) {
        PyErr_SetString(PyExc_TypeError, "VehicleAudition requires the loaded PyCompoundModel hull node");
        return NULL;
    }
    if (stockAudioReady()) {
        PyObjectPtr hull(PyObject_CallMethod(compound, const_cast<char*>("node"), const_cast<char*>("s"), "hull"), PyObjectPtr::STEAL_REFERENCE);
        PyObjectPtr gun(PyObject_CallMethod(compound, const_cast<char*>("node"), const_cast<char*>("s"), "gun"), PyObjectPtr::STEAL_REFERENCE);
        MatrixProviderPtr hullMatrix, gunMatrix;
        if (!hull || !gun || Script::setData(hull.get(), hullMatrix) || Script::setData(gun.get(), gunMatrix)) return NULL;
        setStockAudioSourceMatrix(stockAudioVoiceSource(value->engine), hullMatrix);
        setStockAudioSourceMatrix(stockAudioVoiceSource(value->tracks), hullMatrix);
        setStockAudioSourceMatrix(value->gun, gunMatrix);
    }
    Py_RETURN_NONE;
}
PyObject* VehicleAudition::py_getSoundObject(PyObject* args) {
    int kind;
    if (!PyArg_ParseTuple(args, "i:getSoundObject", &kind)) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    return wrapStockAudioSource(kind == 0 ? stockAudioVoiceSource(value->engine) :
        kind == 1 ? stockAudioVoiceSource(value->tracks) : kind == 2 ? value->gun : StockAudioSource());
}
PyObject* VehicleAudition::py_onCameraChanged(PyObject* args) {
    PyObject* object; int id;
    if (!PyArg_ParseTuple(args, "Oi:onCameraChanged", &object, &id)) return NULL;
    BW::string name; if (Script::setData(object, name)) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    value->camera(name, id); Py_RETURN_NONE;
}
PyObject* VehicleAudition::py_onEngineStart(PyObject* args) {
    if (!PyArg_ParseTuple(args, ":onEngineStart")) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    value->start(); Py_RETURN_NONE;
}
PyObject* VehicleAudition::py_onEngineGearUp(PyObject* args) {
    if (!PyArg_ParseTuple(args, ":onEngineGearUp")) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    value->shifted = true; Py_RETURN_NONE;
}
PyObject* VehicleAudition::py_onEngineStateChanged(PyObject* args) {
    int previous, next;
    if (!PyArg_ParseTuple(args, "ii:onEngineStateChanged", &previous, &next)) return NULL;
    const auto value = this->state(); if (!value || !value->transition(previous, next)) return NULL;
    Py_RETURN_NONE;
}
PyObject* VehicleAudition::py_onSiegeStateChanged(PyObject* args) {
    int state;
    if (!PyArg_ParseTuple(args, "i:onSiegeStateChanged", &state)) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    if (stockAudioReady()) {
        const auto source = stockAudioVoiceSource(value->engine);
        if (state == 1 || state == 3) setStockAudioSourceRTPC(source, "RTPC_ext_siege_mode", state == 1 ? 100.f : 0.f);
        if (value->player) playStockAudioSource(source, state == 2 ? value->siegeOn : value->siegeOff);
        value->siegeSettled = state == 1 || state == 3;
    }
    Py_RETURN_NONE;
}
PyObject* VehicleAudition::py_setDetailedEngineState(PyObject* args) {
    PyObject* object;
    if (!PyArg_ParseTuple(args, "O:setDetailedEngineState", &object)) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    if (object == Py_None) { value->detailed.reset(); Py_RETURN_NONE; }
    if (!DetailedEngineState::Check(object)) { value->detailed.reset(); Py_RETURN_NONE; }
    const auto detail = static_cast<DetailedEngineState*>(object)->state();
    if (!detail) return NULL;
    value->detailed = detail; Py_RETURN_NONE;
}
#define AUDITION_LINK(NAME, MEMBER, COUNT) \
PyObject* VehicleAudition::py_##NAME(PyObject* args) { \
    const auto value = this->state(); if (!value) return NULL; \
    if (!this->links(args, MEMBER, COUNT)) return NULL; Py_RETURN_NONE; }
AUDITION_LINK(setEffectMaterialsInfo, &value->materials, 1)
AUDITION_LINK(setSpeedInfo, value->speed, 2)
AUDITION_LINK(setTrackScrollInfo, value->scroll, 4)
AUDITION_LINK(setIsUnderwaterInfo, &value->underwater, 1)
AUDITION_LINK(setIsFlyingInfo, &value->flying, 1)
AUDITION_LINK(setIsInWaterInfo, &value->inWater, 1)
#undef AUDITION_LINK
PyObject* VehicleAudition::py_setWeaponEnergy(PyObject* args) {
    float energy;
    if (!PyArg_ParseTuple(args, "f:setWeaponEnergy", &energy)) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    if (stockAudioReady()) setStockAudioSourceRTPC(value->gun, "RTPC_ext_weapon_energy", energy);
    Py_RETURN_NONE;
}
PyObject* VehicleAudition::py_setSiegeSoundEvents(PyObject* args) {
    PyObject* on; PyObject* off;
    if (!PyArg_ParseTuple(args, "OO:setSiegeSoundEvents", &on, &off)) return NULL;
    BW::string onName, offName;
    if (Script::setData(on, onName) || Script::setData(off, offName)) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    value->siegeOn = onName; value->siegeOff = offName; Py_RETURN_NONE;
}
PyObject* VehicleAudition::py_setUpdatePeriod(PyObject* args) {
    float period;
    if (!PyArg_ParseTuple(args, "f:setUpdatePeriod", &period)) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    value->updatePeriod = period; Py_RETURN_NONE;
}
PY_TYPEOBJECT(VehicleAudition)
PY_BEGIN_METHODS(VehicleAudition)
    PY_METHOD(attachToModel)
    PY_METHOD(getSoundObject)
    PY_METHOD(onCameraChanged)
    PY_METHOD(onEngineStart)
    PY_METHOD(onEngineStateChanged)
    PY_METHOD(onEngineGearUp)
    PY_METHOD(onSiegeStateChanged)
    PY_METHOD(setDetailedEngineState)
    PY_METHOD(setEffectMaterialsInfo)
    PY_METHOD(setSpeedInfo)
    PY_METHOD(setTrackScrollInfo)
    PY_METHOD(setIsUnderwaterInfo)
    PY_METHOD(setIsFlyingInfo)
    PY_METHOD(setIsInWaterInfo)
    PY_METHOD(setWeaponEnergy)
    PY_METHOD(setSiegeSoundEvents)
    PY_METHOD(setUpdatePeriod)
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES(VehicleAudition)
PY_END_ATTRIBUTES()
PY_FACTORY(VehicleAudition, Vehicular)

void tickStockVehicleAuditions(float frameTime) {
    const uint32 frame = Moo::rc().frameTimestamp();
    for (auto& entry : auditions) {
        const float previous = entry.remaining;
        entry.remaining -= frameTime;
        if (entry.remaining <= 0.f && previous > 0.f) entry.readyFrame = frame;
    }
    const size_t count = std::min<size_t>(3, auditions.size()); // 0x005af430
    const auto better = [](const AuditionEntry& a, const AuditionEntry& b) {
        if ((a.remaining <= 0.f) != (b.remaining <= 0.f)) return a.remaining <= 0.f;
        return a.readyFrame < b.readyFrame;
    };
    std::partial_sort(auditions.begin(), auditions.begin() + count, auditions.end(), better);
    const std::vector<AuditionEntry> work(auditions.begin(), auditions.begin() + count);
    for (const auto& entry : work) {
        if (!(entry.remaining <= 0.f)) break;
        const auto value = entry.state.lock();
        if (!value || !value->current(entry.token)) continue;
        if (!value->update() && PyErr_Occurred()) PyErr_Print();
        if (!value->current(entry.token)) continue;
        for (auto& current : auditions) if (current.token == entry.token) {
            current.remaining = value->updatePeriod == -1.f ? .25f : value->updatePeriod;
            break;
        }
    }
}

static void finiStockVehicleAuditions() {
    const auto states = allAuditions;
    for (const auto& weak : states) if (const auto value = weak.lock()) value->destroy();
    auditions.clear(); allAuditions.clear();
}

namespace {
class FrictionState;
std::vector<std::weak_ptr<FrictionState>> allFriction;
class FrictionState : public StockComponentState {
public:
    BW::string event;
    MatrixProviderPtr matrix;
    std::map<uint, StockAudioVoice> contacts;
    uint64 revision = 0;
    virtual void activate() {} // #1513 vtable slot 0 is the empty 0x00579c00.
    virtual void deactivate() {
        ++revision;
        for (const auto& contact : contacts) stopStockAudioVoice(contact.second);
        contacts.clear();
        matrix = NULL;
    }
    virtual void destroy() {
        if (!valid_) return;
        valid_ = false;
        this->deactivate();
    }
    virtual PyObject* wrap(bool owning);
    bool process(uint id, const Vector3& position, int state) {
        MatrixProviderPtr provider = matrix;
        if (!provider) return true;
        const uint64 epoch = revision;
        const StockAudioVoice old = contacts[id];
        if (state == 3) {
            stopStockAudioVoice(old);
            contacts[id].reset();
        } else if (state == 1 || state == 2) {
            // The original start guard tests handle allocation, not isPlaying.
            // A naturally ended event retains its contact handle until state 3.
            if (state == 1 && stockAudioVoiceValid(old)) return true;
            Matrix pose;
            provider->matrix(pose);
            if (!valid_ || revision != epoch) return false;
            const Vector3 offset = position - pose.applyToOrigin();
            if (state == 1) {
                StockAudioVoice voice = createStockAudioAttachedVoice(event, provider, offset);
                // Source creation can synchronously sample a Python provider.
                // Do not retain a contact created by an already retired owner.
                if (!valid_ || revision != epoch) {
                    stopStockAudioVoice(voice);
                    return false;
                }
                if (!voice) return false;
                contacts[id] = voice;
                playStockAudioVoice(contacts[id]);
            } else if (stockAudioVoiceValid(old)) {
                setStockAudioSourceOffset(stockAudioVoiceSource(old), offset);
            }
        }
        return true;
    }
};
}
class FrictionAudition : public StockComponentWrapper {
    Py_Header(FrictionAudition, StockComponentWrapper)
public:
    FrictionAudition(const std::shared_ptr<StockComponentState>& value, bool owning) :
        StockComponentWrapper(value, &s_type_, owning) {}
    std::shared_ptr<FrictionState> state() const {
        const auto value = std::dynamic_pointer_cast<FrictionState>(this->component());
        if (!value) PyErr_SetString(PyExc_ValueError, "FrictionAudition has been destroyed");
        return value;
    }
    PY_FACTORY_DECLARE()
    PY_METHOD_DECLARE(py_processFriction)
    PY_METHOD_DECLARE(py_setVehicleMatrix)
};
PyObject* FrictionState::wrap(bool owning) { return new FrictionAudition(shared_from_this(), owning); }
PyObject* FrictionAudition::pyNew(PyObject* args) {
    PyObject* object;
    if (!PyArg_ParseTuple(args, "O:FrictionAudition", &object)) return NULL;
    BW::string event;
    if (Script::setData(object, event)) return NULL;
    const auto value = std::make_shared<FrictionState>();
    value->event = event;
    allFriction.push_back(value);
    return new FrictionAudition(value, true);
}
PyObject* FrictionAudition::py_processFriction(PyObject* args) {
    PyObject* idObject; PyObject* positionObject; int state;
    if (!PyArg_ParseTuple(args, "OOi:processFriction", &idObject, &positionObject, &state)) return NULL;
    uint id; Vector3 position;
    if (Script::setData(idObject, id) || Script::setData(positionObject, position)) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    if (!value->process(id, position, state) && PyErr_Occurred()) return NULL;
    Py_RETURN_NONE;
}
PyObject* FrictionAudition::py_setVehicleMatrix(PyObject* args) {
    PyObject* object;
    if (!PyArg_ParseTuple(args, "O:setVehicleMatrix", &object)) return NULL;
    MatrixProviderPtr provider;
    if (Script::setData(object, provider)) return NULL;
    const auto value = this->state(); if (!value) return NULL;
    ++value->revision;
    value->matrix = provider;
    Py_RETURN_NONE;
}
PY_TYPEOBJECT(FrictionAudition)
PY_BEGIN_METHODS(FrictionAudition)
    PY_METHOD(processFriction)
    PY_METHOD(setVehicleMatrix)
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES(FrictionAudition)
PY_END_ATTRIBUTES()
PY_FACTORY(FrictionAudition, Vehicular)

static void finiStockFrictionAuditions() {
    const auto states = allFriction;
    for (const auto& weak : states) if (const auto value = weak.lock()) value->destroy();
    allFriction.clear();
}

BW_END_NAMESPACE
