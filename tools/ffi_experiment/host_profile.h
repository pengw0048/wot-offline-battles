/* Optional Linux-host CPU attribution. Never compiled into the #1513 bridge.
 * Zones partition one thread's synchronous loop, including nested dispatches.
 * Python zones include their ordinary builtin/native callees and test fakes;
 * they are ownership boundaries, not Python bytecode instruction timings. */
#ifndef OFFLINE_HOST_PROFILE_H
#define OFFLINE_HOST_PROFILE_H
#include <time.h>

enum { PROFILE_OUTER, PROFILE_CORE, PROFILE_BRIDGE, PROFILE_CALLBACK, PROFILE_ZONES };
enum { PROFILE_ROWS = 804 };
static struct {
    int enabled, zone;
    double last, seconds[PROFILE_ZONES], callbacks[PROFILE_ROWS];
    unsigned long calls[PROFILE_ROWS], transitions;
} host_profile;

static double profile_clock(void)
{
    struct timespec value;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &value);
    return (double)value.tv_sec + (double)value.tv_nsec * 1e-9;
}
static void profile_zone(int zone)
{
    if (host_profile.enabled) {
        double now = profile_clock();
        host_profile.seconds[host_profile.zone] += now - host_profile.last;
        host_profile.last = now;
        host_profile.zone = zone;
        ++host_profile.transitions;
    }
}
static int profile_key(const double *packet, int count)
{
    double opcode = packet[0];
    int key = opcode >= 0 && opcode < 800 ? (int)opcode : 0;
    if (key == 772 && count > 1 && packet[1] >= 0 && packet[1] < 4)
        key = 800 + (int)packet[1];
    return key;
}
static PyObject *profile_start(PyObject *self, PyObject *args)
{
    (void)self;
    if (!PyArg_ParseTuple(args, "")) return NULL;
    if (offline_query_active() || host_profile.enabled) {
        PyErr_SetString(PyExc_RuntimeError, "host profile owner is active");
        return NULL;
    }
    memset(&host_profile, 0, sizeof(host_profile));
    host_profile.last = profile_clock();
    host_profile.enabled = 1;
    Py_RETURN_NONE;
}
static PyObject *profile_stop(PyObject *self, PyObject *args)
{
    PyObject *rows, *result;
    int key;
    (void)self;
    if (!PyArg_ParseTuple(args, "")) return NULL;
    if (offline_query_active() || !host_profile.enabled) {
        PyErr_SetString(PyExc_RuntimeError, "host profile has no idle owner");
        return NULL;
    }
    profile_zone(PROFILE_OUTER);
    host_profile.enabled = 0;
    rows = PyList_New(0);
    if (!rows) return NULL;
    for (key = 0; key < PROFILE_ROWS; ++key) {
        PyObject *row;
        int status;
        if (!host_profile.calls[key]) continue;
        row = Py_BuildValue("{s:i,s:k,s:d}", "key", key,
                           "calls", host_profile.calls[key],
                           "inclusive_cpu_seconds", host_profile.callbacks[key]);
        if (!row) { Py_DECREF(rows); return NULL; }
        status = PyList_Append(rows, row);
        Py_DECREF(row);
        if (status) { Py_DECREF(rows); return NULL; }
    }
    result = Py_BuildValue("{s:d,s:d,s:d,s:d,s:k,s:O}",
                          "python_outer", host_profile.seconds[PROFILE_OUTER],
                          "native_core", host_profile.seconds[PROFILE_CORE],
                          "callback_bridge", host_profile.seconds[PROFILE_BRIDGE],
                          "python_callbacks", host_profile.seconds[PROFILE_CALLBACK],
                          "clock_transitions", host_profile.transitions, "callbacks", rows);
    Py_DECREF(rows);
    return result;
}
#endif
