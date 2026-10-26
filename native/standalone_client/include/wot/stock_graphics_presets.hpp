#pragma once

#include <map>
#include <string>
#include <utility>
#include <vector>

namespace wot
{
using StockGraphicsSettings=std::vector<std::pair<std::string,int>>;

struct StockGraphicsPreset
{
    int authoredIndex=0;
    std::string optionName;
    StockGraphicsSettings settings;
};

// #1513 91e8c0 keeps authored option indices separately from the map order
// used by the Python query APIs. Selecting "presets" publishes this vector;
// 91ce80 is the separate, explicit live-setting application boundary.
class StockGraphicsPresets
{
public:
    using Presets=std::map<std::string,StockGraphicsPreset>;
    const Presets& presets()const{return presets_;}
    const StockGraphicsSettings& desired()const{return desired_;}
    bool hasPresetOverload()const{return !overload_.empty();}
    void clear(){presets_.clear();}
    void add(const std::string& name,int index,const StockGraphicsSettings& settings)
    {
        presets_[name]={index,name,settings};
    }
    // 91c760 is map::operator[]: unknown names insert an empty native entry.
    StockGraphicsPreset& lookup(const std::string& name){return presets_[name];}
    const StockGraphicsPreset* byAuthoredIndex(int index)const
    {
        for(const auto& entry:presets_)
            if(entry.second.authoredIndex==index)return &entry.second;
        return nullptr;
    }
    const StockGraphicsPreset* byOrdinal(int index)const
    {
        if(index<0 || static_cast<std::size_t>(index)>=presets_.size())return nullptr;
        auto entry=presets_.begin();
        while(index--)++entry;
        return &entry->second;
    }
    bool desiredOption(const std::string& name,int& option)const
    {
        for(const auto& entry:desired_)
            if(entry.first==name){option=entry.second;return true;}
        return false;
    }
    std::string currentKey(bool ignoreMissing=false)const
    {
        for(const auto& preset:presets_)
        {
            if(preset.first=="CUSTOM")continue;
            bool matches=true;
            for(const auto& expected:preset.second.settings)
            {
                int actual=0;
                if(!desiredOption(expected.first,actual))
                {
                    if(!ignoreMissing){matches=false;break;}
                }
                else if(actual!=expected.second){matches=false;break;}
            }
            if(matches)return preset.first;
        }
        return "CUSTOM";
    }
    void presetOverload(const std::string& name){overload_=name;}
    void publish(const StockGraphicsSettings& settings)
    {
        desired_=settings;
        if(overload_.empty() || overload_==currentKey())return;
        // 91e550 overwrites existing entries from the command-line preset and
        // appends absent entries. It does not call any live-setting callback.
        const StockGraphicsSettings extra=lookup(overload_).settings;
        for(const auto& setting:extra)
        {
            auto found=desired_.begin();
            for(;found!=desired_.end();++found)if(found->first==setting.first)break;
            if(found==desired_.end())desired_.push_back(setting);
            else found->second=setting.second;
        }
    }
private:
    Presets presets_;
    StockGraphicsSettings desired_;
    std::string overload_;
};
}
