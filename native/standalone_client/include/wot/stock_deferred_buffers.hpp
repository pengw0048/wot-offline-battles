#pragma once

#include <array>

namespace wot
{
enum StockDeferredMaterialCategory
{
    stock_terrain,stock_flora,stock_speedtree,stock_objects,stock_decal,stock_overlay
};

struct StockDeferredMaterialConversion
{
    bool enabled=false;
    std::array<float,4> albedo{{.05f,.95f,.7f,.3f}};
    std::array<float,4> gloss{{.05f,.75f,.5f,2.f}};
    std::array<float,4> metallic{{.02f,.6f,2.f,1.5f}};
};

// a54c90 supplies defaults for each space; a556b0 loads the six categories
// from that space's skyGradientDome/PBSConversions resource. Activation is a
// separate publication step (a55970), so loading another space is not live.
struct StockDeferredMaterialSettings
{
    std::array<StockDeferredMaterialConversion,6> categories;
    float objects_albedo_multiplier=.9f;
    bool sceneConversionEnabled() const
    {
        for (unsigned i=0;i!=4;++i) if(categories[i].enabled)return true;
        return false;
    }
    void useEnvironmentDefaults()
    {
        static const bool enabled[]={false,true,false,true,true,false};
        for(unsigned i=0;i!=6;++i)categories[i].enabled=enabled[i];
    }
};

// 9c3850 reads the deferred pipeline's selected option. TransmissionType's
// 9c3be0 functor clamps its live value and permanently lowers it at quality > 1.
struct StockDeferredLightingSelection
{
    int quality=0;
    int transmission=2; // Original data word 01a1ada0.
    int transmissionType()
    {
        transmission=transmission<0?0:transmission>2?2:transmission;
        if(quality>1)transmission=1;
        return transmission;
    }
};

// a82040/a82070: these are authored environment settings, separate from
// the quality gate in SSShadowsSupport::onQualitySelected (a6a870).
struct StockScreenSpaceShadowSettings
{
    float amplify=1.5f;
    float distance_near=.5f;
    float distance_far=50.f;
    float noise=1.f;
    bool enabled=true;
};

struct StockDepthEffectQuality
{
    int lighting=0;
    int shadows=-1;
    bool ambientOcclusionEnabled() const { return lighting>=0 && lighting<3; }
    bool horizonBasedOcclusion() const { return lighting<2; }
    bool lowQualityOcclusion() const { return lighting>0; }
    bool directHalfResolution() const { return lighting>1; }
    bool contactShadowsEnabled() const { return shadows>=0 && shadows<2; }
    bool lowQualityContactShadows() const { return shadows>0; }
    bool sunContactShadowsEnabled() const { return lighting<3 && contactShadowsEnabled(); }
};
}
