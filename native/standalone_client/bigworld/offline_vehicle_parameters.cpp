#include "offline_vehicle_parameters.hpp"

#include <cmath>
#include <cstring>
#include <limits>
#include <utility>

namespace {
using namespace wot::offline_vehicle_control;

class Owned {
public:
    explicit Owned(PyObject* object):object_(object) {}
    ~Owned() { Py_XDECREF(object_); }
    PyObject* get() const { return object_; }
private:
    Owned(const Owned&);
    Owned& operator=(const Owned&);
    PyObject* object_;
};

bool number(PyObject* source,double& output) {
    if (!source) return false;
    if (!PyNumber_Check(source) || !source->ob_type->tp_as_number ||
        !source->ob_type->tp_as_number->nb_float) {
        PyErr_SetString(PyExc_TypeError,"Offline vehicle parameter must be numeric");
        return false;
    }
    const double value=PyFloat_AsDouble(source);
    if (PyErr_Occurred()) return false;
    if (!std::isfinite(value)) {
        PyErr_SetString(PyExc_ValueError,"Offline vehicle parameter must be finite");
        return false;
    }
    output=value;
    return true;
}
class Reader {
public:
    explicit Reader(PyObject* source):source_(source) {}
    PyObject* item(const char* name,bool required=true) const {
        if (PyErr_Occurred()) return NULL;
        if (!source_ || !PyDict_Check(source_)) {
            PyErr_SetString(PyExc_TypeError,"Offline vehicle parameters must be a dictionary");
            return NULL;
        }
        PyObject* value=PyDict_GetItemString(source_,name);
        if (!value && required) PyErr_Format(PyExc_KeyError,"Missing offline vehicle parameter: %s",name);
        return value;
    }
    bool field(const char* name,double& output) const { return number(item(name),output); }
    bool optional(const char* name,double& output) const {
        PyObject* value=item(name,false);
        return value ? number(value,output) : !PyErr_Occurred();
    }
private:
    PyObject* source_;
};

template<class T> struct Field { const char* name; double T::* member; };
template<class T,std::size_t N>
bool fields(const Reader& reader,T& output,const Field<T>(&table)[N]) {
    for (const auto& field:table) if (!reader.field(field.name,output.*field.member)) return false;
    return true;
}
bool size_value(PyObject* source,std::size_t& output) {
    if (!source || (!PyInt_Check(source) && !PyLong_Check(source)) || PyBool_Check(source)) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_TypeError,"Suspension iteration count must be integral");
        return false;
    }
    unsigned PY_LONG_LONG value;
    if (PyInt_Check(source)) {
        const long integer=PyInt_AsLong(source);
        if (PyErr_Occurred()) return false;
        if (integer<0) {
            PyErr_SetString(PyExc_OverflowError,"Suspension iteration count is negative");
            return false;
        }
        value=static_cast<unsigned PY_LONG_LONG>(integer);
    } else {
        value=PyLong_AsUnsignedLongLong(source);
        if (PyErr_Occurred()) return false;
    }
    if (value>std::numeric_limits<std::size_t>::max()) {
        PyErr_SetString(PyExc_OverflowError,"Suspension iteration count exceeds size_t");
        return false;
    }
    output=static_cast<std::size_t>(value);
    return true;
}
bool side(PyObject* source,ContactSide& output) {
    if (!source || source==Py_None) { output=ContactSide::none;return !PyErr_Occurred(); }
    if (!PyString_Check(source)) {
        PyErr_SetString(PyExc_TypeError,"Suspension contact side must be a string or None");
        return false;
    }
    const char* value=PyString_AsString(source);
    if (!value) return false;
    if (strcmp(value,"left")==0) output=ContactSide::left;
    else if (strcmp(value,"right")==0) output=ContactSide::right;
    else {
        PyErr_SetString(PyExc_ValueError,"Suspension contact side is unknown");
        return false;
    }
    return true;
}
bool point(const Reader& reader,SuspensionPoint& output,bool required_side) {
    return reader.field("x",output.x) && reader.optional("y",output.y) &&
        reader.field("z",output.z) && side(reader.item("side",required_side),output.side);
}
template<class T,class Parse>
bool sequence(PyObject* source,std::vector<T>& output,Parse parse) {
    if (!source) return false;
    Owned values(PySequence_Fast(source,"Suspension records must be a sequence"));
    if (!values.get()) return false;
    const Py_ssize_t count=PySequence_Fast_GET_SIZE(values.get());
    output.reserve(static_cast<std::size_t>(count));
    for (Py_ssize_t i=0;i<count;++i) {
        T candidate;
        if (!parse(Reader(PySequence_Fast_GET_ITEM(values.get(),i)),candidate)) return false;
        output.push_back(std::move(candidate));
    }
    return true;
}
} // namespace

bool parseOfflineVehicleParameters(PyObject* source,Parameters& output) {
    Parameters candidate{};
    static const Field<Parameters> table[]={
        {"mass",&Parameters::mass},{"powerW",&Parameters::power_w},
        {"speedFwd",&Parameters::speed_forward},{"speedBwd",&Parameters::speed_backward},
        {"rotSpd",&Parameters::rotation_speed},{"specificFriction",&Parameters::specific_friction},
        {"brakeDecel",&Parameters::brake_deceleration},{"trackCenter",&Parameters::track_center},
        {"nativePowerRatio",&Parameters::native_power_ratio}};
    const Reader reader(source);
    if (!fields(reader,candidate,table)) return false;
    PyObject* resistance=reader.item("terrainResist");
    if (!resistance) return false;
    Owned values(PySequence_Fast(resistance,"Terrain resistance must be a sequence"));
    if (!values.get()) return false;
    if (PySequence_Fast_GET_SIZE(values.get())!=3) {
        PyErr_SetString(PyExc_ValueError,"Terrain resistance needs three elements");
        return false;
    }
    for (int i=0;i<3;++i)
        if (!number(PySequence_Fast_GET_ITEM(values.get(),i),candidate.terrain_resistance[i])) return false;
    output=candidate;
    return true;
}

bool parseOfflineSuspensionParameters(PyObject* source,SuspensionParameters& output) {
    SuspensionParameters candidate;
    static const Field<SuspensionParameters> table[]={
        {"mass",&SuspensionParameters::mass},{"width",&SuspensionParameters::width},
        {"length",&SuspensionParameters::length},{"clearance",&SuspensionParameters::clearance},
        {"rest_length",&SuspensionParameters::rest_length},
        {"static_compression",&SuspensionParameters::static_compression},
        {"hard_ratio",&SuspensionParameters::hard_ratio},
        {"contact_memory_distance",&SuspensionParameters::contact_memory_distance},
        {"pitch_inertia",&SuspensionParameters::pitch_inertia},
        {"roll_inertia",&SuspensionParameters::roll_inertia},{"fixed_step",&SuspensionParameters::fixed_step}};
    const Reader reader(source);
    if (!fields(reader,candidate,table) ||
        !size_value(reader.item("constraint_iterations"),candidate.constraint_iterations)) return false;
    if (!sequence(reader.item("springs"),candidate.springs,
        [](const Reader& current,SuspensionSpring& spring) {
            return point(current,spring,true) && current.field("static_compression",spring.static_compression) &&
                current.field("max_compression",spring.max_compression) && current.field("stiffness",spring.stiffness) &&
                current.field("damping",spring.damping) && current.field("max_force",spring.max_force);
        })) return false;
    PyObject* pseudo=reader.item("pseudo_contacts",false);
    if (PyErr_Occurred()) return false;
    if (pseudo && !sequence(pseudo,candidate.pseudo_contacts,
        [](const Reader& current,SuspensionPseudoContact& contact) {
            return point(current,contact,false) && current.optional("penetration",contact.penetration);
        })) return false;
    output=std::move(candidate);
    return true;
}

bool parseOfflineVehicleTuning(PyObject* source,Tuning& output) {
    PyObject* dictionary=source;
    if (source && PyModule_Check(source)) dictionary=PyModule_GetDict(source);
    const Reader reader(dictionary);
    Tuning candidate;
    static const Field<Tuning> table[]={
        {"GRAVITY_FACTOR",&Tuning::gravity_factor},{"COHESION",&Tuning::cohesion},
        {"DRIVE_TRACTION",&Tuning::drive_traction},{"SLIP_DRAG",&Tuning::slip_drag},
        {"SLIP_THRESHOLD_TAN",&Tuning::slip_threshold_tan},{"POWER_FACTOR",&Tuning::power_factor},
        {"BKWD_POWER_FRACTION",&Tuning::backward_power_fraction},
        {"ANG_ACCELERATION_TIME",&Tuning::traverse_acceleration_time},
        {"SPEED_AFFECT_ROT_DECREASE",&Tuning::traverse_speed_cost},
        {"COAST_BRAKE_SHARE",&Tuning::coast_brake_share},{"STEER_RESIST_MULT",&Tuning::steering_resistance_multiplier},
        {"SLIDE_HOLD_TAN",&Tuning::slide_hold_tan},{"SLIDE_KINETIC",&Tuning::slide_kinetic},
        {"OVERSPEED_MAX_FACTOR",&Tuning::overspeed_max_factor},{"OVERSPEED_DAMP",&Tuning::overspeed_damping},
        {"OVERSPEED_BUILD",&Tuning::overspeed_build},{"SLOPE_COH_DECAY",&Tuning::slope_cohesion_decay},
        {"SLOPE_COH_DECAY_Y",&Tuning::slope_cohesion_decay_y},{"COH_DECAY_BOUND",&Tuning::cohesion_decay_bound},
        {"SLIDE_MAX",&Tuning::slide_max},{"SLIDE_DRAG",&Tuning::slide_drag},
        {"FALL_SAFE_SPEED",&Tuning::fall_safe_speed},{"FALL_DMG_PER_MS",&Tuning::fall_damage_per_speed}};
    if (!fields(reader,candidate,table)) return false;
    output=candidate;
    return true;
}
