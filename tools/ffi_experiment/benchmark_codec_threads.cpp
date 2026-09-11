// Host-only parallelism probe. No Python objects, query bridge, engine calls or
// state mutation are reachable from the helper. Run each mode in a fresh process
// because creating a thread can change the standard library's reference counts.
#include "kernel_codec.h"
#include <chrono>
#include <condition_variable>
#include <ctime>
#include <fstream>
#include <iostream>
#include <mutex>
#include <thread>

using namespace offline_kernel;

int main(int argc, char **argv) {
    if (argc != 4)
        throw std::invalid_argument("usage: benchmark_codec_threads fixture.json workers batches");
    int workers = std::stoi(argv[2]), batches = std::stoi(argv[3]);
    if ((workers != 1 && workers != 2) || batches <= 0)
        throw std::invalid_argument("workers must be 1 or 2; batches must be positive");
    std::ifstream input(argv[1]);
    if (!input)
        throw std::invalid_argument("cannot read codec fixture");
    std::string source((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    Value fixture = JsonReader(source).read();
    Codec codec(fixture.get("codec"));
    std::vector<Value> states, rows;
    for (const Value &state : elements(fixture.get("states")))
        states.push_back(state.as_record());
    if (states.empty() || states.size() != fixture.get("rows").size())
        throw std::invalid_argument("codec fixture row count");
    rows.resize(states.size());
    std::vector<std::exception_ptr> errors(states.size());
    const size_t split = workers == 1 ? states.size() : (states.size() + 1) / 2;
    auto encode = [&](size_t start, size_t end) {
        for (size_t i = start; i < end; ++i) {
            errors[i] = nullptr;
            try {
                rows[i] = codec.row(states[i]);
            } catch (...) {
                errors[i] = std::current_exception();
            }
        }
    };
    std::mutex mutex;
    std::condition_variable ready, completed;
    bool stopping = false;
    unsigned issued = 0, finished = 0;
    std::thread helper;
    if (workers == 2)
        helper = std::thread([&]() {
            std::unique_lock<std::mutex> lock(mutex);
            for (;;) {
                ready.wait(lock, [&]() { return stopping || issued != finished; });
                if (stopping)
                    return;
                unsigned job = issued;
                lock.unlock();
                encode(split, states.size());
                lock.lock();
                finished = job;
                completed.notify_one();
            }
        });
    auto batch = [&]() {
        if (workers == 2) {
            std::lock_guard<std::mutex> lock(mutex);
            ++issued;
            ready.notify_one();
        }
        encode(0, split);
        if (workers == 2) {
            std::unique_lock<std::mutex> lock(mutex);
            completed.wait(lock, [&]() { return finished == issued; });
        }
    };
    auto verify = [&]() {
        for (size_t i = 0; i < rows.size(); ++i) {
            if (errors[i])
                std::rethrow_exception(errors[i]);
            if (rows[i] != fixture.get("rows")[i])
                throw std::runtime_error("threaded row differs from Python oracle");
        }
    };
    double wall = 0, cpu = 0;
    std::exception_ptr failure;
    try {
        for (int i = 0; i < 20; ++i)
            batch();
        verify();
        auto start = std::chrono::steady_clock::now();
        std::clock_t cpu_start = std::clock();
        for (int i = 0; i < batches; ++i)
            batch();
        cpu = static_cast<double>(std::clock() - cpu_start) / CLOCKS_PER_SEC;
        wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        verify();
        // Both partitions finish even when a row fails. A later batch recovers;
        // the caller can select the first error in original row order.
        Value first = states.front(), last = states.back();
        states.front() = Value();
        states.back() = Value();
        batch();
        if (!errors.front() || !errors.back())
            throw std::runtime_error("row failure was lost");
        states.front() = first;
        states.back() = last;
        batch();
        verify();
    } catch (...) {
        failure = std::current_exception();
    }
    if (workers == 2) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            stopping = true;
            ready.notify_one();
        }
        helper.join();
    }
    if (failure)
        std::rethrow_exception(failure);
    std::cout << std::setprecision(12) << "{\"workers\":" << workers
              << ",\"rows\":" << states.size() << ",\"batches\":" << batches
              << ",\"wall_seconds\":" << wall << ",\"cpu_seconds\":" << cpu
              << ",\"python_row_parity\":true,\"failure_recovery\":true}\n";
}
