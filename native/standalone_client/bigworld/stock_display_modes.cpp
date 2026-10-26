#ifndef WOT_STOCK_DISPLAY_MODES_PORTABLE
#include "pch.hpp"
#include "stock_display_modes.hpp"
#include "moo/render_context.hpp"
#endif

#include <string>

BW_BEGIN_NAMESPACE
int StockDisplayModes_token = 1;

namespace
{
enum WindowMode { WINDOWED = 0, EXCLUSIVE = 1, BORDERLESS = 2 };

struct DisplayState
{
    HWND window = NULL;
    int selected[3] = { 0, 0, 0 };
    RECT normalRect = {};
    LONG normalStyle = 0;
    LONG normalExtendedStyle = 0;
    int normalWidth = 0;
    int normalHeight = 0;
    int fixedWidth = 0;
    int fixedHeight = 0;
};
DisplayState displayState;

int windowMode()
{
    if (!Moo::rc().windowed()) return EXCLUSIVE;
    return (GetWindowLong( Moo::rc().windowHandle(), GWL_STYLE ) & WS_CAPTION)
        ? WINDOWED : BORDERLESS;
}

bool monitorInfo( int index, MONITORINFOEXW & info )
{
    if (index < 0 || static_cast<unsigned>( index ) >= Moo::rc().nDevices() ||
        !Moo::rc().device()) return false;
    IDirect3D9 * direct3d = NULL;
    if (FAILED( Moo::rc().device()->GetDirect3D( &direct3d ) )) return false;
    HMONITOR monitor = direct3d->GetAdapterMonitor(
        Moo::rc().deviceInfo( index ).adapterID_ );
    direct3d->Release();
    info = MONITORINFOEXW();
    info.cbSize = sizeof( info );
    return monitor && GetMonitorInfoW( monitor, &info );
}

int actualMonitor()
{
    if (!Moo::rc().windowed()) return static_cast<int>( Moo::rc().deviceIndex() );
    HMONITOR current = MonitorFromWindow( Moo::rc().windowHandle(), MONITOR_DEFAULTTONEAREST );
    MONITORINFOEXW actual = {};
    actual.cbSize = sizeof( actual );
    if (current && GetMonitorInfoW( current, &actual ))
    {
        for (unsigned index = 0; index < Moo::rc().nDevices(); ++index)
        {
            MONITORINFOEXW candidate;
            if (monitorInfo( index, candidate ) &&
                wcscmp( candidate.szDevice, actual.szDevice ) == 0) return index;
        }
    }
    return static_cast<int>( Moo::rc().deviceIndex() );
}

void rememberNormalWindow()
{
    HWND window = Moo::rc().windowHandle();
    GetWindowRect( window, &displayState.normalRect );
    displayState.normalStyle = GetWindowLong( window, GWL_STYLE );
    displayState.normalExtendedStyle = GetWindowLong( window, GWL_EXSTYLE );
    RECT client = {};
    GetClientRect( window, &client );
    displayState.normalWidth = client.right - client.left;
    displayState.normalHeight = client.bottom - client.top;
}

bool ready()
{
    if (!Moo::rc().device() || !Moo::rc().nDevices() || !Moo::rc().windowHandle())
    {
        PyErr_SetString( PyExc_RuntimeError, "The native display device is not initialized" );
        return false;
    }
    if (displayState.window != Moo::rc().windowHandle())
    {
        displayState = DisplayState();
        displayState.window = Moo::rc().windowHandle();
        for (int & selected : displayState.selected) selected = actualMonitor();
        if (windowMode() == WINDOWED) rememberNormalWindow();
    }
    return true;
}

bool validMode( int mode ) { return mode >= WINDOWED && mode <= BORDERLESS; }

int selectedMonitor( int mode )
{
    // A dragged window and an exclusive device remain the live authority.
    return mode == windowMode() ? actualMonitor() : displayState.selected[mode];
}

int refreshRate()
{
    const Moo::DeviceInfo & device = Moo::rc().deviceInfo( Moo::rc().deviceIndex() );
    if (!Moo::rc().windowed() && Moo::rc().modeIndex() < device.displayModes_.size())
        return device.displayModes_[Moo::rc().modeIndex()].RefreshRate;
    return device.windowedDisplayMode_.RefreshRate;
}

int colorDepth( D3DFORMAT format )
{
    switch (format)
    {
    case D3DFMT_A2R10G10B10: case D3DFMT_A2B10G10R10:
    case D3DFMT_A8R8G8B8: case D3DFMT_X8R8G8B8:
    case D3DFMT_A8B8G8R8: case D3DFMT_X8B8G8R8: return 32;
    case D3DFMT_A1R5G5B5: case D3DFMT_X1R5G5B5: case D3DFMT_R5G6B5: return 16;
    default: return 0;
    }
}

PyObject * videoMode( int index, int width, int height, int depth, int rate )
{
    PyObject * label = PyString_FromFormat( "%dx%dx%d@%dHz", width, height, depth, rate );
    if (!label) return NULL;
    PyObject * result = Py_BuildValue( "(iiiiOi)", index, width, height, depth, label, rate );
    Py_DECREF( label );
    return result;
}

bool borderlessRect( int monitor, RECT & rectangle )
{
    MONITORINFOEXW info;
    if (!monitorInfo( monitor, info )) return false;
    rectangle = info.rcMonitor;
    if (displayState.fixedWidth > 0 && displayState.fixedHeight > 0)
    {
        rectangle.right = rectangle.left + displayState.fixedWidth;
        rectangle.bottom = rectangle.top + displayState.fixedHeight;
    }
    return true;
}

bool positionWindow( const RECT & rectangle, UINT flags )
{
    return SetWindowPos( Moo::rc().windowHandle(), NULL, rectangle.left, rectangle.top,
        rectangle.right - rectangle.left, rectangle.bottom - rectangle.top,
        SWP_NOZORDER | SWP_NOACTIVATE | flags ) != FALSE;
}

std::string utf8( const wchar_t * text )
{
    int length = WideCharToMultiByte( CP_UTF8, 0, text, -1, NULL, 0, NULL, NULL );
    if (!length) return std::string();
    std::string result( length, '\0' );
    WideCharToMultiByte( CP_UTF8, 0, text, -1, &result[0], length, NULL, NULL );
    result.resize( length - 1 );
    return result;
}
}

void stockRememberDisplayWindow()
{
    // createDevice calls this before the first WS_POPUP/fullscreen mutation,
    // while the HWND still carries its actual normal client area and styles.
    displayState = DisplayState();
    displayState.window = Moo::rc().windowHandle();
    for (int & selected : displayState.selected) selected = Moo::rc().deviceIndex();
    rememberNormalWindow();
}

static PyObject * py_listVideoModesAllMonitors( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":listVideoModesAllMonitors" ) || !ready()) return NULL;
    PyObject * result = PyList_New( Moo::rc().nDevices() );
    if (!result) return NULL;
    for (unsigned monitor = 0; monitor < Moo::rc().nDevices(); ++monitor)
    {
        const Moo::DeviceInfo & device = Moo::rc().deviceInfo( monitor );
        PyObject * modes = PyList_New( device.displayModes_.size() );
        if (!modes) { Py_DECREF( result ); return NULL; }
        PyList_SET_ITEM( result, monitor, modes );
        for (unsigned index = 0; index < device.displayModes_.size(); ++index)
        {
            const D3DDISPLAYMODE & mode = device.displayModes_[index];
            PyObject * entry = videoMode( index, mode.Width, mode.Height,
                colorDepth( mode.Format ), mode.RefreshRate );
            if (!entry) { Py_DECREF( result ); return NULL; }
            PyList_SET_ITEM( modes, index, entry );
        }
    }
    return result;
}
PY_MODULE_FUNCTION( listVideoModesAllMonitors, BigWorld )

static PyObject * py_listBorderlessResolutionsAllMonitors( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":listBorderlessResolutionsAllMonitors" ) || !ready()) return NULL;
    PyObject * result = PyList_New( Moo::rc().nDevices() );
    if (!result) return NULL;
    for (unsigned index = 0; index < Moo::rc().nDevices(); ++index)
    {
        RECT rectangle;
        if (!borderlessRect( index, rectangle ))
        {
            Py_DECREF( result );
            return PyErr_Format( PyExc_RuntimeError, "Cannot query monitor %u", index );
        }
        PyObject * entry = videoMode( 0, rectangle.right - rectangle.left,
            rectangle.bottom - rectangle.top, 32, refreshRate() );
        if (!entry) { Py_DECREF( result ); return NULL; }
        PyObject * modes = PyList_New( 1 );
        if (!modes) { Py_DECREF( entry ); Py_DECREF( result ); return NULL; }
        PyList_SET_ITEM( modes, 0, entry );
        PyList_SET_ITEM( result, index, modes );
    }
    return result;
}
PY_MODULE_FUNCTION( listBorderlessResolutionsAllMonitors, BigWorld )

static PyObject * maxResolution( PyObject * args, bool borderless )
{
    int index;
    if (!PyArg_ParseTuple( args, "i", &index ) || !ready()) return NULL;
    // 00970ce0/00970c50 preserve this native default for an absent monitor.
    int width = 1024, height = 768;
    MONITORINFOEXW info;
    if (monitorInfo( index, info ))
    {
        RECT rectangle = borderless ? info.rcMonitor : info.rcWork;
        width = rectangle.right - rectangle.left;
        height = rectangle.bottom - rectangle.top;
        if (!borderless)
        {
            RECT frame = {};
            LONG style = displayState.normalStyle ? displayState.normalStyle :
                GetWindowLong( Moo::rc().windowHandle(), GWL_STYLE );
            LONG extended = displayState.normalExtendedStyle;
            if (!AdjustWindowRectEx( &frame, style, FALSE, extended ))
                return PyErr_Format( PyExc_RuntimeError, "Cannot query the native window frame" );
            width -= frame.right - frame.left;
            height -= frame.bottom - frame.top;
        }
    }
    return Py_BuildValue( "(iii)", width, height, refreshRate() );
}
static PyObject * py_wg_getMaxWindowedResolution( PyObject * args ) { return maxResolution( args, false ); }
static PyObject * py_wg_getMaxBorderlessResolution( PyObject * args ) { return maxResolution( args, true ); }
PY_MODULE_FUNCTION( wg_getMaxWindowedResolution, BigWorld )
PY_MODULE_FUNCTION( wg_getMaxBorderlessResolution, BigWorld )

static PyObject * py_getWindowMode( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":getWindowMode" ) || !ready()) return NULL;
    return PyInt_FromLong( windowMode() );
}
PY_MODULE_FUNCTION( getWindowMode, BigWorld )

static PyObject * py_getActiveMonitorIndex( PyObject * args )
{
    int mode;
    if (!PyArg_ParseTuple( args, "i:getActiveMonitorIndex", &mode ) || !ready()) return NULL;
    if (!validMode( mode )) return PyErr_Format( PyExc_ValueError, "Invalid window mode" );
    return PyInt_FromLong( selectedMonitor( mode ) );
}
PY_MODULE_FUNCTION( getActiveMonitorIndex, BigWorld )

static PyObject * py_getExclusiveFullscreenMonitorIndex( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":getExclusiveFullscreenMonitorIndex" ) || !ready()) return NULL;
    return PyInt_FromLong( Moo::rc().deviceIndex() );
}
PY_MODULE_FUNCTION( getExclusiveFullscreenMonitorIndex, BigWorld )

static PyObject * py_wg_getCurrentResolution( PyObject * args )
{
    int mode;
    if (!PyArg_ParseTuple( args, "i:wg_getCurrentResolution", &mode ) || !ready()) return NULL;
    if (!validMode( mode )) return PyErr_Format( PyExc_ValueError, "Invalid window mode" );
    int width, height;
    if (mode == windowMode())
    {
        RECT client = {};
        if (!GetClientRect( Moo::rc().windowHandle(), &client ))
            return PyErr_Format( PyExc_RuntimeError, "Cannot query the native client area" );
        width = client.right - client.left;
        height = client.bottom - client.top;
    }
    else if (mode == WINDOWED && displayState.normalWidth > 0)
    {
        width = displayState.normalWidth;
        height = displayState.normalHeight;
    }
    else if (mode == EXCLUSIVE)
    {
        const Moo::DeviceInfo & info = Moo::rc().deviceInfo( Moo::rc().deviceIndex() );
        if (Moo::rc().modeIndex() >= info.displayModes_.size())
            return PyErr_Format( PyExc_RuntimeError, "The native video mode index is unavailable" );
        const D3DDISPLAYMODE & chosen = info.displayModes_[Moo::rc().modeIndex()];
        width = chosen.Width; height = chosen.Height;
    }
    else
    {
        RECT rectangle;
        if (!borderlessRect( selectedMonitor( mode ), rectangle ))
            return PyErr_Format( PyExc_RuntimeError, "The selected monitor is unavailable" );
        width = rectangle.right - rectangle.left;
        height = rectangle.bottom - rectangle.top;
    }
    return Py_BuildValue( "(fff)", static_cast<double>( width ),
        static_cast<double>( height ), static_cast<double>( refreshRate() ) );
}
PY_MODULE_FUNCTION( wg_getCurrentResolution, BigWorld )

static PyObject * py_wg_getMonitorNames( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":wg_getMonitorNames" ) || !ready()) return NULL;
    PyObject * result = PyList_New( Moo::rc().nDevices() );
    if (!result) return NULL;
    for (unsigned index = 0; index < Moo::rc().nDevices(); ++index)
    {
        MONITORINFOEXW info;
        if (!monitorInfo( index, info ))
        {
            Py_DECREF( result );
            return PyErr_Format( PyExc_RuntimeError, "Cannot query monitor %u", index );
        }
        DISPLAY_DEVICEW device = {};
        device.cb = sizeof( device );
        std::string name = utf8( info.szDevice );
        for (DWORD child = 0; EnumDisplayDevicesW( info.szDevice, child, &device, 0 ); ++child)
            if ((device.StateFlags & DISPLAY_DEVICE_ACTIVE) &&
                !(device.StateFlags & DISPLAY_DEVICE_MIRRORING_DRIVER))
            { name = utf8( device.DeviceString ); break; }
        PyObject * label = PyString_FromFormat( "%u - %s%s", index + 1, name.c_str(),
            (info.dwFlags & MONITORINFOF_PRIMARY) ? " (primary)" : "" );
        if (!label) { Py_DECREF( result ); return NULL; }
        PyList_SET_ITEM( result, index, label );
    }
    return result;
}
PY_MODULE_FUNCTION( wg_getMonitorNames, BigWorld )

static PyObject * py_getBorderlessParameters( PyObject * args )
{
    if (!PyArg_ParseTuple( args, ":getBorderlessParameters" ) || !ready()) return NULL;
    int monitor = selectedMonitor( BORDERLESS );
    RECT rectangle;
    if (!borderlessRect( monitor, rectangle ))
        return PyErr_Format( PyExc_RuntimeError, "The borderless monitor is unavailable" );
    // 00b0dc50's fixed-size constructor sets behaviour=1 and a local (0,0).
    return Py_BuildValue( "(iiiiii)", displayState.fixedWidth > 0 ? 1 : 0, 0, 0,
        rectangle.right - rectangle.left, rectangle.bottom - rectangle.top, monitor );
}
PY_MODULE_FUNCTION( getBorderlessParameters, BigWorld )

static PyObject * py_setBorderlessFixedSize( PyObject * args )
{
    int width, height;
    if (!PyArg_ParseTuple( args, "ii:setBorderlessFixedSize", &width, &height ) || !ready()) return NULL;
    if (width <= 0 || height <= 0) return PyErr_Format( PyExc_ValueError, "Invalid borderless size" );
    displayState.fixedWidth = width; displayState.fixedHeight = height;
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION( setBorderlessFixedSize, BigWorld )

bool stockChangeVideoMode( int modeIndex, int mode )
{
    if (!Moo::rc().device() || !validMode( mode )) return false;
    if (!ready()) { PyErr_Clear(); return false; }
    const Moo::DeviceInfo & info = Moo::rc().deviceInfo( Moo::rc().deviceIndex() );
    if (modeIndex == -1) modeIndex = Moo::rc().modeIndex();
    if (modeIndex < 0 || static_cast<unsigned>( modeIndex ) >= info.displayModes_.size()) return false;
    // Stock VideoSettingsStorage requests a restart when the exclusive device
    // changes. Do not claim that this existing D3D device changed adapters.
    if (mode == EXCLUSIVE && displayState.selected[EXCLUSIVE] !=
        static_cast<int>( Moo::rc().deviceIndex() )) return false;
    int previousMode = windowMode();
    if (previousMode == WINDOWED) rememberNormalWindow();
    RECT previousRectangle = {};
    GetWindowRect( displayState.window, &previousRectangle );
    const LONG previousStyle = GetWindowLong( displayState.window, GWL_STYLE );
    const LONG previousExtended = GetWindowLong( displayState.window, GWL_EXSTYLE );
    const unsigned previousIndex = Moo::rc().modeIndex();
    const bool previousWindowed = Moo::rc().windowed();
    auto restore = [&]() -> bool
    {
        SetWindowLong( displayState.window, GWL_STYLE, previousStyle );
        SetWindowLong( displayState.window, GWL_EXSTYLE, previousExtended );
        positionWindow( previousRectangle, SWP_FRAMECHANGED );
        if (Moo::rc().modeIndex() != previousIndex || Moo::rc().windowed() != previousWindowed)
            Moo::rc().changeMode( previousIndex, previousWindowed );
        else
            Moo::rc().resetDevice();
        return false;
    };
    if (previousMode == BORDERLESS && mode != BORDERLESS)
    {
        SetWindowLong( displayState.window, GWL_STYLE, displayState.normalStyle );
        SetWindowLong( displayState.window, GWL_EXSTYLE, displayState.normalExtendedStyle );
        if (!positionWindow( displayState.normalRect, SWP_FRAMECHANGED )) return restore();
    }
    if (!Moo::rc().changeMode( modeIndex, mode != EXCLUSIVE )) return restore();
    if (mode == BORDERLESS)
    {
        if (previousMode == EXCLUSIVE) rememberNormalWindow();
        RECT rectangle;
        if (!borderlessRect( displayState.selected[BORDERLESS], rectangle )) return restore();
        SetWindowLong( displayState.window, GWL_STYLE,
            static_cast<LONG>( (displayState.normalStyle & ~WS_OVERLAPPEDWINDOW) | WS_POPUP ) );
        SetWindowLong( displayState.window, GWL_EXSTYLE,
            displayState.normalExtendedStyle & ~(WS_EX_WINDOWEDGE | WS_EX_CLIENTEDGE) );
        if (!positionWindow( rectangle, SWP_FRAMECHANGED ) || !Moo::rc().resetDevice())
            return restore();
    }
    else if (mode == WINDOWED && actualMonitor() != displayState.selected[WINDOWED])
    {
        MONITORINFOEXW destination;
        RECT rectangle = {};
        if (!monitorInfo( displayState.selected[WINDOWED], destination )) return restore();
        GetWindowRect( displayState.window, &rectangle );
        OffsetRect( &rectangle, destination.rcWork.left - rectangle.left,
            destination.rcWork.top - rectangle.top );
        if (!positionWindow( rectangle, 0 )) return restore();
        rememberNormalWindow();
    }
    return true;
}

void stockResizeWindow( int width, int height )
{
    if (width <= 0 || height <= 0 || !Moo::rc().device() || !Moo::rc().windowed()) return;
    RECT rectangle = {};
    HWND window = Moo::rc().windowHandle();
    GetWindowRect( window, &rectangle );
    rectangle.right = rectangle.left + width; rectangle.bottom = rectangle.top + height;
    AdjustWindowRectEx( &rectangle, GetWindowLong( window, GWL_STYLE ), FALSE,
        GetWindowLong( window, GWL_EXSTYLE ) );
    MONITORINFOEXW info;
    if (monitorInfo( actualMonitor(), info ))
    {
        LONG x = rectangle.left < info.rcWork.left ? info.rcWork.left - rectangle.left : 0;
        LONG y = rectangle.top < info.rcWork.top ? info.rcWork.top - rectangle.top : 0;
        OffsetRect( &rectangle, x, y );
    }
    positionWindow( rectangle, 0 );
    if (windowMode() == WINDOWED) rememberNormalWindow();
}

static PyObject * py_setActiveMonitorIndex( PyObject * args )
{
    int index, mode;
    if (!PyArg_ParseTuple( args, "ii:setActiveMonitorIndex", &index, &mode ) || !ready()) return NULL;
    MONITORINFOEXW info;
    if (!validMode( mode ) || !monitorInfo( index, info ))
        return PyErr_Format( PyExc_ValueError, "Invalid monitor or window mode" );
    displayState.selected[mode] = index;
    if (mode == windowMode() && mode == BORDERLESS)
    {
        if (!stockChangeVideoMode( -1, BORDERLESS ))
            return PyErr_Format( PyExc_RuntimeError, "Cannot move the borderless window" );
    }
    else if (mode == windowMode() && mode == WINDOWED)
    {
        RECT rectangle = {};
        GetWindowRect( displayState.window, &rectangle );
        OffsetRect( &rectangle, info.rcWork.left - rectangle.left, info.rcWork.top - rectangle.top );
        if (!positionWindow( rectangle, 0 ))
            return PyErr_Format( PyExc_RuntimeError, "Cannot move the native window" );
        rememberNormalWindow();
    }
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION( setActiveMonitorIndex, BigWorld )

PY_MODULE_ATTRIBUTE( BigWorld, WindowModeWindowed, PyInt_FromLong( WINDOWED ) )
PY_MODULE_ATTRIBUTE( BigWorld, WindowModeExclusiveFullscreen, PyInt_FromLong( EXCLUSIVE ) )
PY_MODULE_ATTRIBUTE( BigWorld, WindowModeBorderless, PyInt_FromLong( BORDERLESS ) )
BW_END_NAMESPACE
