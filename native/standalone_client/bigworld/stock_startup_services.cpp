#include "pch.hpp"
#include "stock_startup_services.hpp"

#include "moo/graphics_settings.hpp"

BW_BEGIN_NAMESPACE
int StockStartupServices_token = 1;

namespace
{
// Exact #1513 globals 01a1a974/01a1a978 start enabled. A setting's initial
// option index is separately zero; constructing its options does not call
// its callback. Keeping those two facts distinct matters before preferences
// are applied by the graphics-settings owner.
bool vehicleDustEnabled = true;
bool sniperModeSwingingEnabled = true;

class StockScriptGraphicsSetting : public Moo::GraphicsSetting
{
public:
    StockScriptGraphicsSetting( const char * label, const char * description,
        bool & output ) : Moo::GraphicsSetting( label, description,
            0, false, false ), output_( output )
    {
        // 00971070/0096f7a0: OFF precedes ON, values equal option indices.
        this->addOption( "OFF", "Off", true );
        this->addOption( "ON", "On", true );
    }

    void onOptionSelected( int optionIndex ) override
    {
        // 00971040 and 00971010 publish exactly value != 0.
        output_ = optionIndex != 0;
    }

private:
    bool & output_;
};

bool registerScriptSetting( const char * label, const char * description,
    bool & output )
{
    Moo::GraphicsSetting::GraphicsSettingPtr previous =
        Moo::GraphicsSetting::getFromLabel( label );
    if (previous)
    {
        if (dynamic_cast<StockScriptGraphicsSetting *>( previous.getObject() ))
            return true;
        PyErr_Format( PyExc_RuntimeError,
            "Original graphics setting '%s' already has another owner", label );
        return false;
    }
    Moo::GraphicsSetting::GraphicsSettingPtr setting =
        new StockScriptGraphicsSetting( label, description, output );
    // The public registry supplies persistence, supported-option selection,
    // pending updates and teardown; this adapter does not own another table.
    Moo::GraphicsSetting::add( setting );
    return true;
}
}

bool initialiseStockScriptGraphicsSettings()
{
    return registerScriptSetting( "VEHICLE_DUST_ENABLED",
        "Vehicle Dust Enabled", vehicleDustEnabled ) &&
        registerScriptSetting( "SNIPER_MODE_SWINGING_ENABLED",
            "Sniper Mode Swinging Enabled", sniperModeSwingingEnabled );
}

// 0096f3f0/0096f3b0 return Python ints, not booleans or option indices.
static PyObject * py_wg_isVehicleDustEnabled( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":wg_isVehicleDustEnabled" ))
        return NULL;
    return PyInt_FromLong( vehicleDustEnabled ? 1 : 0 );
}
PY_MODULE_FUNCTION( wg_isVehicleDustEnabled, BigWorld )

static PyObject * py_wg_isSniperModeSwingingEnabled( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":wg_isSniperModeSwingingEnabled" ))
        return NULL;
    return PyInt_FromLong( sniperModeSwingingEnabled ? 1 : 0 );
}
PY_MODULE_FUNCTION( wg_isSniperModeSwingingEnabled, BigWorld )

BW_END_NAMESPACE
