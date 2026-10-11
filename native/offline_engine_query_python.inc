// Shared synchronous ray frontier. No PyObject enters the background pool.
// Include after offline_world_python.inc and before its owner adapters.
namespace engine_query {
enum Capability {
    Vector, XYZ, Length, Add, Subtract, Scale, Normalise, Ray, Skin, Alias,
    Filter, Equal, Skipped, NativeFilter, Failure, Unavailable, CanRecast,
    Tuning, RecoverGround, CapabilityCount
};
struct Failed {};

class Object {
    PyObject *value_ = nullptr;
public:
    Object() = default;
    explicit Object(PyObject *value): value_(value) {
        if (!value_) throw PythonAllocationFailed();
    }
    static Object borrow(PyObject *value) {
        if (!value) throw Unsupported();
        ++value->ob_refcnt;
        return Object(value);
    }
    Object(const Object &other): value_(other.value_) {
        if (value_) ++value_->ob_refcnt;
    }
    Object(Object &&other) noexcept: value_(other.value_) { other.value_ = nullptr; }
    Object &operator=(Object other) noexcept { std::swap(value_, other.value_); return *this; }
    ~Object() { decref(value_); }
    PyObject *get() const { return value_; }
};

void borrow(OwnedTuple &tuple, size_t index, PyObject *value) {
    if (!value) throw Unsupported();
    ++value->ob_refcnt;
    tuple.set(index, value);
}

class Session;
struct FilterContext {
    Session *session;
    PyObject *filter;
    const std::vector<Object> *excluded;
    std::vector<Object> candidates;
    bool failed = false;
};
thread_local FilterContext *filter_context = nullptr;
struct FilterBinding {
    FilterContext *previous;
    explicit FilterBinding(FilterContext &current): previous(filter_context) {
        filter_context = &current;
    }
    ~FilterBinding() { filter_context = previous; }
};

class Session {
    Object capabilities_;
    PyObject **caps_;
    unsigned budget_;
    double epsilon_;

    bool same(PyObject *a, PyObject *b) {
        if (a == b) return true;
        if (sequence(a) && sequence(b) && size(a) == size(b)) {
            bool plain = true;
            for (Py_ssize_t i = 0; i < size(a); ++i) {
                auto x = items(a)[i], y = items(b)[i];
                if ((x->ob_type == int_type || x->ob_type == bool_type) &&
                    (y->ob_type == int_type || y->ob_type == bool_type)) {
                    if (identity(x) != identity(y)) return false;
                } else { plain = false; break; }
            }
            if (plain) return true;
        }
        OwnedTuple args(2);
        borrow(args, 0, a); borrow(args, 1, b);
        return number(call(Equal, args).get()) != 0.;
    }

    bool contains(const std::vector<Object> &rows, PyObject *value) {
        for (const auto &row: rows) if (same(row.get(), value)) return true;
        return false;
    }

    Object alias(PyObject *surface) {
        if (sequence(surface) && size(surface) == 4) {
            bool exact_ints = true;
            for (unsigned i = 0; i < 4; ++i)
                exact_ints = exact_ints && items(surface)[i]->ob_type == int_type;
            if (exact_ints) {
                const auto material = identity(items(surface)[0]);
                const auto flags = identity(items(surface)[1]);
                if (material < 71 || material > 86 || !(flags & 0x80))
                    return Object::borrow(none_object);
            }
        }
        OwnedTuple args(1); borrow(args, 0, surface);
        return call(Alias, args);
    }

    Object raw_ray(PyObject *start, PyObject *end, PyObject *filter, bool ground) {
        OwnedTuple args(4);
        borrow(args, 0, start); borrow(args, 1, end); borrow(args, 2, filter);
        args.set(3, int_new(ground ? 1 : 0));
        return call(Ray, args);
    }

    Object scalar_vector(Capability operation, PyObject *vector, double factor) {
        OwnedTuple args(2); borrow(args, 0, vector); args.set(1, float_new(factor));
        return call(operation, args);
    }

public:
    explicit Session(PyObject *capabilities): capabilities_(Object::borrow(capabilities)) {
        require_sequence(capabilities, CapabilityCount, true);
        caps_ = items(capabilities);
        require_sequence(caps_[Tuning], 2, true);
        const auto budget = identity(items(caps_[Tuning])[0]);
        epsilon_ = finite_number(items(caps_[Tuning])[1]);
        if (budget < 0 || budget > 1024 || epsilon_ <= 0.) throw Unsupported();
        budget_ = static_cast<unsigned>(budget);
    }

    Object call(Capability operation, OwnedTuple &arguments) {
        NativeProfilePhase bridge(NativePhase::Pack);
        Object args(arguments.release());
        Object result(profiled_object_call(NativeCallback::EngineQuery,
            "engine.query", static_cast<int>(operation), 1, caps_[operation], args.get()));
        bridge.input();
        if (result.get() == caps_[Failure]) throw Failed();
        return result;
    }

    Object vector(const double *point) {
        OwnedTuple args(3);
        for (unsigned i = 0; i < 3; ++i) args.set(i, float_new(point[i]));
        return call(Vector, args);
    }

    void point(PyObject *value, double *result) {
        OwnedTuple args(1); borrow(args, 0, value);
        auto coordinates = call(XYZ, args);
        world_array(coordinates.get(), result, 3);
    }

    double length(PyObject *value) {
        OwnedTuple args(1); borrow(args, 0, value);
        return finite_number(call(Length, args).get());
    }

    Object add(PyObject *a, PyObject *b) {
        OwnedTuple args(2); borrow(args, 0, a); borrow(args, 1, b);
        return call(Add, args);
    }
    Object subtract(PyObject *a, PyObject *b) {
        OwnedTuple args(2); borrow(args, 0, a); borrow(args, 1, b);
        return call(Subtract, args);
    }
    Object scale(PyObject *value, double factor) { return scalar_vector(Scale, value, factor); }

    bool keep(FilterContext &context, PyObject *surface) {
        OwnedTuple args(2); borrow(args, 0, context.filter); borrow(args, 1, surface);
        const bool accepted = number(call(Filter, args).get()) != 0.;
        // The original callback evaluates alias classification even when its
        // live ledger rejects the surface. Keep that ordering and live state.
        auto key = alias(surface);
        bool keep = accepted && !contains(*context.excluded, surface);
        if (keep && key.get() != none_object) {
            require_sequence(key.get(), 2, true);
            for (const auto &excluded: *context.excluded) {
                require_sequence(excluded.get(), 2);
                OwnedTuple prefix(2);
                borrow(prefix, 0, items(excluded.get())[0]);
                borrow(prefix, 1, items(excluded.get())[1]);
                Object pair(prefix.release());
                if (same(key.get(), pair.get())) { keep = false; break; }
            }
        }
        if (keep && sequence(surface) && size(surface) == 4 &&
            context.candidates.size() < 16 && !contains(context.candidates, surface))
            context.candidates.push_back(Object::borrow(surface));
        return keep;
    }

    Object collide(PyObject *start, PyObject *end, PyObject *collision_filter,
                   bool ground_unknown = false) {
        try {
            return collide_impl(start, end, collision_filter, ground_unknown);
        } catch (const Failed &) {
            if (ground_unknown) {
                OwnedTuple recover(0);
                if (number(call(RecoverGround, recover).get()) != 0.)
                    return Object::borrow(none_object);
            }
            throw;
        }
    }

private:
    Object collide_impl(PyObject *start, PyObject *end, PyObject *collision_filter,
                        bool ground_unknown) {
        OwnedTuple empty(0);
        if (collision_filter == none_object || number(call(CanRecast, empty).get()) == 0.) {
            auto hit = raw_ray(start, end, collision_filter, ground_unknown);
            return hit.get() == caps_[Unavailable] ? Object::borrow(none_object) : hit;
        }
        struct Segment { Object start, end; std::vector<Object> excluded; };
        std::vector<Segment> segments;
        segments.push_back({Object::borrow(start), Object::borrow(end), {}});
        unsigned remaining = budget_;
        while (!segments.empty()) {
            Segment segment = std::move(segments.back());
            segments.pop_back();
            FilterContext filter{this, collision_filter, &segment.excluded, {}, false};
            Object hit;
            {
                FilterBinding binding(filter);
                hit = raw_ray(segment.start.get(), segment.end.get(), caps_[NativeFilter], ground_unknown);
            }
            if (filter.failed) throw Failed();
            if (hit.get() == caps_[Unavailable]) return Object::borrow(none_object);
            if (hit.get() == none_object) continue;
            OwnedTuple candidates(filter.candidates.size());
            for (size_t i = 0; i < filter.candidates.size(); ++i)
                borrow(candidates, i, filter.candidates[i].get());
            OwnedTuple args(5);
            borrow(args, 0, hit.get()); borrow(args, 1, segment.start.get());
            borrow(args, 2, segment.end.get()); args.set(3, candidates.release());
            args.set(4, int_new(ground_unknown ? 1 : 0));
            auto skin = call(Skin, args);
            if (skin.get() == caps_[Unavailable]) return Object::borrow(none_object);
            if (skin.get() == none_object || !remaining) return hit;
            require_sequence(skin.get(), 2, true);
            const double exit = finite_number(items(skin.get())[0]);
            auto excluded = items(skin.get())[1];
            require_sequence(excluded, 0);
            auto direction = subtract(segment.end.get(), segment.start.get());
            const double distance = length(direction.get());
            if (distance <= epsilon_) return hit;
            OwnedTuple normalize(1); borrow(normalize, 0, direction.get());
            direction = call(Normalise, normalize);
            Object bound = segment.end;
            if (exit < distance) {
                auto delta = scale(direction.get(), exit);
                bound = add(segment.start.get(), delta.get());
            }
            --remaining;
            if (exit + epsilon_ < distance) {
                auto step = scale(direction.get(), epsilon_);
                auto tail = add(bound.get(), step.get());
                segments.push_back({std::move(tail), segment.end, segment.excluded});
            }
            auto united = segment.excluded;
            for (Py_ssize_t i = 0; i < size(excluded); ++i)
                if (!contains(united, items(excluded)[i]))
                    united.push_back(Object::borrow(items(excluded)[i]));
            segments.push_back({segment.start, std::move(bound), std::move(united)});
            OwnedTuple note(0);
            auto ignored = call(Skipped, note);
        }
        return Object::borrow(none_object);
    }
};
} // namespace engine_query

PyObject *WOT_CDECL engine_query_filter(PyObject *, PyObject *args) {
    NATIVE_PROFILE_ENTRY(engine_query_filter);
    auto *context = engine_query::filter_context;
    if (!context) return int_new(1);
    try {
        const bool keep = profile.run(&engine_query::Session::keep,
                                     context->session, *context, args);
        return int_new(keep ? 1 : 0);
    } catch (const PythonAllocationFailed &) {
        profile.fail(); context->failed = true; return nullptr;
    } catch (...) {
        // A native callback cannot throw across BigWorld's C boundary. Reject
        // this traversal, then fail the owning operation before using its hit.
        profile.fail(); context->failed = true; return int_new(0);
    }
}
