#pragma once

#include "cstdmf/stdmf.hpp"

BW_BEGIN_NAMESPACE
extern int StockDisplayModes_token;
void stockRememberDisplayWindow();
bool stockChangeVideoMode( int modeIndex, int windowMode );
void stockResizeWindow( int width, int height );
BW_END_NAMESPACE
