#include "pch.hpp"
#include "stock_scene_prediction.hpp"
#include "stock_scene_identity.hpp"

#include "entity.hpp"
#include "py_entity.hpp"
#include "physics2/collision_callback.hpp"
#include "physics2/collision_obstacle.hpp"
#include "space/client_space.hpp"
#include "space/space_manager.hpp"

#include <cmath>

BW_BEGIN_NAMESPACE
namespace {
bool finite(const Vector3& value) {
    return std::isfinite(value.x) && std::isfinite(value.y) && std::isfinite(value.z);
}

bool mayBreakDestructible(const PyEntity* owner,const StockObstacleIdentity& item,
    unsigned material,const Vector3& velocity,bool& allowed) {
    // 0x006ea2f0 calls the original unbound class method with the owner as its
    // first argument. IDs cross the original signed32 Python-int boundary;
    // scale and speed cross the original float32-to-Python-float boundary.
    if (item.filename.empty() || !std::isfinite(item.scale) || item.scale<0.f) {
        PyErr_SetString(PyExc_RuntimeError,"WGVehicleFilter destructible identity is incomplete");
        return false;
    }
    PyObjectPtr module(PyImport_ImportModule("Vehicle"),PyObjectPtr::STEAL_REFERENCE);
    if (!module) return false;
    PyObjectPtr vehicleClass(PyObject_GetAttrString(module.get(),"Vehicle"),PyObjectPtr::STEAL_REFERENCE);
    if (!vehicleClass) return false;
    PyObjectPtr method(PyObject_GetAttrString(vehicleClass.get(),"_isDestructibleMayBeBroken"),
        PyObjectPtr::STEAL_REFERENCE);
    if (!method) return false;
    const float speed=velocity.length();
    if (!std::isfinite(speed)) {
        PyErr_SetString(PyExc_ValueError,"WGVehicleFilter prediction speed is not finite");
        return false;
    }
    PyObjectPtr args(Py_BuildValue("(Oiiisff)",static_cast<PyObject*>(const_cast<PyEntity*>(owner)),
        static_cast<int32>(item.chunkID),static_cast<int32>(item.itemIndex),
        static_cast<int>(material),item.filename.c_str(),item.scale,speed),PyObjectPtr::STEAL_REFERENCE);
    if (!args) return false;
    PyObjectPtr result(PyObject_CallObject(method.get(),args.get()),PyObjectPtr::STEAL_REFERENCE);
    if (!result) return false;
    // 0x006ea2f0 accepts every result except exact Py_False, including None
    // and non-bool objects. The stock method itself returns only bool.
    // Exceptions intentionally remain query failures here: the independent
    // adapter must not report an unverifiable physical clearance as success.
    allowed=result.get()!=Py_False;
    return true;
}

class PredictionEdgeHit : public CollisionCallback {
public:
    PredictionEdgeHit(const PyEntity* owner,const EntityPtr& entity,const Vector3& velocity,
                      const ClientSpacePtr& space):
        owner_(owner),entity_(entity),filter_(entity->pFilter()),velocity_(velocity),space_(space),
        nearest_(-1.f),unavailable_(false) {}

    int operator()(const CollisionObstacle& obstacle,const WorldTriangle& triangle,
                   float distance) override {
        const unsigned material=triangle.materialKind();
        // Original Ccb_::operator() 0x006efa80. This is a horizontal BSP
        // prediction query, not the separately filtered ground placement ray.
        if ((triangle.flags()&0x50u)!=0 || (material>=0x57u && material<=0x64u))
            return COLLIDE_ALL;
        if (!std::isfinite(distance) || distance<0.f) {
            unavailable_=true;
            return COLLIDE_STOP;
        }
        StockObstacleIdentity identity;
        const bool identified=readStockObstacleIdentity(obstacle,identity);
        if (PyErr_Occurred()) { unavailable_=true;return COLLIDE_STOP; }
        if (identified) {
            bool allowed=false;
            if (!mayBreakDestructible(owner_,identity,material,velocity_,allowed)) {
                unavailable_=true;return COLLIDE_STOP;
            }
            if (owner_->isDestroyed() || space_->cleared() || owner_->pEntity().get()!=entity_.get() ||
                entity_->spaceID()!=space_->id() || entity_->pFilter()!=filter_) {
                PyErr_SetString(PyExc_RuntimeError,"WGVehicleFilter owner changed during destruction query");
                unavailable_=true;return COLLIDE_STOP;
            }
            if (allowed) return COLLIDE_ALL;
        } else if (material>=0x47u && material<=0x56u) {
            // No original identity means no trustworthy destruction decision.
            // The loader owns this metadata; do not substitute a BSP index.
            unavailable_=true;
            return COLLIDE_STOP;
        }
        if (nearest_<0.f || distance<nearest_) nearest_=distance;
        return COLLIDE_BEFORE;
    }

    bool unavailable() const { return unavailable_; }
    bool blocked() const { return nearest_>=0.f; }

private:
    const PyEntity* owner_;
    EntityPtr entity_;
    const Filter* filter_;
    Vector3 velocity_;
    ClientSpacePtr space_;
    float nearest_;
    bool unavailable_;
};
}

bool queryStockScenePrediction(SpaceID spaceID,const Vector3* endpoints,
    const WeakPyPtr<PyEntity>& weakOwner,const Vector3& velocity,bool& blocked) {
    if (PyErr_Occurred()) return false;
    const WeakPyPtr<PyEntity> ownerIdentity(weakOwner);
    const PyEntity* owner=ownerIdentity.get();
    if (!owner || owner->isDestroyed()) {
        PyErr_SetString(PyExc_RuntimeError,"WGVehicleFilter prediction owner is unavailable");
        return false;
    }
    // Pin both Python/native owners only for this synchronous query. Keep no
    // entity pointer beyond it; replacing a filter releases its weak identity.
    PyObjectPtr ownerPin(const_cast<PyEntity*>(owner));
    EntityPtr entity=owner->pEntity();
    if (!entity || entity->spaceID()!=spaceID) {
        PyErr_SetString(PyExc_RuntimeError,"WGVehicleFilter prediction owner changed space");
        return false;
    }
    if (!endpoints || !finite(velocity)) {
        PyErr_SetString(PyExc_ValueError,"WGVehicleFilter prediction geometry is invalid");
        return false;
    }
    for (unsigned i=0;i<8;++i) {
        if (!finite(endpoints[i])) {
            PyErr_SetString(PyExc_ValueError,"WGVehicleFilter prediction edge is not finite");
            return false;
        }
    }
    ClientSpacePtr space=SpaceManager::instance().space(spaceID);
    if (!space || space->cleared()) {
        PyErr_SetString(PyExc_RuntimeError,"WGVehicleFilter prediction space is unavailable");
        return false;
    }
    const Filter* filter=entity->pFilter();
    for (unsigned i=0;i<4;++i) {
        PredictionEdgeHit query(owner,entity,velocity,space);
        space->collide(endpoints[i*2],endpoints[i*2+1],query);
        if (query.unavailable()) {
            if (!PyErr_Occurred()) PyErr_SetString(PyExc_RuntimeError,
                "WGVehicleFilter prediction requires original destructible item metadata");
            return false;
        }
        if (owner->isDestroyed() || ownerIdentity.get()!=owner || space->cleared() ||
            owner->pEntity().get()!=entity.get() || entity->spaceID()!=spaceID || entity->pFilter()!=filter) {
            PyErr_SetString(PyExc_RuntimeError,"WGVehicleFilter prediction owner changed during query");
            return false;
        }
        // Public traversal's returned distance can retain rejected hits.
        // Only an accepted callback owns the native prediction result.
        if (query.blocked()) { blocked=true;return true; }
    }
    blocked=false;
    return true;
}
BW_END_NAMESPACE
