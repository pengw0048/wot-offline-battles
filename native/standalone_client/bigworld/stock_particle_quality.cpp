#include "pch.hpp"
#include "stock_particle_quality.hpp"
#include "app.hpp"

#include "resmgr/bwresource.hpp"

BW_BEGIN_NAMESPACE
namespace
{
wot::StockParticleQualityProfile readProfile( DataSectionPtr section, bool sniper )
{
    wot::StockParticleQualityProfile value;
    // Exact 00624a00 defaults deliberately differ from the record constructor.
    value.lod_offset = section->readFloat( sniper ? "LOD_SNIPER_OFFSET" : "LOD_OFFSET", 0.f );
    value.lod_multiplier = section->readFloat( sniper ? "LOD_SNIPER_MULT" : "LOD_MULT", 1.f );
    value.time_multiplier = section->readFloat( sniper ? "TIME_SNIPER_MULT" : "TIME_MULT", 1.f );
    value.tessellation_scale = section->readFloat( sniper ?
        "TESSELATION_SNIPER_SCALE_FACTOR" : "TESSELATION_SCALE_FACTOR", 1.f );
    value.tessellation_maximum = section->readInt( sniper ?
        "TESSELATION_SNIPER_MAX_LEVEL" : "TESSELATION_MAX_LEVEL", 0 );
    value.render_quality = section->readInt( sniper ? "RENDER_SNIPER_QUALITY" : "RENDER_QUALITY", 0 );
    return value;
}
}

wot::StockParticleQuality & stockParticleQuality()
{
    // f9ade0 owns one manager, shared by both ordinary and sniper settings.
    static wot::StockParticleQuality value;
    return value;
}

bool initialiseStockParticleQualityProfiles()
{
    DataSectionPtr graphics = BWResource::openSection( s_graphicsSettingsXML.value() );
    DataSectionPtr profiles = graphics ? graphics->openSection( "PS_settings" ) : NULL;
    // Original 624a00 accepts a missing section and retains manager defaults.
    if (!profiles)
        return true;
    BW::vector<DataSectionPtr> options;
    profiles->openSections( "option", options );
    for (const auto & option : options)
    {
        const BW::string label = option->readString( "label", "" );
        const std::string name( label.c_str(), label.size() );
        stockParticleQuality().register_profile( name, false, readProfile( option, false ) );
        stockParticleQuality().register_profile( name, true, readProfile( option, true ) );
    }
    return true;
}
BW_END_NAMESPACE
