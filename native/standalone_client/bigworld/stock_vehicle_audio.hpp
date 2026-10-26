#pragma once

#include "cstdmf/bw_namespace.hpp"

BW_BEGIN_NAMESPACE
extern int StockVehicleAudio_token;
// Original Svarog phase 1, separate from the SDK's Device/App draw update.
void tickStockDetailedEngineStates(float frameTime);
// Original TaskScheduler runs at the end of Scene tick (0x00637c60).
void tickStockVehicleAudioTasks();
void finiStockVehicleAudio();
BW_END_NAMESPACE
