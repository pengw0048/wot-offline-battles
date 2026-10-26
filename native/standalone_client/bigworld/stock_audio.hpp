#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "math/matrix.hpp"
#include "resmgr/datasection.hpp"

BW_BEGIN_NAMESPACE

// Application lifetime, independent of Avatar/battle session lifetime. HWND is
// passed opaquely to keep Windows and SDK headers out of application consumers.
bool initialiseStockAudio(DataSectionPtr configuration, void* window);
bool stockAudioReady();
bool selectStockAudioBankSet(const BW::string& section);
bool setStockAudioAuxiliarySend(unsigned busID, float gain);
void tickStockAudio(const Matrix& cameraWorld, float elapsed);
void finiStockAudio();

BW_END_NAMESPACE
