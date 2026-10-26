#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "wot/vehicle_physics.hpp"

#include <memory>

BW_BEGIN_NAMESPACE

// Ground-placement rays use the original non-destructive SceneObstaclesCollider
// branch. Authority sweeps/destruction have different contracts and owners.
std::unique_ptr<wot::VehicleCollisionProvider> makeStockGroundCollisionProvider();

BW_END_NAMESPACE
