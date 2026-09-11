#ifndef OFFLINE_EXPERIMENT_KERNEL_PUBLICATION_H
#define OFFLINE_EXPERIMENT_KERNEL_PUBLICATION_H
#include "kernel_equipment.h"
#include <array>

namespace offline_kernel {
// Retain the signature's ordered structure and missing/null distinction without
// allocating a Value container for every tuple and every (presence, value) pair.
// Two buffers keep allocations stable after warmup. Values retain the same
// shallow ownership as the old signature; this does not mutate Bot state.
struct PublicationEdges {
    struct Key {
        std::string name;
        int slot;
        Field field() const { return Field{slot, name.c_str(), name.size()}; }
    };
    struct Scalar {
        bool present = false;
        Value value;
        bool operator==(const Scalar &other) const {
            return present == other.present && value == other.value;
        }
    };
    struct BotEdge {
        std::vector<Scalar> scalars;
        Value ammo;
        std::vector<std::array<Value, 7>> equipment;
        std::array<Scalar, 2> shot;
        bool operator==(const BotEdge &other) const {
            return scalars == other.scalars && ammo == other.ammo &&
                   equipment == other.equipment && shot == other.shot;
        }
    };
    struct Signature {
        std::vector<BotEdge> bots;
        std::vector<std::array<Value, 2>> launches;
        std::vector<std::array<Value, 4>> rams;
        bool operator==(const Signature &other) const {
            return bots == other.bots && launches == other.launches && rams == other.rams;
        }
    };
    std::vector<Key> fields;
    Signature current, previous;
    bool valid = false;
    explicit PublicationEdges(const Value &names) {
        for (const Value &name : elements(names)) {
            std::string key = name.text();
            fields.push_back(Key{key, field_slot(key)});
        }
    }
    void begin(size_t count) { current.bots.resize(count); }
    static void scalar(Scalar &out, const Value &state, Field key) {
        out.present = state.has(key);
        out.value = state.get(key);
    }
    void capture(size_t index, const Value &state, const std::vector<Equipment> &equipment,
                 double now) {
        BotEdge &out = current.bots.at(index);
        out.scalars.resize(fields.size());
        for (size_t i = 0; i < fields.size(); ++i)
            scalar(out.scalars[i], state, fields[i].field());
        out.ammo = state.get(sf::ammo_remaining);
        out.equipment.resize(equipment.size());
        for (size_t i = 0; i < equipment.size(); ++i) {
            const Equipment &e = equipment[i];
            out.equipment[i] = {{e.contract, Value(e.uses), Value(e.active), Value(e.ready_at),
                                 Value(now >= e.ready_at),
                                 e.auto_pending ? Value(e.auto_since) : Value(),
                                 e.ai_pending ? Value(e.ai_since) : Value()}};
        }
        scalar(out.shot[0], state, sf::shot_yaw);
        scalar(out.shot[1], state, sf::shot_pitch);
    }
    bool finish(const Value &launches, const Value &rams) {
        current.launches.resize(launches.size());
        for (size_t i = 0; i < launches.size(); ++i)
            current.launches[i] = {{launches[i].get(sf::id), launches[i].get(sf::fire_seq)}};
        current.rams.resize(rams.size());
        for (size_t i = 0; i < rams.size(); ++i)
            current.rams[i] = {{rams[i].get("bot_id"), rams[i].get(sf::target_kind),
                                rams[i].get(sf::target_id), rams[i].get("ram_seq")}};
        if (valid && current == previous)
            return false;
        std::swap(current, previous);
        valid = true;
        return true;
    }
};
} // namespace offline_kernel
#endif
