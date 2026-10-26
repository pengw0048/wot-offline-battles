#pragma once

#include "pyscript/script.hpp"

BW_BEGIN_NAMESPACE
// The reviewed direct side effects of 00971070. This constituent deliberately
// does not export wg_initCustomSettings before all its settings are restored.
void initialiseStockGraphicsConfiguration();
BW_END_NAMESPACE
