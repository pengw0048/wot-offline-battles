// Real CPython 2.7 C API integration fixture for the producer boundary.
#include "offline_vehicle_parameters.hpp"

using namespace wot::offline_vehicle_control;
namespace {
PyObject* vehicle(PyObject*,PyObject* source) {
    Parameters p{};p.mass=123;p.power_w=456;p.terrain_resistance={7,8,9};
    if (!parseOfflineVehicleParameters(source,p)) {
        if (p.mass!=123 || p.power_w!=456 || p.terrain_resistance[1]!=8) {
            PyErr_Clear();PyErr_SetString(PyExc_AssertionError,"Vehicle parse was not atomic");
        }
        return NULL;
    }
    return Py_BuildValue("(dddddddddddd)",p.mass,p.power_w,p.speed_forward,p.speed_backward,
        p.rotation_speed,p.terrain_resistance[0],p.terrain_resistance[1],p.terrain_resistance[2],
        p.specific_friction,p.brake_deceleration,p.track_center,p.native_power_ratio);
}
PyObject* suspension(PyObject*,PyObject* source) {
    SuspensionParameters p;p.mass=123;p.springs.resize(1);p.springs[0].x=456;
    if (!parseOfflineSuspensionParameters(source,p)) {
        if (p.mass!=123 || p.springs.size()!=1 || p.springs[0].x!=456) {
            PyErr_Clear();PyErr_SetString(PyExc_AssertionError,"Suspension parse was not atomic");
        }
        return NULL;
    }
    PyObject* springs=PyList_New(static_cast<Py_ssize_t>(p.springs.size()));
    PyObject* contacts=PyList_New(static_cast<Py_ssize_t>(p.pseudo_contacts.size()));
    if (!springs || !contacts) { Py_XDECREF(springs);Py_XDECREF(contacts);return NULL; }
    for (std::size_t i=0;i<p.springs.size();++i) {
        const auto& s=p.springs[i];
        PyObject* item=Py_BuildValue("(dddiddddd)",s.x,s.y,s.z,static_cast<int>(s.side),
            s.static_compression,s.max_compression,s.stiffness,s.damping,s.max_force);
        if (!item) { Py_DECREF(springs);Py_DECREF(contacts);return NULL; }
        PyList_SET_ITEM(springs,static_cast<Py_ssize_t>(i),item);
    }
    for (std::size_t i=0;i<p.pseudo_contacts.size();++i) {
        const auto& c=p.pseudo_contacts[i];
        PyObject* item=Py_BuildValue("(dddid)",c.x,c.y,c.z,static_cast<int>(c.side),c.penetration);
        if (!item) { Py_DECREF(springs);Py_DECREF(contacts);return NULL; }
        PyList_SET_ITEM(contacts,static_cast<Py_ssize_t>(i),item);
    }
    PyObject* result=Py_BuildValue("(dddddddddddKOO)",p.mass,p.width,p.length,p.clearance,
        p.rest_length,p.static_compression,p.hard_ratio,p.contact_memory_distance,
        p.pitch_inertia,p.roll_inertia,p.fixed_step,
        static_cast<unsigned PY_LONG_LONG>(p.constraint_iterations),springs,contacts);
    Py_DECREF(springs);Py_DECREF(contacts);
    return result;
}
PyObject* tuning(PyObject*,PyObject* source) {
    Tuning t;t.gravity_factor=123;t.fall_damage_per_speed=456;
    if (!parseOfflineVehicleTuning(source,t)) {
        if (t.gravity_factor!=123 || t.fall_damage_per_speed!=456) {
            PyErr_Clear();PyErr_SetString(PyExc_AssertionError,"Tuning parse was not atomic");
        }
        return NULL;
    }
    const double values[]={t.gravity_factor,t.cohesion,t.drive_traction,t.slip_drag,
        t.slip_threshold_tan,t.power_factor,t.backward_power_fraction,t.traverse_acceleration_time,
        t.traverse_speed_cost,t.coast_brake_share,t.steering_resistance_multiplier,t.slide_hold_tan,
        t.slide_kinetic,t.overspeed_max_factor,t.overspeed_damping,t.overspeed_build,
        t.slope_cohesion_decay,t.slope_cohesion_decay_y,t.cohesion_decay_bound,
        t.slide_max,t.slide_drag,t.fall_safe_speed,t.fall_damage_per_speed};
    PyObject* result=PyTuple_New(sizeof(values)/sizeof(values[0]));
    if (!result) return NULL;
    for (std::size_t i=0;i<sizeof(values)/sizeof(values[0]);++i) {
        PyObject* value=PyFloat_FromDouble(values[i]);
        if (!value) { Py_DECREF(result);return NULL; }
        PyTuple_SET_ITEM(result,static_cast<Py_ssize_t>(i),value);
    }
    return result;
}
PyMethodDef methods[]={
    {"vehicle",vehicle,METH_O,NULL},{"suspension",suspension,METH_O,NULL},
    {"tuning",tuning,METH_O,NULL},{NULL,NULL,0,NULL}};
}
PyMODINIT_FUNC init_offline_vehicle_parameters_probe(void) {
    Py_InitModule("_offline_vehicle_parameters_probe",methods);
}
