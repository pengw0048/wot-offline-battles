#pragma once

#include "math/matrix.hpp"

BW_BEGIN_NAMESPACE
extern int StockMath_token;
Matrix stockSlerp( const Matrix & source, const Matrix & target, float amount );
BW_END_NAMESPACE
