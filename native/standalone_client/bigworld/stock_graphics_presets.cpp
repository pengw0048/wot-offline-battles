#include "pch.hpp"
#include "stock_graphics_presets.hpp"
#include "wot/stock_graphics_presets.hpp"
#include "resmgr/bwresource.hpp"
#include "pyscript/script.hpp"
#include <algorithm>

BW_BEGIN_NAMESPACE
int StockGraphicsPresets_token=1;
namespace
{
wot::StockGraphicsPresets catalogue;
bool registered=false;

wot::StockGraphicsSettings toStock(const Moo::GraphicsSetting::SettingsVector& input)
{
    wot::StockGraphicsSettings output;
    for(const auto& entry:input)
        output.emplace_back(std::string(entry.first.data(),entry.first.size()),static_cast<int>(entry.second));
    return output;
}

void loadCatalogue()
{
    catalogue.clear();
    DataSectionPtr source=BWResource::openSection("system/data/graphics_settings_presets.xml");
    int index=0;
    if(source)
    {
        BW::vector<DataSectionPtr> presets;
        source->openSections("graphicsPreferences",presets);
        for(const auto& preset:presets)
        {
            BW::vector<DataSectionPtr> entries;
            preset->openSections("entry",entries);
            wot::StockGraphicsSettings settings;
            for(const auto& entry:entries)
            {
                const BW::string label=entry->readString("label","");
                if(!label.empty())settings.emplace_back(std::string(label.data(),label.size()),
                    entry->readInt("activeOption",0));
            }
            const BW::string name=preset->asString();
            catalogue.add(std::string(name.data(),name.size()),index++,settings);
        }
    }
    // 91e8c0 creates this actual empty option even if the resource is absent.
    catalogue.add("CUSTOM",index,{});
}

class PresetsSetting : public Moo::GraphicsSetting
{
public:
    explicit PresetsSetting(int selected) :
        Moo::GraphicsSetting("presets","Presets",selected,false,false)
    {
        // UI options retain XML order, whereas Python queries enumerate the
        // original name-ordered map. The two index spaces are not aliases.
        const std::size_t count=catalogue.presets().size();
        for(std::size_t i=0;i<count;++i)
        {
            const auto* preset=catalogue.byAuthoredIndex(static_cast<int>(i));
            MF_ASSERT(preset);
            addOption(preset->optionName.c_str(),preset->optionName.c_str(),true);
        }
    }
    virtual void onOptionSelected(int option)
    {
        const auto* preset=catalogue.byAuthoredIndex(option);
        if(preset)catalogue.publish(preset->settings);
    }
};

void apply(const wot::StockGraphicsSettings& settings)
{
    Moo::GraphicsSetting::SettingsVector input;
    for(const auto& entry:settings)input.emplace_back(BW::string(entry.first.data(),entry.first.size()),
        static_cast<uint32>(entry.second));
    Moo::GraphicsSetting::applyStockPreset(input);
}

PyObject* dictionary(const wot::StockGraphicsSettings& settings)
{
    PyObjectPtr result(PyDict_New(),PyObjectPtr::STEAL_REFERENCE);
    if(!result)return NULL;
    for(const auto& setting:settings)
    {
        PyObjectPtr value(PyInt_FromLong(setting.second),PyObjectPtr::STEAL_REFERENCE);
        if(!value || PyDict_SetItemString(result.get(),setting.first.c_str(),value.get())<0)return NULL;
    }
    Py_INCREF(result.get());return result.get();
}

wot::StockGraphicsPreset& lookup(const BW::string& name)
{
    if(catalogue.presets().empty())loadCatalogue();
    return catalogue.lookup(std::string(name.data(),name.size()));
}
}

bool stockGraphicsPresetDesiredOption(const BW::string& label,int& option)
{
    return registered && catalogue.desiredOption(std::string(label.data(),label.size()),option);
}

bool stockGraphicsPresetsActive(){return registered;}

void setStockGraphicsPresetOverload(const BW::string& name)
{
    if(name!="none")catalogue.presetOverload(std::string(name.data(),name.size()));
}

void publishStockGraphicsPreferences(const Moo::GraphicsSetting::SettingsVector& settings)
{
    if(catalogue.presets().empty() && catalogue.hasPresetOverload())loadCatalogue();
    catalogue.publish(toStock(settings));
}

bool initialiseStockGraphicsPresets()
{
    auto existing=Moo::GraphicsSetting::getFromLabel("presets");
    if(existing)
    {
        if(dynamic_cast<PresetsSetting*>(existing.getObject()))return true;
        PyErr_SetString(PyExc_RuntimeError,"Graphics presets already have another owner");return false;
    }
    loadCatalogue();
    int selected=-1;
    if(!catalogue.desiredOption("presets",selected))
        selected=catalogue.lookup(catalogue.currentKey()).authoredIndex;
    // Registration must read the already published desired option without
    // executing a callback. This is the 91c320/91def0 constructor contract.
    // Public core constructors precede preference loading. The native core
    // constructors instead read desired preferences before 91ce80 snapshots
    // them. Start its comparison model from the now-established live state.
    Moo::GraphicsSetting::captureStockPresetBaseline();
    registered=true;
    Moo::GraphicsSetting::add(new PresetsSetting(selected));
    return true;
}

static PyObject* py_getGraphicsPresets(PyObject* args)
{
    if(!PyArg_ParseTuple(args,":getGraphicsPresets"))return NULL;
    PyObjectPtr result(PyList_New(catalogue.presets().size()),PyObjectPtr::STEAL_REFERENCE);
    if(!result)return NULL;
    Py_ssize_t index=0;
    for(const auto& preset:catalogue.presets())
    {
        PyObject* value=dictionary(preset.second.settings);
        if(!value)return NULL;
        PyList_SET_ITEM(result.get(),index++,value);
    }
    Py_INCREF(result.get());return result.get();
}
PY_MODULE_FUNCTION(getGraphicsPresets,BigWorld)

static PyObject* py_getGraphicsPreset(PyObject* args)
{
    int index;
    if(!PyArg_ParseTuple(args,"i:getGraphicsPreset",&index))return NULL;
    const auto* preset=catalogue.byOrdinal(index);
    if(!preset)Py_RETURN_NONE;
    return dictionary(preset->settings);
}
PY_MODULE_FUNCTION(getGraphicsPreset,BigWorld)

static PyObject* py_getGraphicsPresetsIndices(PyObject* args)
{
    if(!PyArg_ParseTuple(args,":getGraphicsPresetsIndices"))return NULL;
    PyObjectPtr result(PyDict_New(),PyObjectPtr::STEAL_REFERENCE);
    if(!result)return NULL;
    long index=0;
    for(const auto& preset:catalogue.presets())
    {
        PyObjectPtr value(PyInt_FromLong(index++),PyObjectPtr::STEAL_REFERENCE);
        if(!value || PyDict_SetItemString(result.get(),preset.first.c_str(),value.get())<0)return NULL;
    }
    Py_INCREF(result.get());return result.get();
}
PY_MODULE_FUNCTION(getGraphicsPresetsIndices,BigWorld)

static PyObject* py_currentGraphicPresetKey(PyObject* args)
{
    if(!PyArg_ParseTuple(args,":currentGraphicPresetKey"))return NULL;
    const std::string key=catalogue.currentKey();
    return PyString_FromStringAndSize(key.data(),key.size());
}
PY_MODULE_FUNCTION(currentGraphicPresetKey,BigWorld)

static PyObject* py_setGraphicPresetByName(PyObject* args)
{
    BW::string name;PyObject* input;
    if(!PyArg_ParseTuple(args,"O:setGraphicPresetByName",&input) || Script::setData(input,name))return NULL;
    apply(lookup(name).settings);Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(setGraphicPresetByName,BigWorld)

static PyObject* py_setGraphicPresetByIndex(PyObject* args)
{
    int index;
    if(!PyArg_ParseTuple(args,"i:setGraphicPresetByIndex",&index))return NULL;
    const auto* preset=catalogue.byAuthoredIndex(index);
    if(preset)apply(preset->settings);
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(setGraphicPresetByIndex,BigWorld)

static PyObject* py_applyGraphicPreset(PyObject* args)
{
    BW::string name;PyObject* input;
    if(!PyArg_ParseTuple(args,"O:applyGraphicPreset",&input) || Script::setData(input,name))return NULL;
    if(std::string(name.data(),name.size())!=catalogue.currentKey())apply(lookup(name).settings);
    Py_RETURN_TRUE;
}
PY_MODULE_FUNCTION(applyGraphicPreset,BigWorld)

static PyObject* py_getGraphicsPresetPropertyNames(PyObject* args)
{
    if(!PyArg_ParseTuple(args,":getGraphicsPresetPropertyNames"))return NULL;
    const auto snapshot=Moo::GraphicsSetting::stockCurrentSettings();
    PyObjectPtr result(PyTuple_New(snapshot.size()),PyObjectPtr::STEAL_REFERENCE);
    if(!result)return NULL;
    for(std::size_t i=0;i<snapshot.size();++i)
    {
        PyObject* name=PyString_FromStringAndSize(snapshot[i].first.data(),snapshot[i].first.size());
        if(!name)return NULL;
        PyTuple_SET_ITEM(result.get(),i,name);
    }
    Py_INCREF(result.get());return result.get();
}
PY_MODULE_FUNCTION(getGraphicsPresetPropertyNames,BigWorld)

namespace
{
bool liveSettingsMatch(const wot::StockGraphicsSettings& expected)
{
    // 91d010: absent live settings remain unmatched. An empty vector matches.
    for(const auto& entry:expected)
    {
        auto setting=Moo::GraphicsSetting::getFromLabel(BW::string(entry.first.data(),entry.first.size()));
        if(!setting || setting->activeOption()!=entry.second)return false;
    }
    return true;
}
}

static PyObject* py_isCustomGraphicPreset(PyObject* args)
{
    if(!PyArg_ParseTuple(args,":isCustomGraphicPreset"))return NULL;
    for(const auto& preset:catalogue.presets())
        if(liveSettingsMatch(preset.second.settings))Py_RETURN_FALSE;
    Py_RETURN_TRUE;
}
PY_MODULE_FUNCTION(isCustomGraphicPreset,BigWorld)

static PyObject* py_canToDowngradePreset(PyObject* args)
{
    if(!PyArg_ParseTuple(args,":canToDowngradePreset"))return NULL;
    if(liveSettingsMatch(lookup("MIN").settings))Py_RETURN_FALSE;
    Py_RETURN_TRUE;
}
PY_MODULE_FUNCTION(canToDowngradePreset,BigWorld)

static PyObject* py_updateCurrentPresetIndex(PyObject* args)
{
    if(!PyArg_ParseTuple(args,":updateCurrentPresetIndex"))return NULL;
    catalogue.publish(toStock(Moo::GraphicsSetting::stockCurrentSettings()));
    auto setting=Moo::GraphicsSetting::getFromLabel("presets");
    if(setting)
    {
        // 91f450 reloads the authored catalogue before syncing the registered
        // option. Its select callback publishes that option's complete vector.
        loadCatalogue();
        setting->selectOption(catalogue.lookup(catalogue.currentKey()).authoredIndex,true);
    }
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(updateCurrentPresetIndex,BigWorld)
BW_END_NAMESPACE
