#include "offline_async_worker.h"
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>
namespace offline_async {
namespace {
class Executor {
    std::mutex mutex_;
    std::condition_variable wake_;
    std::deque<std::function<void()>> ready_;
    std::vector<std::thread> threads_;
    bool stopping_ = false;
    void run() {
        for (;;) {
            std::function<void()> task;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                wake_.wait(lock, [this] { return stopping_ || !ready_.empty(); });
                if (stopping_) return;
                task = std::move(ready_.front());
                ready_.pop_front();
            }
            try { task(); } catch (...) { /* Each service owns terminal errors. */ }
        }
    }
public:
    Executor() {
        try {
            threads_.reserve(2);
            for (unsigned index = 0; index < 2; ++index)
                threads_.emplace_back([this] { run(); });
        } catch (...) {
            // Construction runs in the first Python submit, outside DLL
            // detach. Roll back a partially created pool before std::thread's
            // destructor could terminate the process for a joinable thread.
            {
                std::lock_guard<std::mutex> lock(mutex_);
                stopping_ = true;
            }
            wake_.notify_all();
            for (auto &thread : threads_) if (thread.joinable()) thread.join();
            throw;
        }
    }
    void submit(std::function<void()> task) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (stopping_) return;
            ready_.push_back(std::move(task));
        }
        wake_.notify_one();
    }
};
Executor &executor() {
    // The extension and pool live for the process. Joining C++ workers from
    // a DLL static destructor can deadlock under Windows' loader lock. Round
    // close cancels owned jobs; process termination retires these two threads.
    static Executor *const value = new Executor;
    return *value;
}
}
void schedule(std::function<void()> task) { executor().submit(std::move(task)); }
unsigned worker_count() { return 2; }
}
