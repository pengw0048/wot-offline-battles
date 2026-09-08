#ifndef OFFLINE_EXPERIMENT_KERNEL_ENGINE_H
#define OFFLINE_EXPERIMENT_KERNEL_ENGINE_H
#include "kernel_value.h"
#include "query_bridge.h"
#include <array>

namespace offline_kernel {
struct Engine {
    const Value config;
    double sample_time = 0, equipment_time = 0;
    std::vector<std::string> fields;
    explicit Engine(const Value &v) : config(v) {
        for (const Value &name : elements(v.get("fields")))
            fields.push_back(name.text());
    }
    size_t actor(std::array<double, 256> &packet, size_t at, const Value &state) {
        if (fields.size() > 48)
            throw std::invalid_argument("kernel engine actor width");
        packet[at++] = state.get("kind").text() == "human" || state.get("kind").text() == "player";
        packet[at++] = integer(state, "network_id", integer(state, "id"));
        size_t mask_at = at++;
        uint64_t mask = 0;
        for (size_t i = 0; i < fields.size(); ++i) {
            const Value &value = state.get(fields[i]);
            if (value.kind != Value::Null)
                mask |= uint64_t(1) << i;
            packet[at++] = value.number();
        }
        packet[mask_at] = static_cast<double>(mask);
        for (const char *name : {"position", "velocity"}) {
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
        size_t at = actor(packet, 1, source);
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
