#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "romp/resource_ref.hpp"
#include "romp/py_resource_refs.hpp"
#include "math/boundbox.hpp"
#include "pyscript/script_math.hpp"

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
bool stockVehicleCompoundContext( PyObject * compound, uint32 & spaceID,
    BoundingBox & worldBounds );
bool stockVehicleCompoundNode( PyObject * compound, const BW::string & name,
    Matrix & authoredLocal, Matrix & parentWorld, Matrix & world );
bool writeStockVehicleCompoundNodeLocal( PyObject * compound,
    const BW::string & name, const Matrix & local );

BW_END_NAMESPACE
