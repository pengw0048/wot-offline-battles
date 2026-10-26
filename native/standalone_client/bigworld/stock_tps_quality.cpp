#include "pch.hpp"
#include "stock_tps_quality.hpp"
#include "app_config.hpp"
#include "moo/graphics_settings.hpp"
#include <memory>

BW_BEGIN_NAMESPACE
namespace
{
// Original 01a5bf70 is an inverse render-group mask, not a Pixie particle cap.
std::uint32_t inverseRenderGroups = 0xffffu;
std::unique_ptr<wot::StockTPSPerformer> performer;
wot::StockTPSPerformanceConfig configuration;

class TPSGraphicsSetting : public Moo::GraphicsSetting
{
public:
    TPSGraphicsSetting( bool performance ) : Moo::GraphicsSetting(
        performance ? "PS_USE_PERFORMANCER" : "SMOKE_ENABLED",
        performance ? "usage perfomancer" : "Scene Smoke", 0, false, false ),
        performance_( performance )
    {
        addOption( "OFF", "Off", true );
        addOption( "ON", "On", true );
    }
    virtual void onOptionSelected( int option )
    {
        if (performance_)
        {
            // 00971060 is a tail jump to 00fac5c0: OFF does not create it.
            if (option) ensureStockTPSPerformer();
            if (performer) performer->enabled( option != 0 );
        }
        else if (option)
        {
            // 00970f60: ON clears the inverse mask. OFF preserves the
            // existing native mask rather than inventing an all-groups reset.
            inverseRenderGroups = 0;
        }
    }
private:
    bool performance_;
};

bool registerTPSSetting( bool performance )
{
    const char * label = performance ? "PS_USE_PERFORMANCER" : "SMOKE_ENABLED";
    Moo::GraphicsSetting::GraphicsSettingPtr previous = Moo::GraphicsSetting::getFromLabel( label );
    if (previous)
    {
        if (dynamic_cast<TPSGraphicsSetting *>( previous.getObject() )) return true;
        PyErr_Format( PyExc_RuntimeError, "Original graphics setting '%s' already has another owner", label );
        return false;
    }
    Moo::GraphicsSetting::add( new TPSGraphicsSetting( performance ) );
    return true;
}
}

bool initialiseStockTPSQuality()
{
    DataSectionPtr root = AppConfig::instance().pRoot();
    if (root)
    {
        configuration.fixedFPS = root->readFloat( "ParticleSystem/PS_PERFORMANCE_FIXED_FPS", 50.f );
        configuration.minimumFPS = root->readFloat( "ParticleSystem/PS_PERFORMANCE_MIN_FPS", 3.f );
        configuration.maximumFPS = root->readFloat( "ParticleSystem/PS_PERFORMANCE_MAX_FPS", 15.f );
        configuration.speedMultiplier = root->readFloat( "ParticleSystem/PS_PERFORMANCE_SPEED_MULT", 1.f );
        configuration.lodOffset = root->readFloat( "ParticleSystem/PS_PERFORMANCE_LOD_OFFSET", 0.f );
        configuration.lodMultiplier = root->readFloat( "ParticleSystem/PS_PERFORMANCE_LOD_MULT", 1.f );
    }
    if (performer) performer->configure( configuration );
    return registerTPSSetting( false ) && registerTPSSetting( true );
}
void ensureStockTPSPerformer()
{
    if (!performer)
    {
        performer.reset( new wot::StockTPSPerformer() );
        performer->configure( configuration );
    }
}
wot::StockTPSPerformer * stockTPSPerformer() { return performer.get(); }
void tickStockTPSQuality( float elapsed ) { if (performer) performer->tick( elapsed ); }
void finiStockTPSQuality() { performer.reset(); }
std::uint32_t stockTPSInverseRenderGroups() { return inverseRenderGroups; }
bool stockTPSGroupDisabled( std::uint32_t inverseGroups )
{
    // 00fab2e0 / MetaParticleSystem::clone 009e3a70.
    return (inverseGroups & inverseRenderGroups) != 0;
}
BW_END_NAMESPACE
