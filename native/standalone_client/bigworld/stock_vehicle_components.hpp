#pragma once

#include "pyscript/script.hpp"
#include <memory>

BW_BEGIN_NAMESPACE

// #1513's Svarog wrapper transfers an existing component to a system. It
// never copies its state. Shared control blocks keep non-owning adapters safe
// when the one owning wrapper/system destroys the component synchronously.
class StockComponentState : public std::enable_shared_from_this<StockComponentState>
{
public:
    StockComponentState() : valid_( true ) {}
    virtual ~StockComponentState() {}
    bool valid() const { return valid_; }
    virtual void activate() = 0;
    virtual void deactivate() = 0;
    virtual void destroy() = 0;
    virtual PyObject * wrap( bool owning ) = 0;

protected:
    bool valid_;
};

class StockComponentWrapper : public PyObjectPlusWithWeakReference
{
    Py_Header( StockComponentWrapper, PyObjectPlusWithWeakReference )
public:
    StockComponentWrapper( const std::shared_ptr<StockComponentState> & state,
        PyTypeObject * type, bool owning = true );
    virtual ~StockComponentWrapper();
    std::shared_ptr<StockComponentState> component() const;
    std::shared_ptr<StockComponentState> transfer();
    PY_RO_ATTRIBUTE_DECLARE( owning_, isOwning )
    PY_METHOD_DECLARE( activate )
    PY_METHOD_DECLARE( deactivate )
    PY_METHOD_DECLARE( destroy )

private:
    void clear();
    std::shared_ptr<StockComponentState> owned_;
    std::weak_ptr<StockComponentState> view_;
    bool owning_;
};

extern int StockVehicleComponents_token;
BW_END_NAMESPACE
