"""Exercise the production resource binder with independently numbered textures and samplers."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
DEVICE = r'''
#pragma once
#include <cstdint>
#include <map>
#include <tuple>
using DWORD = std::uint32_t;
using BOOL = int;
using HRESULT = std::int32_t;
using D3DFORMAT = int;
using D3DRENDERSTATETYPE = int;
using D3DSAMPLERSTATETYPE = int;
#define FAILED(value) ((value) < 0)
#define SUCCEEDED(value) ((value) >= 0)
constexpr DWORD D3DVERTEXTEXTURESAMPLER0 = 257;
constexpr int D3DDEVTYPE_HAL = 1, D3DUSAGE_QUERY_VERTEXTEXTURE = 0x100000;
constexpr int D3DRTYPE_TEXTURE = 3, D3DFMT_R32F = 114;
struct D3DDEVICE_CREATION_PARAMETERS { DWORD AdapterOrdinal = 0; };
struct D3DDISPLAYMODE { D3DFORMAT Format = 21; };
struct Object {
    unsigned refs = 1;
    virtual ~Object() = default;
    void AddRef() { ++refs; }
    void Release() { if (!--refs) delete this; }
};
struct IDirect3DBaseTexture9 : Object {};
struct IDirect3DVertexShader9 : Object {};
struct IDirect3DPixelShader9 : Object {};
struct IDirect3D9 : Object {
    unsigned checks = 0;
    HRESULT GetAdapterDisplayMode(DWORD, D3DDISPLAYMODE*) { return 0; }
    HRESULT CheckDeviceFormat(DWORD, int, D3DFORMAT, int usage, int type, D3DFORMAT format) {
        ++checks;
        return usage == D3DUSAGE_QUERY_VERTEXTEXTURE && type == D3DRTYPE_TEXTURE && format == D3DFMT_R32F ? 0 : -1;
    }
};
struct IDirect3DDevice9 : Object {
    IDirect3D9* direct3d = new IDirect3D9;
    ~IDirect3DDevice9() { direct3d->Release(); }
    HRESULT GetCreationParameters(D3DDEVICE_CREATION_PARAMETERS*) { return 0; }
    HRESULT GetDirect3D(IDirect3D9** output) { direct3d->AddRef(); *output = direct3d; return 0; }
    HRESULT CreateVertexShader(const DWORD*, IDirect3DVertexShader9** out) { *out = new IDirect3DVertexShader9; return 0; }
    HRESULT CreatePixelShader(const DWORD*, IDirect3DPixelShader9** out) { *out = new IDirect3DPixelShader9; return 0; }
};
struct ID3DXEffectStateManager : Object {
    std::map<DWORD, IDirect3DBaseTexture9*> textures;
    std::map<std::pair<DWORD, int>, DWORD> samplers;
    HRESULT SetVertexShader(IDirect3DVertexShader9*) { return 0; }
    HRESULT SetPixelShader(IDirect3DPixelShader9*) { return 0; }
    HRESULT SetVertexShaderConstantB(DWORD, const BOOL*, DWORD) { return 0; }
    HRESULT SetPixelShaderConstantB(DWORD, const BOOL*, DWORD) { return 0; }
    HRESULT SetVertexShaderConstantI(DWORD, const int*, DWORD) { return 0; }
    HRESULT SetPixelShaderConstantI(DWORD, const int*, DWORD) { return 0; }
    HRESULT SetVertexShaderConstantF(DWORD, const float*, DWORD) { return 0; }
    HRESULT SetPixelShaderConstantF(DWORD, const float*, DWORD) { return 0; }
    HRESULT SetRenderState(int, DWORD) { return 0; }
    HRESULT SetTexture(DWORD slot, IDirect3DBaseTexture9* texture) { textures[slot] = texture; return 0; }
    HRESULT SetSamplerState(DWORD slot, int state, DWORD value) { samplers[{slot, state}] = value; return 0; }
};
'''
PROBE = r'''
#include "stock_effect_dx9.hpp"
#include <iostream>
#include <stdexcept>
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
void exercise(unsigned stage, bool hole, bool distinctSamplers) {
    auto effect = std::make_shared<wot::StockEffectBinary>();
    wot::StockEffectType textureType; textureType.basic_type = 5;
    effect->types.push_back(textureType);
    effect->resource_views.resize(3);
    effect->sampler_states = {{{0, 0, 1, 0, 0}}, {{21, 0, 1, 0, 0}}};
    effect->resource_indirections[0] = distinctSamplers ?
        std::vector<std::uint32_t>{1, 0} : std::vector<std::uint32_t>{1};
    effect->resource_indirections[1] = {2, 0};
    effect->resource_indirections[5] = {0, 1};
    effect->shaders.resize(2);
    effect->shaders[0].stage = 1; effect->shaders[1].stage = 2;
    for (auto& shader : effect->shaders) shader.bytecode = {0xfffe0300, 0xffff};
    const unsigned last = hole ? 2 : 1;
    // b3e080 groups 1/2 become sampler/SRV vectors. b3b3d0 resolves group 1
    // through sampler RTB; b3e9a0/b3b1f0 resolve group 2 through texture RTB.
    effect->shaders[stage-1].resources = {{2, 0, 0}, {1, 0, 0},
        {2, 1, last}, {1, distinctSamplers ? 1u : 0u, last}};
    wot::StockEffectPass pass;
    pass.descriptor = {0xffffffff, 1, 0, 0xffffffff, 0xffffffff, 0xffffffff, 0xffffffff};
    effect->techniques.push_back({"COLOR", 0xffffffff, {pass}});
    auto* device = new IDirect3DDevice9;
    auto* states = new ID3DXEffectStateManager;
    std::shared_ptr<wot::StockDx9EffectRuntime> runtime;
    std::string error;
    check(wot::StockDx9EffectRuntime::create(device, states, effect, runtime, error), error.c_str());
    check(device->direct3d->checks == (stage == 1 ? 1u : 0u), "Vertex sampling must check native capabilities");
    wot::StockEffectUniformPool pool; wot::StockEffectUniformContext context(pool);
    auto* first = new IDirect3DBaseTexture9;
    auto* second = new IDirect3DBaseTexture9;
    {
        wot::StockDx9EffectMaterial material(runtime, context);
        check(material.set_texture(2, first, error) && material.set_texture(0, second, error), error.c_str());
        check(!material.set_texture(3, first, error), "Invalid texture owner must be rejected");
        first->Release(); second->Release();
        const DWORD base = stage == 1 ? D3DVERTEXTEXTURESAMPLER0 : 0;
        states->textures[base+1] = first;
        states->samplers[{base+1, 99}] = 123;
        check(material.apply(0, 0, error), error.c_str());
        check(states->textures.at(base) == first && states->textures.at(base+last) == second,
              "Independent SRVs must retain separate texture bindings");
        if (hole) {
            check(states->textures.at(base+1) == nullptr, "SRV holes must clear previous textures");
            check(states->samplers.at({base+1, 99}) == 123, "Absent sampler slots retain native state");
        }
        for (unsigned slot : {0u, last}) {
            const auto expected = wot::stock_dx9_sampler_states(effect->sampler_states[slot && distinctSamplers ? 0 : 1]);
            for (const auto& state : expected)
                check(states->samplers.at({base+slot, static_cast<int>(state.state)}) == state.value,
                      "Sampler state must resolve its own independent RTB");
        }
        check(material.set_texture(2, nullptr, error) && material.apply(0, 0, error), error.c_str());
        check(states->textures.at(base) == nullptr && states->textures.at(base+last) == second,
              "Clearing one texture must preserve the other binding");
    }
    runtime.reset(); device->Release(); states->Release();
}
int main() {
    try {
        for (unsigned stage : {1u, 2u}) for (bool hole : {false, true})
            for (bool distinct : {false, true}) exercise(stage, hole, distinct);
        std::cout << "Production DX9 binding passed both stages, shared/distinct samplers, and sparse slots\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


class StockDx9ResourceTests(unittest.TestCase):
    def test_production_resource_tables_are_independent(self):
        compiler = shutil.which("c++")
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="wot-dx9-bindings-") as directory:
            root = Path(directory)
            (root / "d3d9.h").write_text(DEVICE)
            (root / "d3dx9effect.h").write_text('#include "d3d9.h"\n')
            (root / "probe.cpp").write_text(PROBE)
            binary = root / "probe"
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(root), "-I" + str(ROOT / "include"),
                            "-I" + str(ROOT / "bigworld"), str(root / "probe.cpp"),
                            str(ROOT / "bigworld/stock_effect_dx9.cpp"),
                            str(ROOT / "src/stock_effect_binding.cpp"),
                            str(ROOT / "src/stock_shader_runtime.cpp"),
                            str(ROOT / "src/stock_shader_binary.cpp"),
                            "-o", str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
