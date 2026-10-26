#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "pyscript/script_math.hpp"

BW_BEGIN_NAMESPACE
extern int StockVehicleVisuals_token;

// Svarog's component phases run in Device/App before Script/Camera/World.
void tickStockVehicleComponents( float frameTime );
// The separate World/App TrackScroll scheduler uses the game clock and
// dispatches once per 0.05 seconds without integrating an authority body.
void tickStockVehicleTrackScrolls( double gameTime );

// Native consumers share the original typed DataLink protocol. These reject
// mismatched link types without invoking arbitrary Python callables.
bool sampleStockBoolLink( PyObject * link, bool & value );
bool sampleStockFloatLink( PyObject * link, float & value );
bool sampleStockTranslationLink( PyObject * link, Vector3 & value );
BW_END_NAMESPACE
