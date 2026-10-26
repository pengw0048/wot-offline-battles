#ifndef WOT_STOCK_DEFERRED_LIGHTING_PORTABLE
#include "pch.hpp"
#include "stock_deferred_lighting.hpp"
#include "moo/effect_visual_context.hpp"
#include "moo/render_context.hpp"
#include "moo/texture_manager.hpp"
#include "resmgr/bwresource.hpp"
#endif

BW_BEGIN_NAMESPACE
void packStockDeferredSphericalHarmonics( const Vector3 (&source)[9], float (&packed)[28] )
{
    // #1513 PBSLightingSupport::load (a750cb..a75258) packs each colour's
    // quadratic terms, followed by the RGB sh8 term. These are already baked
    // SH coefficients; no guessed basis, normalisation or ambient scaling.
    for (unsigned colour = 0; colour < 3; ++colour)
    {
        packed[colour * 4] = source[0][colour] - source[6][colour];
        packed[colour * 4 + 1] = source[1][colour];
        packed[colour * 4 + 2] = source[2][colour];
        packed[colour * 4 + 3] = source[3][colour];
        packed[12 + colour * 4] = source[4][colour];
        packed[13 + colour * 4] = source[5][colour];
        packed[14 + colour * 4] = source[6][colour] * 3.f;
        packed[15 + colour * 4] = source[7][colour];
        packed[24 + colour] = source[8][colour];
    }
    packed[27] = 0.f;
}

#ifndef WOT_STOCK_DEFERRED_LIGHTING_CORE_ONLY
namespace
{
StockDeferredLightingSettings activeSettings;
Moo::BaseTexturePtr brdfLut;
bool constantsRegistered = false;

class SphericalHarmonicsConstant : public Moo::EffectConstantValue
{
public:
    virtual bool operator()( ID3DXEffect * effect, D3DXHANDLE handle )
    {
        // An absent environment is not a black ambient-lighting result.
        return activeSettings.loaded && SUCCEEDED( effect->SetValue(
            handle, activeSettings.sphericalHarmonics, sizeof( activeSettings.sphericalHarmonics ) ) );
    }
};

class BrdfLutConstant : public Moo::EffectConstantValue
{
public:
    virtual bool operator()( ID3DXEffect * effect, D3DXHANDLE handle )
    {
        return brdfLut && brdfLut->pTexture() && SUCCEEDED( effect->SetTexture( handle, brdfLut->pTexture() ) );
    }
};
}

bool loadStockDeferredLightingSettings( DataSectionPtr environment, StockDeferredLightingSettings & settings )
{
    settings.loaded = false;
    if (!environment)
    {
        ERROR_MSG( "Deferred lighting: the original environment resource is unavailable\n" );
        return false;
    }
    // a82950 reads the PBS child; a74bc0 selects the deferred SH resource.
    // a827a0's original default path is empty, with no fabricated black SH.
    const BW::string resource = environment->readString( "PBS/DEFERRED/REM_SH" );
    DataSectionPtr source = resource.empty() ? NULL : BWResource::openSection( resource );
    if (!source)
    {
        ERROR_MSG( "Deferred lighting: cannot load original PBS/DEFERRED/REM_SH '%s'\n", resource.c_str() );
        return false;
    }
    Vector3 coefficients[9];
    const char * const names[] = { "sh0", "sh1", "sh2", "sh3", "sh4", "sh5", "sh6", "sh7", "sh8" };
    for (unsigned i = 0; i < 9; ++i)
        coefficients[i] = source->readVector3( names[i], Vector3( 0.f, 0.f, 0.f ) );
    // The original reader reads then discards max_lum (a750c6/fstp).
    source->readFloat( "max_lum", 1.f );
    packStockDeferredSphericalHarmonics( coefficients, settings.sphericalHarmonics );
    settings.loaded = true;
    return true;
}

void activateStockDeferredLightingSettings( const StockDeferredLightingSettings & settings )
{
    activeSettings = settings;
}

void deactivateStockDeferredLightingSettings()
{
    activeSettings.loaded = false;
}

bool initialiseStockDeferredLightingConstants()
{
    // a748e0 loads this exact resource through the texture manager. It is a
    // lookup texture, not an environment cube or an image generated at runtime.
    const char * resource = "system/maps/env_brdf_lut.dds";
    brdfLut = BWResource::fileExists( resource ) ? Moo::TextureManager::instance()->get(
        resource, true, false, true, "Art/File Textures/" ) : NULL;
    // Do not accept TextureManager's optional missing-texture placeholder as
    // a physical BRDF. The real input must have loaded successfully.
    if (!brdfLut || !brdfLut->pTexture() || brdfLut->status() != Moo::BaseTexture::STATUS_READY)
    {
        ERROR_MSG( "Deferred lighting: the original environment BRDF lookup texture is unavailable\n" );
        return false;
    }
    if (!constantsRegistered)
    {
        Moo::rc().effectVisualContext().registerAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME, "REM_SH9", new SphericalHarmonicsConstant );
        Moo::rc().effectVisualContext().registerAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME, "EnvBRDFLut", new BrdfLutConstant );
        constantsRegistered = true;
    }
    // SpeedTreeMaterials belongs to the actual SpeedTree renderer/type pool.
    // Its public producer is not replaced by an unpopulated synthetic texture.
    return true;
}

void finaliseStockDeferredLightingConstants()
{
    if (constantsRegistered)
    {
        Moo::rc().effectVisualContext().unregisterAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME, "REM_SH9" );
        Moo::rc().effectVisualContext().unregisterAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME, "EnvBRDFLut" );
        constantsRegistered = false;
    }
    brdfLut = NULL;
}
#endif
BW_END_NAMESPACE
