#ifndef OFFLINE_EXPERIMENT_KERNEL_VALUE_H
#define OFFLINE_EXPERIMENT_KERNEL_VALUE_H

// Round/configuration and publication values. Hot pose and gun fields have
// typed owners; this tree carries the irregular pinned descriptor/ledger data.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <iomanip>
#include <map>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unordered_map>

namespace offline_kernel {
struct Value;
struct Container;
struct Value {
    enum Kind { Null, Boolean, Integer, Real, String, Array, Object } kind;
    int64_t integer;
    double real;
    std::shared_ptr<Container> data;
    Value() : kind(Null), integer(0), real(0) {}
    explicit Value(bool v) : kind(Boolean), integer(v), real(0) {}
    explicit Value(int v) : kind(Integer), integer(v), real(0) {}
    explicit Value(int64_t v) : kind(Integer), integer(v), real(0) {}
    explicit Value(double v) : kind(Real), integer(0), real(v) {}
    explicit Value(const std::string &v);
    explicit Value(const char *v) : Value(std::string(v)) {}
    static Value array();
    static Value object();
    bool has(const std::string &key) const;
    const Value &get(const std::string &key) const;
    Value &operator[](const std::string &key);
    const Value &operator[](size_t i) const;
    Value &operator[](size_t i);
    void erase(const std::string &key);
    size_t size() const;
    void append(const Value &v);
    bool truth() const;
    double number(double fallback = 0) const;
    int64_t exact(int64_t fallback = 0) const;
    std::string text(const std::string &fallback = "") const;
    Value copy() const;
    Value clone() const;
    bool operator==(const Value &other) const;
    bool operator!=(const Value &other) const { return !(*this == other); }
};
struct Container {
    std::string string;
    std::vector<Value> array;
    std::unordered_map<std::string, Value> object;
};
inline Value::Value(const std::string &v)
    : kind(String), integer(0), real(0), data(new Container()) {
    data->string = v;
}
inline Value Value::array() {
    Value v;
    v.kind = Array;
    v.data.reset(new Container());
    return v;
}
inline Value Value::object() {
    Value v;
    v.kind = Object;
    v.data.reset(new Container());
    return v;
}
inline bool Value::has(const std::string &key) const {
    return kind == Object && data->object.count(key);
}
inline const Value &Value::get(const std::string &key) const {
    static const Value missing;
    if (kind != Object)
        return missing;
    auto i = data->object.find(key);
    return i == data->object.end() ? missing : i->second;
}
inline Value &Value::operator[](const std::string &key) {
    if (kind != Object)
        throw std::invalid_argument("kernel value is not a mapping");
    return data->object[key];
}
inline const Value &Value::operator[](size_t i) const {
    if (kind != Array || i >= data->array.size())
        throw std::out_of_range("kernel array index");
    return data->array[i];
}
inline Value &Value::operator[](size_t i) {
    if (kind != Array || i >= data->array.size())
        throw std::out_of_range("kernel array index");
    return data->array[i];
}
inline void Value::erase(const std::string &key) {
    if (kind == Object)
        data->object.erase(key);
}
inline size_t Value::size() const {
    return kind == Array    ? data->array.size()
           : kind == Object ? data->object.size()
           : kind == String ? data->string.size()
                            : 0;
}
inline void Value::append(const Value &v) {
    if (kind != Array)
        throw std::invalid_argument("kernel array append");
    data->array.push_back(v);
}
inline bool Value::truth() const {
    if (kind == Null)
        return false;
    if (kind == Boolean || kind == Integer)
        return integer != 0;
    if (kind == Real)
        return real != 0;
    return size() != 0;
}
inline double Value::number(double fallback) const {
    if (kind == Real)
        return real;
    if (kind == Integer || kind == Boolean)
        return static_cast<double>(integer);
    if (kind == String) {
        char *end = nullptr;
        double v = std::strtod(data->string.c_str(), &end);
        if (end != data->string.c_str() && !*end)
            return v;
    }
    return fallback;
}
inline int64_t Value::exact(int64_t fallback) const {
    if (kind == Integer || kind == Boolean)
        return integer;
    if (kind == Real && std::isfinite(real) && real >= -9007199254740991.0 &&
        real <= 9007199254740991.0)
        return static_cast<int64_t>(real);
    return fallback;
}
inline std::string Value::text(const std::string &fallback) const {
    return kind == String ? data->string : fallback;
}
inline Value Value::copy() const {
    Value v = *this;
    if (data)
        v.data.reset(new Container(*data));
    return v;
}
inline Value Value::clone() const {
    Value v = copy();
    if (kind == Array)
        for (Value &item : v.data->array)
            item = item.clone();
    else if (kind == Object)
        for (auto &item : v.data->object)
            item.second = item.second.clone();
    return v;
}
inline bool Value::operator==(const Value &o) const {
    if (kind <= Real && o.kind <= Real) {
        if (kind == Null || o.kind == Null)
            return kind == o.kind;
        return number() == o.number();
    }
    if (kind != o.kind)
        return false;
    if (kind == String)
        return data->string == o.data->string;
    if (kind == Array)
        return data->array == o.data->array;
    if (kind == Object)
        return data->object == o.data->object;
    return false;
}
inline void utf8(std::string &s, unsigned c) {
    if (c <= 0x7f)
        s.push_back(static_cast<char>(c));
    else if (c <= 0x7ff) {
        s.push_back(static_cast<char>(0xc0 | (c >> 6)));
        s.push_back(static_cast<char>(0x80 | (c & 63)));
    } else if (c <= 0xffff) {
        s.push_back(static_cast<char>(0xe0 | (c >> 12)));
        s.push_back(static_cast<char>(0x80 | ((c >> 6) & 63)));
        s.push_back(static_cast<char>(0x80 | (c & 63)));
    } else {
        s.push_back(static_cast<char>(0xf0 | (c >> 18)));
        s.push_back(static_cast<char>(0x80 | ((c >> 12) & 63)));
        s.push_back(static_cast<char>(0x80 | ((c >> 6) & 63)));
        s.push_back(static_cast<char>(0x80 | (c & 63)));
    }
}
struct JsonReader {
    const std::string &source;
    size_t at = 0;
    unsigned depth = 0;
    explicit JsonReader(const std::string &s) : source(s) {}
    void space() {
        while (at < source.size() && (source[at] == ' ' || source[at] == '\r' ||
                                      source[at] == '\n' || source[at] == '\t'))
            ++at;
    }
    char take() {
        if (at >= source.size())
            throw std::invalid_argument("truncated kernel JSON");
        return source[at++];
    }
    unsigned hex() {
        unsigned n = 0;
        for (int i = 0; i < 4; ++i) {
            char c = take();
            unsigned v = c >= '0' && c <= '9'   ? c - '0'
                         : c >= 'a' && c <= 'f' ? c - 'a' + 10
                         : c >= 'A' && c <= 'F' ? c - 'A' + 10
                                                : 16;
            if (v == 16)
                throw std::invalid_argument("kernel JSON unicode");
            n = (n << 4) | v;
        }
        return n;
    }
    std::string string() {
        if (take() != '"')
            throw std::invalid_argument("kernel JSON string");
        std::string s;
        for (;;) {
            unsigned char c = static_cast<unsigned char>(take());
            if (c == '"')
                return s;
            if (c < 32)
                throw std::invalid_argument("kernel JSON control");
            if (c != '\\') {
                s.push_back(static_cast<char>(c));
                continue;
            }
            c = static_cast<unsigned char>(take());
            if (c == '"' || c == '\\' || c == '/')
                s.push_back(static_cast<char>(c));
            else if (c == 'b')
                s.push_back('\b');
            else if (c == 'f')
                s.push_back('\f');
            else if (c == 'n')
                s.push_back('\n');
            else if (c == 'r')
                s.push_back('\r');
            else if (c == 't')
                s.push_back('\t');
            else if (c == 'u') {
                unsigned cp = hex();
                if (cp >= 0xd800 && cp <= 0xdbff) {
                    if (take() != '\\' || take() != 'u')
                        throw std::invalid_argument("kernel JSON surrogate");
                    unsigned low = hex();
                    if (low < 0xdc00 || low > 0xdfff)
                        throw std::invalid_argument("kernel JSON surrogate");
                    cp = 0x10000 + ((cp - 0xd800) << 10) + (low - 0xdc00);
                } else if (cp >= 0xdc00 && cp <= 0xdfff)
                    throw std::invalid_argument("kernel JSON surrogate");
                utf8(s, cp);
            } else
                throw std::invalid_argument("kernel JSON escape");
        }
    }
    Value value() {
        space();
        if (++depth > 128)
            throw std::invalid_argument("kernel JSON nesting");
        if (at >= source.size())
            throw std::invalid_argument("kernel JSON value");
        char c = source[at];
        Value v;
        if (c == '"')
            v = Value(string());
        else if (c == '[' || c == '{') {
            ++at;
            v = c == '[' ? Value::array() : Value::object();
            space();
            char close = c == '[' ? ']' : '}';
            if (at < source.size() && source[at] == close)
                ++at;
            else
                for (;;) {
                    space();
                    if (c == '[')
                        v.append(value());
                    else {
                        std::string key = string();
                        space();
                        if (take() != ':')
                            throw std::invalid_argument("kernel JSON colon");
                        if (v.has(key))
                            throw std::invalid_argument("duplicate kernel JSON key");
                        v[key] = value();
                    }
                    space();
                    char sep = take();
                    if (sep == close)
                        break;
                    if (sep != ',')
                        throw std::invalid_argument("kernel JSON separator");
                }
        } else if (source.compare(at, 4, "null") == 0)
            at += 4;
        else if (source.compare(at, 4, "true") == 0) {
            v = Value(true);
            at += 4;
        } else if (source.compare(at, 5, "false") == 0) {
            v = Value(false);
            at += 5;
        } else {
            size_t start = at;
            if (source[at] == '-')
                ++at;
            if (at >= source.size() || source[at] < '0' || source[at] > '9')
                throw std::invalid_argument("kernel JSON number");
            if (source[at] == '0')
                ++at;
            else
                while (at < source.size() && source[at] >= '0' && source[at] <= '9')
                    ++at;
            bool floating = false;
            if (at < source.size() && source[at] == '.') {
                floating = true;
                ++at;
                size_t digits = at;
                while (at < source.size() && source[at] >= '0' && source[at] <= '9')
                    ++at;
                if (at == digits)
                    throw std::invalid_argument("kernel JSON fraction");
            }
            if (at < source.size() && (source[at] == 'e' || source[at] == 'E')) {
                floating = true;
                ++at;
                if (at < source.size() && (source[at] == '+' || source[at] == '-'))
                    ++at;
                size_t digits = at;
                while (at < source.size() && source[at] >= '0' && source[at] <= '9')
                    ++at;
                if (at == digits)
                    throw std::invalid_argument("kernel JSON exponent");
            }
            std::string raw = source.substr(start, at - start);
            char *end = nullptr;
            double n = std::strtod(raw.c_str(), &end);
            if (!std::isfinite(n) || !end || *end)
                throw std::invalid_argument("kernel JSON numeric range");
            if (!floating && n >= -9007199254740991.0 && n <= 9007199254740991.0)
                v = Value(static_cast<int64_t>(n));
            else
                v = Value(n);
        }
        --depth;
        return v;
    }
    Value read() {
        Value v = value();
        space();
        if (at != source.size())
            throw std::invalid_argument("kernel JSON suffix");
        return v;
    }
};
inline void write_string(std::ostream &out, const std::string &s) {
    static const char hex[] = "0123456789abcdef";
    out << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\')
            out << '\\' << static_cast<char>(c);
        else if (c < 32)
            out << "\\u00" << hex[c >> 4] << hex[c & 15];
        else
            out << static_cast<char>(c);
    }
    out << '"';
}
inline void write_json(std::ostream &out, const Value &v) {
    if (v.kind == Value::Null)
        out << "null";
    else if (v.kind == Value::Boolean)
        out << (v.integer ? "true" : "false");
    else if (v.kind == Value::Integer)
        out << v.integer;
    else if (v.kind == Value::Real) {
        if (!std::isfinite(v.real))
            throw std::domain_error("nonfinite kernel output");
        out << std::setprecision(17) << v.real;
    } else if (v.kind == Value::String)
        write_string(out, v.data->string);
    else if (v.kind == Value::Array) {
        out << '[';
        bool comma = false;
        for (const Value &item : v.data->array) {
            if (comma)
                out << ',';
            write_json(out, item);
            comma = true;
        }
        out << ']';
    } else {
        out << '{';
        bool comma = false;
        for (const auto &item : v.data->object) {
            if (comma)
                out << ',';
            write_string(out, item.first);
            out << ':';
            write_json(out, item.second);
            comma = true;
        }
        out << '}';
    }
}
inline std::string json(const Value &v) {
    std::ostringstream out;
    write_json(out, v);
    return out.str();
}
inline const std::vector<Value> &elements(const Value &v) {
    if (v.kind != Value::Array)
        throw std::invalid_argument("kernel array required");
    return v.data->array;
}
inline double field(const Value &v, const char *name, double fallback = 0) {
    return v.get(name).number(fallback);
}
inline int integer(const Value &v, const char *name, int fallback = 0) {
    return static_cast<int>(v.get(name).exact(fallback));
}
inline bool flag(const Value &v, const char *name, bool fallback = false) {
    return v.has(name) ? v.get(name).truth() : fallback;
}
inline double clamp(double x, double a, double b) { return std::max(a, std::min(b, x)); }
inline double rounded(double v, int digits) {
    // CPython 2.7 rounds the binary value to decimal, correcting true ties
    // away from zero. Multiplying first can create a false halfway value.
    if (!std::isfinite(v) || digits < 0 || digits > 6)
        throw std::invalid_argument("kernel decimal rounding");
    int exponent = 0;
    double fraction = std::frexp(std::abs(v), &exponent);
    uint64_t mantissa = static_cast<uint64_t>(std::ldexp(fraction, 53));
    exponent -= 53;
    while (mantissa && !(mantissa & 1)) {
        mantissa >>= 1;
        ++exponent;
    }
    if (mantissa && exponent + digits == -1) {
        double scale = std::pow(10.0, digits);
        return std::round(v * scale) / scale;
    }
    char output[384];
    int n = std::snprintf(output, sizeof(output), "%.*f", digits, v);
    if (n < 0 || static_cast<size_t>(n) >= sizeof(output))
        throw std::invalid_argument("kernel decimal width");
    return std::strtod(output, nullptr);
}
} // namespace offline_kernel
#endif
