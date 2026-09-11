#ifndef OFFLINE_EXPERIMENT_KERNEL_WORLD_H
#define OFFLINE_EXPERIMENT_KERNEL_WORLD_H
#include "kernel_engine.h"
#include "world_core.h"

namespace offline_kernel {
// One stack-owned resolver composes the world sweep directly. Python retains
// descriptors, native hit/filter objects and canonical destructible operations.
struct WorldResolver {
    Engine &engine;
    int identity, mode;
    const OfflineQueryRoute *route = nullptr;
    WorldResolver(Engine &e, const Value &state)
        : engine(e), identity(integer(state, sf::id)), mode(integer(state, sf::siege_state)) {}

    std::array<double, 64> call(int operation, const double *values, size_t count) {
        if (count > 58)
            throw std::invalid_argument("kernel world request width");
        std::array<double, 64> packet{};
        packet[0] = 772;
        packet[1] = operation;
        packet[2] = identity;
        packet[3] = mode;
        std::copy(values, values + count, packet.begin() + 4);
        packet[62] = engine.sample_time;
        packet[63] = engine.equipment_time;
        int status = route ? route->forward(packet.data(), packet.size())
                           : offline_query(packet.data(), packet.size());
        if (status)
            throw std::runtime_error("kernel world callback");
        return packet;
    }
    static int callback(void *owner, double *packet, int count) {
        WorldResolver &self = *static_cast<WorldResolver *>(owner);
        if (count == 33 && packet[0] == 5) {
            auto answer = self.call(3, packet + 1, 32);
            std::copy(answer.begin(), answer.begin() + 16, packet);
            return 0;
        }
        if (count != 16 || packet[0] < 1 || packet[0] > 4)
            return self.route ? self.route->forward(packet, count) : 18;
        auto answer = self.call(1, packet, count);
        std::copy(answer.begin(), answer.begin() + 8, packet);
        return 0;
    }
    int resolve(const Value &state, const Value &profile, double turn_speed, const double *motion) {
        const Value &extents = profile.get("extents");
        if (extents.kind != Value::Array || extents.size() != 3 || !profile.has("forward") ||
            !profile.has("backward"))
            throw std::invalid_argument("kernel world descriptor profile");
        offline_world::Input input;
        input.pos = offline_world::Point(motion[2], motion[3], motion[4]);
        input.yaw = motion[9] ? field(state, sf::yaw) : motion[5];
        input.speed = motion[6];
        input.dt = motion[7];
        input.has_motion = motion[9] != 0;
        input.motion = motion[5] + (input.speed < 0 ? 3.141592653589793 : 0);
        input.airborne = flag(state, sf::airborne);
        input.pitch = state.has(sf::terrain_pitch) ? state.get(sf::terrain_pitch).number()
                                                   : field(state, sf::pitch);
        input.roll = field(state, sf::roll);
        input.hw = extents[0].number();
        input.back = extents[1].number();
        input.front = extents[2].number();
        bool driving = integer(state, sf::movement_dir) * input.speed > 0;
        bool turning = integer(state, sf::rotation_dir) != 0 || std::abs(turn_speed) > .01;
        bool reuse = !input.airborne && driving && !turning && motion[20] != 0;
        bool crush = !input.airborne && driving;
        bool commit = !input.has_motion || motion[10] != 0;
        double kinetic =
            crush ? (input.speed < 0 ? -field(profile, "backward") : field(profile, "forward")) : 0;
        // The reason is only a diagnostic classification, never a stop command.
        double reason = input.airborne ? 1 : !driving ? 2 : turning ? 3 : 0;
        double begin[] = {input.pos.x,   input.pos.y,   input.pos.z, input.yaw,
                          input.speed,   input.dt,      motion[8],   double(input.has_motion),
                          input.motion,  double(crush), kinetic,     double(commit),
                          double(reuse), reason};
        auto initial = call(0, begin, 14);
        if ((initial[0] != 0 && initial[0] != 1) || (initial[1] != 0 && initial[1] != 1))
            throw std::invalid_argument("kernel world begin answer");
        if (initial[0] != 0)
            return 0; // The exact corridor and current catalog both allow reuse.
        bool has_catalog = initial[1] != 0;
        int world_status;
        {
            OfflineQueryRoute current(callback, this);
            route = &current;
            world_status = offline_world::sweep(input, true);
            route = nullptr;
        }
        // Catalog calls and their native side effects keep source order. A
        // later Bot cannot observe this pose before the resolver completes.
        int operation = world_status == 0 && has_catalog                      ? 1
                        : world_status != 0 && has_catalog && !input.airborne ? 2
                                                                              : 0;
        double finish[] = {double(operation)};
        auto detail = call(2, finish, 1);
        if (operation < 2) {
            if (detail[0] != 0 && detail[0] != 1)
                throw std::invalid_argument("kernel world contact answer");
        } else {
            if (!std::isfinite(detail[0]) || detail[0] != std::floor(detail[0]) || detail[0] < 0 ||
                detail[0] > 5 || detail[0] == 3)
                throw std::invalid_argument("kernel world catalog answer");
            for (size_t i = 1; i < 4; ++i)
                if (detail[i] != 0 && detail[i] != 1)
                    throw std::invalid_argument("kernel world catalog flags");
        }
        if (world_status == 0)
            return detail[0] != 0 ? 2 : 4;
        if (!has_catalog || input.airborne)
            return world_status == 1 ? 0 : 4;
        int status = static_cast<int>(detail[0]);
        if (status == 4)
            return 4;
        bool accepted = detail[1] != 0, kinetic_used = detail[2] != 0;
        if (kinetic_used && !(accepted && status == 1 && detail[3] != 0))
            throw std::runtime_error("bot cap-crush receipt is inconsistent");
        if (accepted && (status == 0 || status == 5 || status == 2))
            throw std::runtime_error("bot contact receipt is inconsistent");
        if (status == 5)
            return 0; // A lookahead approach is not physical contact.
        return accepted && kinetic_used ? 3 : status;
    }
};
} // namespace offline_kernel
#endif
