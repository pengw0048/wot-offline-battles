#ifndef OFFLINE_EXPERIMENT_KERNEL_GUNNERY_H
#define OFFLINE_EXPERIMENT_KERNEL_GUNNERY_H
#include "kernel_bot.h"
#include <array>

namespace offline_kernel {
// Independent MT19937 implementation with the CPython 2.7 integer-seed and
// 53-bit output contract. A fresh generator owns each accepted shot/aim epoch.
struct Random {
    std::array<uint32_t, 624> words;
    size_t index = 624;
    bool spare_ready = false;
    double spare = 0;
    explicit Random(uint32_t seed) {
        words[0] = 19650218U;
        for (size_t i = 1; i < 624; ++i)
            words[i] =
                1812433253U * (words[i - 1] ^ (words[i - 1] >> 30)) + static_cast<uint32_t>(i);
        size_t i = 1;
        for (size_t k = 0; k < 624; ++k) {
            words[i] = (words[i] ^ ((words[i - 1] ^ (words[i - 1] >> 30)) * 1664525U)) + seed;
            if (++i == 624) {
                words[0] = words[623];
                i = 1;
            }
        }
        for (size_t k = 0; k < 623; ++k) {
            words[i] = (words[i] ^ ((words[i - 1] ^ (words[i - 1] >> 30)) * 1566083941U)) -
                       static_cast<uint32_t>(i);
            if (++i == 624) {
                words[0] = words[623];
                i = 1;
            }
        }
        words[0] = 0x80000000U;
    }
    uint32_t next() {
        if (index == 624) {
            for (size_t i = 0; i < 624; ++i) {
                uint32_t mixed = (words[i] & 0x80000000U) | (words[(i + 1) % 624] & 0x7fffffffU);
                words[i] = words[(i + 397) % 624] ^ (mixed >> 1) ^ ((mixed & 1) ? 0x9908b0dfU : 0);
            }
            index = 0;
        }
        uint32_t v = words[index++];
        v ^= v >> 11;
        v ^= (v << 7) & 0x9d2c5680U;
        v ^= (v << 15) & 0xefc60000U;
        return v ^ (v >> 18);
    }
    double random() {
        uint32_t a = next() >> 5, b = next() >> 6;
        return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
    }
    double uniform(double a, double b) { return a + (b - a) * random(); }
    double gauss(double mean, double sigma) {
        double z = spare;
        if (spare_ready)
            spare_ready = false;
        else {
            double theta = random() * 6.283185307179586,
                   radial = std::sqrt(-2.0 * std::log(1.0 - random()));
            z = std::cos(theta) * radial;
            spare = std::sin(theta) * radial;
            spare_ready = true;
        }
        return mean + z * sigma;
    }
};
inline uint32_t rotate_left(uint32_t v, unsigned n) { return (v << n) | (v >> (32 - n)); }
// Only the first SHA-1 word is used by the source seed contract.
inline uint32_t seed_hash(const std::string &text) {
    std::vector<unsigned char> bytes(text.begin(), text.end());
    uint64_t bits = static_cast<uint64_t>(bytes.size()) * 8;
    bytes.push_back(128);
    while (bytes.size() % 64 != 56)
        bytes.push_back(0);
    for (int i = 7; i >= 0; --i)
        bytes.push_back(static_cast<unsigned char>(bits >> (i * 8)));
    std::array<uint32_t, 5> h = {{0x67452301U, 0xefcdab89U, 0x98badcfeU, 0x10325476U, 0xc3d2e1f0U}};
    for (size_t offset = 0; offset < bytes.size(); offset += 64) {
        std::array<uint32_t, 80> w;
        for (size_t i = 0; i < 16; ++i) {
            w[i] = 0;
            for (size_t j = 0; j < 4; ++j)
                w[i] = (w[i] << 8) | bytes[offset + 4 * i + j];
        }
        for (size_t i = 16; i < 80; ++i)
            w[i] = rotate_left(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4];
        for (size_t i = 0; i < 80; ++i) {
            uint32_t f, k;
            if (i < 20) {
                f = (b & c) | ((~b) & d);
                k = 0x5a827999U;
            } else if (i < 40) {
                f = b ^ c ^ d;
                k = 0x6ed9eba1U;
            } else if (i < 60) {
                f = (b & c) | (b & d) | (c & d);
                k = 0x8f1bbcdcU;
            } else {
                f = b ^ c ^ d;
                k = 0xca62c1d6U;
            }
            uint32_t t = rotate_left(a, 5) + f + e + k + w[i];
            e = d;
            d = c;
            c = rotate_left(b, 30);
            b = a;
            a = t;
        }
        h[0] += a;
        h[1] += b;
        h[2] += c;
        h[3] += d;
        h[4] += e;
    }
    return h[0] & 0x7fffffffU;
}
inline Value vector3(double a, double b, double c) {
    Value v = Value::array(3);
    v.append(Value(a));
    v.append(Value(b));
    v.append(Value(c));
    return v;
}
inline double gun_wrap(double v) {
    while (v > 3.141592653589793)
        v -= 6.283185307179586;
    while (v < -3.141592653589793)
        v += 6.283185307179586;
    return v;
}
inline Value barrel(double yaw, double pitch) {
    return vector3(std::sin(yaw) * std::cos(pitch), -std::sin(pitch),
                   std::cos(yaw) * std::cos(pitch));
}
inline Value rotate_x(const Value &v, double angle) {
    double s = std::sin(angle), c = std::cos(angle);
    return vector3(v[0].number(), c * v[1].number() - s * v[2].number(),
                   s * v[1].number() + c * v[2].number());
}
inline Value rotate_y(const Value &v, double angle) {
    double s = std::sin(angle), c = std::cos(angle);
    return vector3(c * v[0].number() + s * v[2].number(), v[1].number(),
                   -s * v[0].number() + c * v[2].number());
}
inline Value rotate_z(const Value &v, double angle) {
    double s = std::sin(angle), c = std::cos(angle);
    return vector3(c * v[0].number() - s * v[1].number(), s * v[0].number() + c * v[1].number(),
                   v[2].number());
}
inline Value world_barrel(const Value &s) {
    double yaw = field(s, sf::yaw), pitch = field(s, sf::pitch), roll = field(s, sf::roll);
    if (std::abs(pitch) <= 1e-12 && std::abs(roll) <= 1e-12)
        return barrel(s.has(sf::turret_yaw) ? gun_wrap(yaw + field(s, sf::turret_yaw))
                                            : field(s, sf::aim_yaw, yaw),
                      field(s, sf::gun_pitch));
    double turret = s.has(sf::turret_yaw) ? field(s, sf::turret_yaw)
                                          : gun_wrap(field(s, sf::aim_yaw, yaw) - yaw);
    return rotate_y(rotate_x(rotate_z(barrel(turret, field(s, sf::gun_pitch)), roll), pitch), yaw);
}
inline Value dispersal_base(const Value &s) {
    return std::abs(field(s, sf::pitch)) <= 1e-12 && std::abs(field(s, sf::roll)) <= 1e-12
               ? Value()
               : world_barrel(s);
}
inline Value velocity(const Value &v) {
    const Value &raw = v.get(sf::velocity);
    return raw.kind == Value::Array && raw.size() >= 3
               ? vector3(raw[0].number(), raw[1].number(), raw[2].number())
               : vector3(std::sin(field(v, sf::yaw)) * field(v, sf::speed), 0,
                         std::cos(field(v, sf::yaw)) * field(v, sf::speed));
}
inline Value dispersed(int id, int round, int seq, double yaw, double pitch, double dispersion,
                       int burst = 0, int group = -1, const Value &base = Value()) {
    if (!std::isfinite(dispersion) || dispersion <= 0)
        throw std::invalid_argument("kernel shot dispersion");
    std::array<double, 3> d = {
        {std::sin(yaw) * std::cos(pitch), -std::sin(pitch), std::cos(yaw) * std::cos(pitch)}};
    if (base.kind != Value::Null) {
        if (base.kind != Value::Array || base.size() != 3)
            throw std::invalid_argument("kernel barrel direction");
        double length = 0;
        for (size_t i = 0; i < 3; ++i) {
            d[i] = base[i].number();
            if (!std::isfinite(d[i]))
                throw std::invalid_argument("kernel barrel direction");
            length += d[i] * d[i];
        }
        length = std::sqrt(length);
        if (length <= 1e-12)
            throw std::invalid_argument("kernel barrel direction");
        for (double &v : d)
            v /= length;
    }
    uint64_t seed = (static_cast<uint64_t>(round & 0xffff) * 1000003 +
                     static_cast<uint64_t>(id & 0xffff) * 9176 +
                     static_cast<uint64_t>((group < 0 ? seq : group) & 0x7fffffff) * 6113 +
                     static_cast<uint64_t>(burst & 0xffff) * 3571) &
                    0x7fffffff;
    Random rng(static_cast<uint32_t>(seed));
    double radius = std::abs(rng.gauss(0, dispersion / 2));
    if (radius > dispersion)
        radius = dispersion * rng.uniform(0, 1);
    double azimuth = rng.uniform(0, 6.283185307179586);
    std::array<double, 3> reference = {{0, 0, 0}};
    reference[std::abs(d[0]) <= std::abs(d[1]) && std::abs(d[0]) <= std::abs(d[2]) ? 0
              : std::abs(d[1]) <= std::abs(d[2])                                   ? 1
                                                                                   : 2] = 1;
    auto cross = [](const std::array<double, 3> &a, const std::array<double, 3> &b) {
        return std::array<double, 3>{
            {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]}};
    };
    auto tangent = cross(d, reference);
    double norm =
        std::sqrt(tangent[0] * tangent[0] + tangent[1] * tangent[1] + tangent[2] * tangent[2]);
    for (double &v : tangent)
        v /= norm;
    auto up = cross(d, tangent);
    for (size_t i = 0; i < 3; ++i)
        d[i] = d[i] * std::cos(radius) +
               (tangent[i] * std::cos(azimuth) + up[i] * std::sin(azimuth)) * std::sin(radius);
    Value result = Value::array();
    result.append(Value(std::atan2(d[0], d[2])));
    result.append(Value(std::atan2(d[1], std::max(1e-9, std::sqrt(d[0] * d[0] + d[2] * d[2])))));
    return result;
}
struct Gunnery {
    Value config, record;
    explicit Gunnery(const Value &v) : config(v) {}
    static Value target_key(const Value &v) {
        Value key = Value::array();
        key.append(Value(v.get(sf::kind).text("bot")));
        key.append(Value(integer(v, sf::network_id, integer(v, sf::id))));
        return key;
    }
    Value hold(const Value &state, const Value &target, double now) {
        if (target.kind != Value::Object)
            return Value();
        Value key = target_key(target);
        int seq = integer(state, sf::fire_seq);
        if (record.kind == Value::Null || record.get("key") != key) {
            record = Value::object();
            record["key"] = key;
            record["since"] = Value(now);
            record["laid_since"] = Value(now);
            record[sf::fire_seq] = Value(seq);
            record["fired"] = Value(false);
            record["epoch"] = Value();
            record["error"] = Value();
        }
        if (integer(record, sf::fire_seq) != seq) {
            record[sf::fire_seq] = Value(seq);
            record["laid_since"] = Value(now);
            record["fired"] = Value(true);
        }
        Value result = Value::array();
        result.append(Value(std::max(0.0, now - field(record, "since"))));
        result.append(Value(std::max(0.0, now - field(record, "laid_since"))));
        result.append(Value(!flag(record, "fired")));
        return result;
    }
    Value error(const Value &state, const Value &target, double now, int round) {
        Value held = hold(state, target, now);
        if (held.kind == Value::Null)
            return Value();
        int epoch = static_cast<int>(held[0].number() / field(config, "epoch_seconds", 1.5));
        if (record.get("epoch").kind == Value::Null || integer(record, "epoch") != epoch ||
            record.get("error").kind == Value::Null) {
            const Value &key = record.get("key");
            std::string seed = "bot-gunner-v1|" + std::to_string(round) + "|" +
                               std::to_string(integer(state, sf::id)) + "|('" + key[0].text() +
                               "', " + std::to_string(key[1].exact()) + ")|" +
                               std::to_string(epoch);
            Random rng(seed_hash(seed));
            double radius = std::abs(rng.gauss(0, .5));
            if (radius > 1)
                radius = rng.random();
            Value result = Value::object();
            result["radius"] = Value(radius);
            result["azimuth"] = Value(rng.uniform(0, 6.283185307179586));
            result["lead_scale"] = Value(1 + rng.uniform(-1, 1) * field(config, "lead_error"));
            record["epoch"] = Value(epoch);
            record["error"] = result;
        }
        return record.get("error");
    }
    Value aimed(const Value &state, const Value &target, double now, int round, const Gun &gun) {
        if (target.kind != Value::Object)
            return target;
        Value e = error(state, target, now, round), p = target.get(sf::position);
        if (p.kind != Value::Array || p.size() != 3)
            p = vector3(field(target, sf::x), field(target, sf::y), field(target, sf::z));
        double dx = p[0].number() - field(state, sf::x), dz = p[2].number() - field(state, sf::z),
               horizontal = std::sqrt(dx * dx + dz * dz);
        if (horizontal <= 1)
            return target;
        double magnitude = std::min(field(config, "maximum_offset", 8),
                                    field(config, "aim_bias_factor") * gun.fully_aimed *
                                        horizontal * field(e, "radius"));
        double lateral = magnitude * std::cos(field(e, "azimuth")),
               vertical =
                   magnitude * std::sin(field(e, "azimuth")) * field(config, "vertical_share", .45),
               scale = field(e, "lead_scale");
        Value result = target.copy(), v = velocity(target);
        result[sf::position] =
            vector3(p[0].number() + dz / horizontal * lateral, p[1].number() + vertical,
                    p[2].number() - dx / horizontal * lateral);
        result[sf::velocity] =
            vector3(v[0].number() * scale, v[1].number() * scale, v[2].number() * scale);
        return result;
    }
    bool ready(const Value &state, const Value &target, double now, const Gun &gun) {
        Value held = hold(state, target, now);
        if (held.kind == Value::Null || held[0].number() < field(config, "reaction_seconds"))
            return false;
        return !held[2].truth() || gun.current_factor <= field(config, "converged_factor") ||
               held[1].number() >= field(config, "patience_seconds");
    }
};
} // namespace offline_kernel
#endif
