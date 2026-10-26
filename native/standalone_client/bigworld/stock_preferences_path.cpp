#include "pch.hpp"
#include "stock_preferences_path.hpp"
#include "device_app.hpp"
#include "pyscript/script.hpp"

BW_BEGIN_NAMESPACE
int StockPreferencesPath_token = 1;

BW::string DeviceApp::preferencesFilePath() const {
    // Read the same PathedFilename used by DeviceApp's load/save operations.
    // resolveName intentionally resolves dynamic bases on every call.
    return preferencesFilename_.resolveName();
}

PyObject* py_wg_getPreferencesFilePath(PyObject* args) {
    if (!PyArg_ParseTuple(args, ":wg_getPreferencesFilePath")) return NULL;
    // #1513 0x00653be0 -> 0x0068e9d0 returns a UTF-8 byte str, which
    // BattleResultsCache and the other stock caches explicitly decode.
    return Script::getData(DeviceApp::instance.preferencesFilePath());
}
PY_MODULE_FUNCTION(wg_getPreferencesFilePath, BigWorld)

BW_END_NAMESPACE
