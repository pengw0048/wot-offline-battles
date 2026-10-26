#ifndef WOT_STOCK_DEFERRED_LEGACY_DECAL_FXH
#define WOT_STOCK_DEFERRED_LEGACY_DECAL_FXH

float4 g_albedoConversions = float4(.05f,.95f,.7f,.3f);
float4 g_glossConversions = float4(.05f,.75f,.5f,2.f);
float4 g_metallicConversions = float4(.02f,.6f,2.f,1.5f);
texture stockDecalObjectKinds;
sampler stockDecalObjectKindsSampler = sampler_state
{
    Texture = (stockDecalObjectKinds);
    MINFILTER = POINT; MAGFILTER = POINT; MIPFILTER = NONE;
    ADDRESSU = CLAMP; ADDRESSV = CLAMP;
};

// Original wg_deferred_decal DX9 PS11, instructions 957..1054. Public
// AM/normal decals have no GMM; their real gloss source is normal-map alpha.
void stockConvertLegacyDecal(inout float3 albedo, float glossSource, out float3 material)
{
    float3 converted = g_albedoConversions.x +
        g_albedoConversions.y * pow(albedo,g_albedoConversions.z);
    float luminance = dot(converted,float3(.2125f,.7154f,.0721f));
    float3 source = albedo;
    albedo = max(converted + g_albedoConversions.w * (converted-luminance),0.f);
    material.y = pow(g_glossConversions.x +
        g_glossConversions.y * pow(glossSource,g_glossConversions.z),.625f);
    material.x = pow(dot(source,float3(1.f,1.f,1.f))*g_metallicConversions.w,g_glossConversions.w) *
        pow(g_metallicConversions.x+g_metallicConversions.y*pow(glossSource,g_metallicConversions.z),.45f);
    material.z = 0.f;
}
#endif
