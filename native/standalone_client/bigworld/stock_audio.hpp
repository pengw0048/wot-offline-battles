#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "math/matrix.hpp"
#include "resmgr/datasection.hpp"

BW_BEGIN_NAMESPACE

// Application lifetime, independent of Avatar/battle session lifetime. HWND is
// passed opaquely to keep Windows and SDK headers out of application consumers.
// Initialization returns true for an authored disabled preference as well;
// stockAudioReady alone reports whether the real SDK is running.
bool initialiseStockAudio(DataSectionPtr configuration, void* window);
bool stockAudioReady();
bool selectStockAudioBankSet(const BW::string& section);
bool setStockAudioAuxiliarySend(unsigned busID, float gain);
void tickStockAudio(const Matrix& cameraWorld, float elapsed);
void finiStockAudio();

BW_END_NAMESPACE
