#include "pch.hpp"
#include "stock_vehicle_animators.hpp"
#include "stock_vehicle_components.hpp"
#include "stock_vehicle_visuals.hpp"

#include "pyscript/script_math.hpp"
#include "pyscript/py_callback.hpp"

#include <algorithm>
#include <cmath>
#include <memory>
#include <vector>

BW_BEGIN_NAMESPACE
int StockVehicleAnimators_token = 1;

namespace
{
struct VehicleRecoilCurve
{
    float elapsed = 0.f;
    float duration;
    float backoff;
    float backoffFrequency;
    float returnFrequency;
    float amplitude;
    float lodDistance;
    bool recoiling = false;

    VehicleRecoilCurve( float backoffTime, float returnTime, float value, float lod ) :
        duration( backoffTime + returnTime ), backoff( backoffTime ),
        backoffFrequency( 1.5707964f / backoffTime ),
        returnFrequency( 1.5707964f / returnTime ), amplitude( value ), lodDistance( lod ) {}

    void recoil() { recoiling = true; elapsed = 0.f; }

    float advance( float elapsedTime, float distance )
    {
        if (!recoiling)
            return 0.f;
        elapsed += elapsedTime;
        // 007615f0 cancels an off-LOD recoil instead of pausing it. The
        // return leg deliberately uses double sin after a float product;
        // the backoff leg uses sinf. Do not turn these into a single easing.
        if (elapsed > duration || distance > lodDistance)
        {
            recoiling = false;
            return 0.f;
        }
        float value;
        if (!(backoff >= elapsed))
            value = static_cast<float>( std::sin( static_cast<double>(
                (elapsed - backoff) * returnFrequency ) + 1.5707963705062866 ) );
        else
            value = sinf( backoffFrequency * elapsed );
        return value * amplitude;
    }
};

struct RecoilState : public StockComponentState
{
    VehicleRecoilCurve curve;
    Matrix basis;
    Matrix animated;
    PyObjectPtr lodLink;
    bool active = false;

    RecoilState( float backoff, float returnTime, float amplitude, float lod ) :
        curve( backoff, returnTime, amplitude, lod )
    {
        // 00761a10 value-initialises both pool matrices with zero bytes.
        // model_assembler supplies the actual authored gun node basis.
        basis.setZero();
        animated.setZero();
    }
    virtual void activate();
    virtual void deactivate();
    virtual void destroy()
    {
        this->deactivate();
        lodLink = NULL;
        valid_ = false;
    }
    virtual PyObject * wrap( bool owning );
};

std::vector<std::weak_ptr<RecoilState> > activeRecoil;

void RecoilState::activate()
{
    if (active || !valid_)
        return;
    active = true;
    activeRecoil.push_back( std::static_pointer_cast<RecoilState>( this->shared_from_this() ) );
}
void RecoilState::deactivate()
{
    if (!active)
        return;
    active = false;
    for (size_t index = 0; index < activeRecoil.size(); ++index)
        if (activeRecoil[index].lock().get() == this)
        {
            activeRecoil[index] = activeRecoil.back();
            activeRecoil.pop_back();
            break;
        }
}
}

class RecoilMatrixProvider : public MatrixProvider
{
    Py_Header( RecoilMatrixProvider, MatrixProvider )
public:
    explicit RecoilMatrixProvider( const std::shared_ptr<RecoilState> & state ) :
        MatrixProvider( false, &s_type_ ), state_( state ) {}
    virtual void matrix( Matrix & value ) const { value = state_->animated; }
private:
    // Keep the published matrix readable during synchronous detach/destroy.
    // This shared view cannot reactivate or advance the component.
    std::shared_ptr<RecoilState> state_;
};
PY_TYPEOBJECT( RecoilMatrixProvider )
PY_BEGIN_METHODS( RecoilMatrixProvider )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( RecoilMatrixProvider )
PY_END_ATTRIBUTES()

class RecoilAnimator : public StockComponentWrapper
{
    Py_Header( RecoilAnimator, StockComponentWrapper )
public:
    RecoilAnimator( const std::shared_ptr<StockComponentState> & state, bool owning ) :
        StockComponentWrapper( state, &s_type_, owning ) {}
    std::shared_ptr<RecoilState> state() const
    {
        std::shared_ptr<RecoilState> result =
            std::dynamic_pointer_cast<RecoilState>( this->component() );
        if (!result)
            PyErr_SetString( PyExc_ValueError, "RecoilAnimator has been destroyed" );
        return result;
    }
    PyObject * pyGet_basisMatrix()
    {
        std::shared_ptr<RecoilState> value = this->state();
        return value ? Script::getData( value->basis ) : NULL;
    }
    int pySet_basisMatrix( PyObject * object )
    {
        std::shared_ptr<RecoilState> value = this->state();
        if (!value)
            return -1;
        Matrix matrix;
        if (Script::setData( object, matrix ) != 0)
            return -1;
        // 0075e010 writes both the basis and its published animated copy.
        value->basis = value->animated = matrix;
        return 0;
    }
    PyObject * pyGet_animatedMProv()
    {
        std::shared_ptr<RecoilState> value = this->state();
        return value ? static_cast<PyObject *>( new RecoilMatrixProvider( value ) ) : NULL;
    }
    PyObject * pyGet_lodLink()
    {
        std::shared_ptr<RecoilState> value = this->state();
        if (!value)
            return NULL;
        return Script::getData( value->lodLink );
    }
    int pySet_lodLink( PyObject * object )
    {
        std::shared_ptr<RecoilState> value = this->state();
        if (!value)
            return -1;
        if (object != Py_None && !isStockFloatLink( object ))
        {
            PyErr_SetString( PyExc_TypeError, "lodLink requires a float DataLink or None" );
            return -1;
        }
        value->lodLink = object == Py_None ? NULL : object;
        return 0;
    }
    PY_RO_ATTRIBUTE_SET( animatedMProv )
    PY_METHOD_DECLARE( py_recoil )
    PY_FACTORY_DECLARE()
};

PyObject * RecoilState::wrap( bool owning )
{
    return new RecoilAnimator( this->shared_from_this(), owning );
}
PyObject * RecoilAnimator::pyNew( PyObject * args )
{
    float backoff;
    float returnTime;
    float amplitude;
    float lod;
    if (!PyArg_ParseTuple( args, "ffff:RecoilAnimator", &backoff, &returnTime, &amplitude, &lod ))
        return NULL;
    std::shared_ptr<RecoilState> value = std::make_shared<RecoilState>( backoff, returnTime, amplitude, lod );
    // 00762500 first allocates active pool slots, but 00761840 calls
    // IComponent::deactivate (00762770) before publishing its owner. The
    // original appearance's ComponentSystem later activates this state.
    return new RecoilAnimator( value, true );
}
PyObject * RecoilAnimator::py_recoil( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":recoil" ))
        return NULL;
    std::shared_ptr<RecoilState> value = this->state();
    if (!value)
        return NULL;
    value->curve.recoil();
    Py_RETURN_NONE;
}
PY_TYPEOBJECT( RecoilAnimator )
PY_BEGIN_METHODS( RecoilAnimator )
    PY_METHOD( recoil )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( RecoilAnimator )
    PY_ATTRIBUTE( basisMatrix )
    PY_ATTRIBUTE( animatedMProv )
    PY_ATTRIBUTE( lodLink )
PY_END_ATTRIBUTES()
PY_FACTORY( RecoilAnimator, Vehicular )

void tickStockVehicleRecoil( float gameDeltaTime )
{
    const std::vector<std::weak_ptr<RecoilState> > snapshot = activeRecoil;
    for (size_t index = 0; index < snapshot.size(); ++index)
    {
        std::shared_ptr<RecoilState> value = snapshot[index].lock();
        if (!value || !value->valid() || !value->active)
            continue;
        value->animated = value->basis;
        if (!value->curve.recoiling)
            continue;
        float distance = 0.f;
        if (!(value->curve.elapsed + gameDeltaTime > value->curve.duration) &&
            !sampleStockFloatLink( value->lodLink.get(), distance ))
        {
            // Native 007615f0 throws bad_function_call for an unbound link.
            // Contain that malformed presentation component at its owner.
            value->deactivate();
            value->curve.elapsed += gameDeltaTime;
            ERROR_MSG( "RecoilAnimator has no valid float LOD link\n" );
            continue;
        }
        value->animated[3][2] -= value->curve.advance( gameDeltaTime, distance );
    }
}

namespace
{
class RecoilScriptShutdown : public Script::FiniTimeJob
{
    virtual void fini() { activeRecoil.clear(); }
} recoilScriptShutdown;
}
BW_END_NAMESPACE
