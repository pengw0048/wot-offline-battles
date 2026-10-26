#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "romp/py_resource_refs.hpp"
#include "math/boundbox.hpp"

BW_BEGIN_NAMESPACE

// Referenced by the standalone bootstrap when linking the stock appearance
// factories into a client that does not otherwise reference their classes.
extern int StockVehicleAppearance_token;

// Entity prerequisite orders own this request, not the Entity. Cancellation
// drops its result and makes a late background callback harmless.
bool beginStockVehiclePrerequisites( PyObject * resources, PyObjectPtr & request );
bool pollStockVehiclePrerequisites( PyObject * request, ResourceRefs & resources );
void cancelStockVehiclePrerequisites( PyObject * request );
bool stockVehicleModelBounds( PyObject * model, BoundingBox & bounds );

BW_END_NAMESPACE
