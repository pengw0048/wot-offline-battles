#pragma once
#include "cstdmf/bw_namespace.hpp"

BW_BEGIN_NAMESPACE
class TaskManager;
namespace Mercury { class EventDispatcher; }
bool initialiseStockPinger(Mercury::EventDispatcher& dispatcher, TaskManager& tasks);
void finiStockPinger();
BW_END_NAMESPACE
