// Exercise storage semantics independently of the gameplay port. Run with
// ASan/UBSan as well as the host compiler; no Python or native engine required.
#include "kernel_value.h"
#include <cassert>
#include <iostream>
#include <random>
#include <set>

using namespace offline_kernel;

int main() {
    std::mt19937 random(1513);
    Value record = Value::record();
    std::map<std::string, Value> oracle;
    std::set<std::string> names;
    for (int slot = 0; slot < sf::count; ++slot) {
        std::string name = field_name(slot);
        assert(names.insert(name).second);
        assert(field_slot(name) == slot);
        assert(field_string(slot) == name);
    }
    assert(field_slot("unknown-descriptor-field") == -1);
    for (int step = 0; step < 20000; ++step) {
        int slot = static_cast<int>(random() % sf::count);
        std::string name = step % 7 ? field_name(slot) : "extra-" + std::to_string(slot);
        Field key{field_slot(name), name.c_str()};
        if (step % 5 == 0) {
            record.erase(key);
            oracle.erase(name);
        } else {
            Value value = step % 3 ? Value(step) : Value();
            if (step % 2)
                record[key] = value;
            else
                record[name] = value;
            oracle[name] = value;
        }
        assert(record.size() == oracle.size());
        assert(record.truth() == !oracle.empty());
        assert(record.has(name) == (oracle.count(name) != 0));
        assert(record.has(key) == record.has(name));
        assert(record.get(key) == (oracle.count(name) ? oracle.at(name) : Value()));
        if (step % 173 == 0) {
            Value mapping = Value::object();
            for (const auto &item : oracle)
                mapping[item.first] = item.second;
            assert(record == mapping && mapping == record);
            assert(JsonReader(json(record)).read() == mapping);
            assert(mapping.as_record() == record);
            std::map<std::string, Value> visited;
            record.visit([&](const std::string &field, const Value &value) {
                assert(visited.emplace(field, value).second);
            });
            assert(visited == oracle);
        }
    }
    record[sf::x] = Value();
    assert(record.has(sf::x) && record.get(sf::x).kind == Value::Null);
    Value omitted = record.copy();
    omitted.erase(sf::x);
    assert(omitted != record && !omitted.has(sf::x));
    omitted.erase("x");
    assert(!omitted.has(sf::x));
    Value &stable = record[sf::x];
    record[sf::position] = JsonReader("[1,2,3]").read();
    Value alias = record, shallow = record.copy(), deep = record.clone();
    for (int i = 0; i < 2000; ++i)
        record["new-" + std::to_string(i)] = Value(i);
    stable = Value(1513);
    assert(alias.get(sf::x).exact() == 1513);
    assert(shallow.get(sf::x).kind == Value::Null);
    assert(deep.get(sf::x).kind == Value::Null);
    shallow[sf::position][size_t(1)] = Value(42);
    assert(record.get(sf::position)[1].exact() == 42);
    assert(deep.get(sf::position)[1].exact() == 2);
    for (bool dense : {false, true}) {
        Value destination = dense ? Value::record() : Value::object();
        destination["keep"] = Value(7);
        destination.assign_fields(record);
        assert(destination.get("keep").exact() == 7);
        destination.erase("keep");
        assert(destination == record);
        destination.assign_fields(destination);
        assert(destination == record);
        destination.erase(sf::position);
        assert(record.has(sf::position));
    }
    // Conversion and aliasing do not depend on the source object's lifetime.
    Value retained;
    {
        Value temporary = JsonReader("{\"x\":1,\"extra\":{\"nested\":2}}").read();
        retained = temporary.as_record();
    }
    assert(retained.get(sf::x).exact() == 1);
    assert(retained.get("extra").get("nested").exact() == 2);
    // JSON permits embedded NULs in irregular keys. They are not C strings.
    for (bool dense : {false, true}) {
        Value embedded = JsonReader("{\"x\\u0000extra\":4,\"x\":9}").read();
        if (dense)
            embedded = embedded.as_record();
        std::string name("x\0extra", 7);
        Field key{field_slot(name), name.c_str(), name.size()};
        assert(embedded.size() == 2 && embedded.get(key).exact() == 4);
        assert(embedded.get(name).exact() == 4);
        assert(embedded.has(key) && embedded.has(name));
        assert(JsonReader(json(embedded)).read() == embedded);
        embedded.erase(key);
        assert(!embedded.has(name) && embedded.get(sf::x).exact() == 9);
    }
    bool rejected = false;
    try {
        Value().as_record();
    } catch (const std::invalid_argument &) {
        rejected = true;
    }
    assert(rejected);
    std::cout << "State storage: 20000 oracle mutations, presence, alias/copy/clone, "
                 "merge, stable references and JSON parity passed.\n";
}
