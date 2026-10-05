/* Exact-build CPython 2.7 bridge. Geometry borrows caller inputs; simulation
 * contexts own typed state and never retain Python objects. Reading holds
 * the GIL and cannot invoke user conversion/equality methods.
 */
#ifdef WOT_HOST_PYTHON
#include <Python.h>
#define WOT_CDECL
#else
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#define WOT_CDECL __cdecl
struct PyObject { long ob_refcnt; void *ob_type; };
typedef long Py_ssize_t;
typedef PyObject *(WOT_CDECL *PyCFunction)(PyObject *, PyObject *);
struct PyMethodDef {
    const char *ml_name;
    PyCFunction ml_meth;
    int ml_flags;
    const char *ml_doc;
};
#endif
#include <stddef.h>
#include <stdint.h>
#include <cmath>
#include <cstring>
#include <limits>
#include <exception>
#include "offline_math_batch.h"
#include "offline_navigation.h"
#include "offline_visibility.h"
#include "offline_contact_roster.h"
#include "world_stage.h"
#include "navigation_query_stage.h"
#include "offline_simulation.h"
#ifdef WOT_HOST_PYTHON
#include <time.h>
#endif

namespace {
using offline_math::Body;
using offline_math::Vec;
typedef PyObject *(WOT_CDECL *DictGetFn)(PyObject *, PyObject *);
typedef PyObject *(WOT_CDECL *StringNewFn)(const char *);
typedef PyObject *(WOT_CDECL *FloatNewFn)(double);
typedef PyObject *(WOT_CDECL *TupleNewFn)(Py_ssize_t);
typedef PyObject *(WOT_CDECL *IntNewFn)(long);
typedef PyObject *(WOT_CDECL *ObjectCallFn)(PyObject *, PyObject *, PyObject *);
typedef PyObject *(WOT_CDECL *ModuleNewFn)(
    const char *, PyMethodDef *, const char *, PyObject *, int);
typedef void (WOT_CDECL *DeallocFn)(PyObject *);

DictGetFn dict_get = 0;
StringNewFn string_new = 0;
FloatNewFn float_new = 0;
TupleNewFn tuple_new = 0;
IntNewFn int_new = 0;
ObjectCallFn object_call = 0;
ModuleNewFn module_new = 0;
const void *dict_type = 0, *tuple_type = 0, *list_type = 0;
const void *float_type = 0, *int_type = 0, *bool_type = 0;
void *string_lookup = 0;
PyObject *none_object = 0;

enum Field { ID, X, Y, Z, YAW, PITCH, ROLL, SHAPE, DESCRIPTOR, DIMS, POSITION,
    MASS, VX, VY, VZ, PUSH_YAW, GRIP, TRAVERSE_SPEED, TRAVERSE_TORQUE, TEAM,
    ALIVE, IMMOVABLE, IMPULSE, POSITION_FIXED, FIELD_COUNT };
const char *const field_names[] = {
    "id", "x", "y", "z", "yaw", "pitch", "roll", "shape", "descriptor", "dims", "position",
    "mass", "vx", "vy", "vz", "push_yaw", "contact_decel", "traverse_speed",
    "traverse_torque", "team", "alive", "immovable", "impulse", "position_fixed"
};
// Fixed owned string references, retained with this process-lived module.
PyObject *field_keys[FIELD_COUNT] = {};
struct Unsupported {};

template <typename T> T read(const void *address) {
    T value;
    std::memcpy(&value, address, sizeof(value));
    return value;
}

void decref(PyObject *object) {
    if (!object) return;
#ifdef WOT_HOST_PYTHON
    Py_DECREF(object);
#else
    if (--object->ob_refcnt == 0) {
        const auto destroy = read<DeallocFn>(static_cast<const char *>(object->ob_type) + 24);
        destroy(object);
    }
#endif
}

PyObject *fallback() {
    ++none_object->ob_refcnt;
    return none_object;
}

Py_ssize_t size(PyObject *object) {
#ifdef WOT_HOST_PYTHON
    return Py_SIZE(object);
#else
    return read<Py_ssize_t>(reinterpret_cast<const char *>(object) + 8);
#endif
}

PyObject **items(PyObject *object) {
#ifdef WOT_HOST_PYTHON
    if (object->ob_type == tuple_type) return reinterpret_cast<PyTupleObject *>(object)->ob_item;
    return reinterpret_cast<PyListObject *>(object)->ob_item;
#else
    auto data = reinterpret_cast<char *>(object) + 12;
    if (object->ob_type == tuple_type) return reinterpret_cast<PyObject **>(data);
    return read<PyObject **>(data);
#endif
}

bool sequence(PyObject *object) {
    return object && (object->ob_type == tuple_type || object->ob_type == list_type);
}

void require_sequence(PyObject *object, Py_ssize_t minimum, bool exact = false) {
    if (!sequence(object) || size(object) < minimum || (exact && size(object) != minimum))
        throw Unsupported();
}

bool plain_dict(PyObject *object) {
    if (!object || object->ob_type != dict_type) return false;
#ifdef WOT_HOST_PYTHON
    constexpr size_t lookup_offset = offsetof(PyDictObject, ma_lookup);
#else
    constexpr size_t lookup_offset = 24;
#endif
    // The string-only lookup cannot call a custom key's __eq__ and mutate a
    // borrowed peer list during this synchronous read. General dictionaries
    // retain their existing Python behavior through the local fallback.
    return read<void *>(reinterpret_cast<const char *>(object) + lookup_offset) == string_lookup;
}

PyObject *field(PyObject *object, Field key) { return dict_get(object, field_keys[key]); }
bool absent(PyObject *object) { return !object || object == none_object; }

double number(PyObject *object) {
    if (!object) throw Unsupported();
    const char *value = reinterpret_cast<const char *>(object) + sizeof(PyObject);
    if (object->ob_type == float_type) return read<double>(value);
    if (object->ob_type == int_type || object->ob_type == bool_type)
        return static_cast<double>(read<long>(value));
    // Do not call __float__ or inspect an unknown extension object's memory.
    throw Unsupported();
}

int64_t identity(PyObject *object) {
    if (!object || (object->ob_type != int_type && object->ob_type != bool_type))
        throw Unsupported();
    return static_cast<int64_t>(read<long>(reinterpret_cast<const char *>(object) + sizeof(PyObject)));
}

double optional_number(PyObject *object, Field key, double default_value) {
    PyObject *value = field(object, key);
    return value ? number(value) : default_value;
}

Vec movement(PyObject *object) {
    require_sequence(object, 2, true);
    PyObject **values = items(object);
    return {number(values[0]), number(values[1])};
}

std::array<double, 4> shape_values(PyObject *object) {
    require_sequence(object, 4);
    PyObject **values = items(object);
    return {{number(values[0]), number(values[1]), number(values[2]), number(values[3])}};
}

std::array<double, 4> shape(PyObject *object) {
    PyObject *value = field(object, SHAPE);
    if (!absent(value)) return shape_values(value);
    if (!absent(field(object, DESCRIPTOR))) throw Unsupported();
    value = field(object, DIMS);
    if (!absent(value)) {
        require_sequence(value, 3);
        PyObject **values = items(value);
        const double front = number(values[1]), back = number(values[2]);
        return {{number(values[0]), front < back ? back : front, -.8, 2.}};
    }
    return {{1.5, 3.5, -.8, 2.}};
}

void position(Body &body, PyObject *value) {
    require_sequence(value, 3);
    PyObject **values = items(value);
    body.x = number(values[0]);
    body.has_y = !absent(values[1]);
    body.y = body.has_y ? number(values[1]) : 0.;
    body.z = number(values[2]);
}

Body body_values(PyObject *object, bool rotation) {
    if (!plain_dict(object)) throw Unsupported();
    Body body = {};
    body.shape = shape(object);
    PyObject *where = rotation ? field(object, POSITION) : 0;
    if (!absent(where)) {
        position(body, where);
    } else {
        body.x = number(field(object, X));
        body.z = number(field(object, Z));
        PyObject *height = field(object, Y);
        body.has_y = rotation ? height != none_object : !absent(height);
        body.y = absent(height) ? 0. : number(height);
    }
    if (rotation) {
        body.yaw = optional_number(object, YAW, 0.);
    } else {
        body.id = identity(field(object, ID));
        body.yaw = number(field(object, YAW));
        body.pitch = optional_number(object, PITCH, 0.);
        body.roll = optional_number(object, ROLL, 0.);
    }
    return body;
}

std::vector<Body> peer_values(PyObject *object, bool rotation) {
    require_sequence(object, 0);
    const Py_ssize_t count = size(object);
    std::vector<Body> result;
    result.reserve(static_cast<size_t>(count));
    PyObject **values = items(object);
    for (Py_ssize_t index = 0; index < count; ++index)
        result.push_back(body_values(values[index], rotation));
    return result;
}

PyObject *pair_result(Vec value) {
    if (!std::isfinite(value.x) || !std::isfinite(value.z)) return fallback();
    PyObject *x = float_new(value.x);
    if (!x) return 0;
    PyObject *z = float_new(value.z);
    if (!z) { decref(x); return 0; }
    PyObject *pair = tuple_new(2);
    if (!pair) { decref(x); decref(z); return 0; }
    // The new tuple is still private. Transfer the two owned float references.
    PyObject **values = items(pair);
    values[0] = x;
    values[1] = z;
    return pair;
}

PyObject *fraction_result(double value) {
    return std::isfinite(value) ? float_new(value) : fallback();
}

PyObject *WOT_CDECL translate(PyObject *, PyObject *args) {
    try {
        require_sequence(args, 3, true);
        PyObject **values = items(args);
        const Vec move = movement(values[1]);
        if (std::abs(move.x) + std::abs(move.z) <= 1.e-12) return float_new(1.);
        const Body owner = body_values(values[0], false);
        const auto peers = peer_values(values[2], false);
        return fraction_result(offline_math::translation(owner, move, peers));
    } catch (...) { return fallback(); }
}

PyObject *WOT_CDECL slide(PyObject *, PyObject *args) {
    try {
        require_sequence(args, 3);
        if (size(args) > 4) return fallback();
        PyObject **values = items(args);
        const Vec move = movement(values[1]);
        const bool has_first = size(args) == 4 && !absent(values[3]);
        const double first = has_first ? number(values[3]) : 0.;
        const Body owner = body_values(values[0], false);
        const auto peers = peer_values(values[2], false);
        return pair_result(offline_math::slide(owner, move, peers, has_first, first));
    } catch (...) { return fallback(); }
}

PyObject *WOT_CDECL rotate(PyObject *, PyObject *args) {
    try {
        require_sequence(args, 5);
        if (size(args) > 7) return fallback();
        PyObject **values = items(args);
        Body owner = {};
        position(owner, values[0]);
        owner.yaw = number(values[1]);
        const double candidate = number(values[2]);
        owner.shape = shape_values(values[3]);
        const double pivot = size(args) >= 6 ? number(values[5]) : 0.;
        const Vec move = size(args) == 7 ? movement(values[6]) : Vec{0., 0.};
        const auto peers = peer_values(values[4], true);
        return fraction_result(offline_math::rotation(owner, candidate, pivot, move, peers));
    } catch (...) { return fallback(); }
}

#include "offline_navigation_python.inc"
#include "offline_visibility_python.inc"
#include "offline_contact_roster_python.inc"
#include "offline_world_python.inc"
#include "offline_navigation_query_python.inc"
#include "offline_simulation_python.inc"
#include "offline_simulation_control_python.inc"
#include "offline_simulation_motion_python.inc"
#include "offline_simulation_weapons_python.inc"
#include "offline_simulation_navigation_python.inc"

PyMethodDef methods[] = {
    WOT_SIMULATION_METHODS
    WOT_SIM_CONTROL_METHODS
    WOT_SIM_MOTION_METHODS
    WOT_SIM_WEAPON_METHODS
    WOT_SIM_NAVIGATION_METHODS
    {"nav_query_run", nav_query_run, 0x0001, "Run a complete same-thread navigation corridor oracle."},
    {"nav_query_filter", nav_query_filter, 0x0001, "Filter original planning materials during a native oracle call."},
    {"thread_cpu_seconds", thread_cpu_seconds, 0x0001, "Read current-thread user and kernel CPU seconds."},
    {"world_run", world_run, 0x0001, "Run the complete world law with same-thread engine frontiers."},
    {"contact_roster", contact_roster, 0x0001, "Solve one complete roster contact stage over frozen bodies."},
    {"vis_open", vis_open, 0x0001, "Load immutable foliage for asynchronous visibility."},
    {"vis_update", vis_update, 0x0001, "Publish changed foliage rows to future jobs."},
    {"vis_submit", vis_submit, 0x0001, "Prepare complete pair geometry and foliage on a worker."},
    {"vis_poll", vis_poll, 0x0001, "Drain completed visibility rays without blocking."},
    {"vis_reduce", vis_reduce, 0x0001, "Reduce the main-thread sight prefix and retire the job."},
    {"vis_cancel", vis_cancel, 0x0001, "Cancel visibility jobs without blocking."},
    {"vis_close", vis_close, 0x0001, "Retire a visibility context without joining workers."},
    {"nav_open", nav_open, 0x0001, "Load an owned map for asynchronous navigation."},
    {"nav_submit", nav_submit, 0x0001, "Submit a complete baked navigation search."},
    {"nav_poll", nav_poll, 0x0001, "Drain ready navigation paths and native corridor queries."},
    {"nav_answer", nav_answer, 0x0001, "Supply completed main-thread corridor proofs."},
    {"nav_cancel", nav_cancel, 0x0001, "Cancel navigation jobs without blocking."},
    {"nav_close", nav_close, 0x0001, "Retire a navigation context without joining workers."},
    {"translation_fraction", translate, 0x0001, "Sweep the caller's current body and peer objects."},
    {"slide_translation", slide, 0x0001, "Resolve all swept slide segments in one synchronous call."},
    {"rotation_fraction", rotate, 0x0001, "Resolve the complete caller-owned rotation sweep."},
    {0, 0, 0, 0}
};

bool initialize_keys() {
    if (field_keys[0]) return true;
    for (size_t index = 0; index < FIELD_COUNT; ++index) {
        field_keys[index] = string_new(field_names[index]);
        if (!field_keys[index]) {
            for (size_t done = 0; done < index; ++done) {
                decref(field_keys[done]);
                field_keys[done] = 0;
            }
            return false;
        }
    }
    return true;
}

#ifdef WOT_HOST_PYTHON
bool initialize_api() {
    dict_get = PyDict_GetItem;
    string_new = PyString_FromString;
    float_new = PyFloat_FromDouble;
    tuple_new = PyTuple_New;
    int_new = PyInt_FromLong;
    object_call = PyObject_Call;
    module_new = Py_InitModule4;
    dict_type = &PyDict_Type; tuple_type = &PyTuple_Type; list_type = &PyList_Type;
    float_type = &PyFloat_Type; int_type = &PyInt_Type; bool_type = &PyBool_Type;
    none_object = Py_None;
    PyObject *probe = PyDict_New();
    if (!probe) return false;
    string_lookup = read<void *>(reinterpret_cast<const char *>(probe) + offsetof(PyDictObject, ma_lookup));
    decref(probe);
    return true;
}
#else
static_assert(sizeof(void *) == 4 && sizeof(long) == 4 && sizeof(PyObject) == 8,
              "The embedded bridge supports Win32 #1513 only");
constexpr uintptr_t IMAGE_BASE = 0x00400000U;

bool readable(const void *address, size_t bytes) {
    uintptr_t cursor = reinterpret_cast<uintptr_t>(address);
    if (!address || !bytes || bytes > UINTPTR_MAX - cursor) return false;
    const uintptr_t end = cursor + bytes;
    while (cursor < end) {
        MEMORY_BASIC_INFORMATION info;
        if (VirtualQuery(reinterpret_cast<const void *>(cursor), &info, sizeof(info)) != sizeof(info) ||
            info.State != MEM_COMMIT || (info.Protect & PAGE_GUARD)) return false;
        const DWORD mode = info.Protect & 0xffU;
        if (mode != PAGE_READONLY && mode != PAGE_READWRITE && mode != PAGE_WRITECOPY &&
            mode != PAGE_EXECUTE_READ && mode != PAGE_EXECUTE_READWRITE && mode != PAGE_EXECUTE_WRITECOPY)
            return false;
        const uintptr_t start = reinterpret_cast<uintptr_t>(info.BaseAddress);
        if (info.RegionSize > UINTPTR_MAX - start || start + info.RegionSize <= cursor) return false;
        cursor = start + info.RegionSize;
    }
    return true;
}

template <size_t N> bool signature(const unsigned char *address, const unsigned char (&expected)[N]) {
    return readable(address, N) && std::memcmp(address, expected, N) == 0;
}

bool type_layout(const unsigned char *base, uint32_t rva, long basicsize, long itemsize) {
    const unsigned char *type = base + rva;
    return readable(type, 24) && read<void *>(type + 4) == base + 0x0165ff18U &&
        read<long>(type + 16) == basicsize && read<long>(type + 20) == itemsize;
}

bool initialize_api() {
    auto base = reinterpret_cast<unsigned char *>(GetModuleHandleW(0));
    if (reinterpret_cast<uintptr_t>(base) != IMAGE_BASE || !readable(base, sizeof(IMAGE_DOS_HEADER)))
        return false;
    const auto dos = reinterpret_cast<const IMAGE_DOS_HEADER *>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0 || dos->e_lfanew > 0x1000)
        return false;
    const auto nt = reinterpret_cast<const IMAGE_NT_HEADERS32 *>(base + dos->e_lfanew);
    if (!readable(nt, sizeof(*nt)) || nt->Signature != IMAGE_NT_SIGNATURE ||
        nt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386 ||
        nt->FileHeader.TimeDateStamp != 0x5a6edca4U ||
        nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC ||
        nt->OptionalHeader.ImageBase != IMAGE_BASE || nt->OptionalHeader.SizeOfImage != 0x0206a000U)
        return false;
    static const unsigned char init_bytes[] = {0x55,0x8b,0xec,0x81,0xec,0x14,0x02,0x00,0x00,0xa1,0x70,0x35,0xce,0x01,0x33,0xc5};
    static const unsigned char dict_bytes[] = {0x55,0x8b,0xec,0x83,0xec,0x08,0x53,0x8b,0x5d,0x08,0x8b,0x43,0x04,0xf7,0x40,0x54};
    static const unsigned char string_bytes[] = {0x55,0x8b,0xec,0x51,0x53,0x8b,0x5d,0x08,0x56,0x8b,0xf3,0x8d,0x4e,0x01,0x66,0x90};
    static const unsigned char float_bytes[] = {0x55,0x8b,0xec,0x56,0x8b,0x35,0xc0,0xc3,0x14,0x02,0x85,0xf6,0x75,0x5a,0x68,0xe8};
    static const unsigned char tuple_bytes[] = {0x55,0x8b,0xec,0x56,0x8b,0x75,0x08,0x85,0xf6,0x79,0x14,0x6a,0x36,0x68,0x60,0xf4};
    static const unsigned char int_bytes[] = {0x55,0x8b,0xec,0x56,0x8b,0x75,0x08,0x8d,0x46,0x05,0x3d,0x05,0x01,0x00,0x00,0x77};
    static const unsigned char call_bytes[] = {0x55,0x8b,0xec,0x56,0x8b,0x75,0x08,0x57,0x8b,0x46,0x04,0x8b,0x78,0x40,0x85,0xff};
    if (!signature(base + 0x00be1940U, init_bytes) || !signature(base + 0x00be4190U, dict_bytes) ||
        !signature(base + 0x00bd85f0U, string_bytes) || !signature(base + 0x00bdc460U, float_bytes) ||
        !signature(base + 0x00bb3420U, tuple_bytes) || !signature(base + 0x00be1180U, int_bytes) ||
        !signature(base + 0x00bca730U, call_bytes)) return false;
    if (!type_layout(base, 0x01664d30U, 124, 0) || !type_layout(base, 0x0165c398U, 12, 4) ||
        !type_layout(base, 0x01660a88U, 20, 0) || !type_layout(base, 0x01664370U, 16, 0) ||
        !type_layout(base, 0x01664bf0U, 12, 0) || !type_layout(base, 0x016608b0U, 12, 0) ||
        !readable(base + 0x0165c798U, 8) || read<void *>(base + 0x0165c79cU) != base + 0x0165c7c0U)
        return false;
    dict_get = reinterpret_cast<DictGetFn>(base + 0x00be4190U);
    string_new = reinterpret_cast<StringNewFn>(base + 0x00bd85f0U);
    float_new = reinterpret_cast<FloatNewFn>(base + 0x00bdc460U);
    tuple_new = reinterpret_cast<TupleNewFn>(base + 0x00bb3420U);
    int_new = reinterpret_cast<IntNewFn>(base + 0x00be1180U);
    object_call = reinterpret_cast<ObjectCallFn>(base + 0x00bca730U);
    module_new = reinterpret_cast<ModuleNewFn>(base + 0x00be1940U);
    dict_type = base + 0x01664d30U; tuple_type = base + 0x0165c398U; list_type = base + 0x01660a88U;
    float_type = base + 0x01664370U; int_type = base + 0x01664bf0U; bool_type = base + 0x016608b0U;
    string_lookup = base + 0x00be57a0U;
    none_object = reinterpret_cast<PyObject *>(base + 0x0165c798U);
    return true;
}
#endif
} // namespace

#ifdef WOT_HOST_PYTHON
extern "C" __attribute__((visibility("default"))) void initoffline_math_batch_native(void) {
#else
extern "C" __declspec(dllexport) void __cdecl initoffline_math_batch_native(void) {
#endif
    if (!initialize_api() || !initialize_keys()) return;
    module_new("offline_math_batch_native", methods,
               "Exact #1513 geometry and asynchronous navigation/visibility.", 0, 1013);
}
