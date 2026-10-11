#ifndef WOT_OFFLINE_ASYNC_WORKER_H
#define WOT_OFFLINE_ASYNC_WORKER_H
#include <functional>
namespace offline_async {
// Every task owns its data. The executor never enters Python or BigWorld.
void schedule(std::function<void()> task);
unsigned worker_count();
}
#endif
