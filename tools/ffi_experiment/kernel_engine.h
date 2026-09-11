#ifndef OFFLINE_EXPERIMENT_KERNEL_ENGINE_H
#define OFFLINE_EXPERIMENT_KERNEL_ENGINE_H
#include "kernel_value.h"
#include "query_bridge.h"
#include <array>

namespace offline_kernel {
struct Engine {
    const Value config;
    double sample_time = 0, equipment_time = 0;
    std::vector<Field> fields;
    uint64_t descriptor_fields = 0, position_fields = 0, scan_fields = 0, cancel_fields = 0;
    explicit Engine(const Value &v) : config(v) {
        for (const Value &name : elements(v.get("fields"))) {
            if (name.kind != Value::String)
                throw std::invalid_argument("kernel engine field name");
            if (fields.size() >= 48)
                throw std::invalid_argument("kernel engine actor width");
            uint64_t bit = uint64_t(1) << fields.size();
            const std::string &key = name.data->string;
            if (key == "siege_state")
                descriptor_fields |= bit;
            if (key == "x" || key == "y" || key == "z")
                position_fields |= bit;
            if (key == "id")
                cancel_fields |= bit;
            if (key == "id" || key == "yaw" || key == "speed")
                scan_fields |= bit;
            fields.push_back(Field{field_slot(key), key.c_str(), key.size()});
        }
        scan_fields |= position_fields;
    }
    size_t actor(std::array<double, 256> &packet, size_t at, const Value &state,
                 uint64_t wanted = ~uint64_t(0)) {
        if (state.kind != Value::Object || !state.truth())
            return at + 3 + fields.size() + 8;
        packet[at++] =
            state.get(sf::kind).text() == "human" || state.get(sf::kind).text() == "player";
        packet[at++] = integer(state, sf::network_id, integer(state, sf::id));
        size_t mask_at = at++;
        uint64_t mask = 0;
        for (size_t i = 0; i < fields.size(); ++i) {
            if (!(wanted & (uint64_t(1) << i))) {
                ++at;
                continue;
            }
            const Value &value = state.get(fields[i]);
            if (value.kind != Value::Null)
                mask |= uint64_t(1) << i;
            packet[at++] = value.number();
        }
        packet[mask_at] = static_cast<double>(mask);
        if (wanted != ~uint64_t(0))
            return at + 8;
        for (Field name : {sf::position, sf::velocity}) {
            const Value &v = state.get(name);
            bool valid = v.kind == Value::Array && v.size() == 3;
            packet[at++] = valid;
            for (size_t i = 0; i < 3; ++i)
                packet[at++] = valid ? v[i].number() : 0;
        }
        return at;
    }
    std::array<double, 256> query(int kind, const Value &source, const Value &target,
                                  const std::vector<double> &values = {}) {
        std::array<double, 256> packet{};
        packet[0] = kind;
        uint64_t wanted = ~uint64_t(0);
        if (kind == 760 && !values.empty())
            wanted = values[0] == 615                       ? wanted
                     : values[0] == 610 || values[0] == 611 ? descriptor_fields
                                                            : 0;
        else if (kind == 769)
            wanted = position_fields;
        else if (kind == 771)
            wanted = scan_fields;
        else if (kind == 766)
            wanted = cancel_fields;
        size_t at = actor(packet, 1, source, wanted);
        // These leaves have no target consumer. Keep the fixed packet offset.
        if (kind == 760 || kind == 761 || kind == 766 || kind == 769 || kind == 771)
            at += 3 + fields.size() + 8;
        else
            at = actor(packet, at, target);
        if (at + values.size() > 254)
            throw std::invalid_argument("kernel engine packet width");
        std::copy(values.begin(), values.end(), packet.begin() + at);
        packet[254] = sample_time;
        packet[255] = equipment_time;
        if (offline_query(packet.data(), packet.size()))
            throw std::runtime_error("kernel engine callback");
        return packet;
    }
};
} // namespace offline_kernel
#endif
