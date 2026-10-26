#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "math/vector3.hpp"
#include "pyscript/script.hpp"
#include "space/forward_declarations.hpp"

BW_BEGIN_NAMESPACE
class PyEntity;
// #1513 WGVehicleFilter 0x006f0610 tests four geometry edges, in source
// order. Endpoints contains eight world-space vectors, two per edge.
// A false return sets/preserves PyErr and leaves blocked unchanged. It is not
// an empty-world result. The weak owner must still belong to this space.
// Destructible materials require original item metadata; the public terrain
// preview does not expose it yet and reports that query as unavailable.
bool queryStockScenePrediction(SpaceID spaceID,const Vector3* endpoints,
    const WeakPyPtr<PyEntity>& owner,const Vector3& velocity,bool& blocked);

BW_END_NAMESPACE
