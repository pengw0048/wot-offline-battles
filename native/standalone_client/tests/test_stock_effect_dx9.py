"""Exercise the production resource binder with independently numbered textures and samplers."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import os


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
using D3DPOOL = int;
#define FAILED(value) ((value) < 0)
#define SUCCEEDED(value) ((value) >= 0)
constexpr DWORD D3DVERTEXTEXTURESAMPLER0 = 257;
constexpr int D3DDEVTYPE_HAL = 1, D3DUSAGE_QUERY_VERTEXTEXTURE = 0x100000;
constexpr int D3DRTYPE_TEXTURE = 3, D3DFMT_R32F = 114;
constexpr int D3DRTYPE_CUBETEXTURE = 5, D3DRTYPE_VOLUMETEXTURE = 4;
constexpr D3DPOOL D3DPOOL_DEFAULT = 0, D3DPOOL_MANAGED = 1, D3DPOOL_SYSTEMMEM = 2;
struct D3DSURFACE_DESC { D3DPOOL Pool = D3DPOOL_DEFAULT; };
struct D3DVOLUME_DESC { D3DPOOL Pool = D3DPOOL_DEFAULT; };
struct D3DDEVICE_CREATION_PARAMETERS { DWORD AdapterOrdinal = 0; };
struct D3DDISPLAYMODE { D3DFORMAT Format = 21; };
struct Object {
    unsigned refs = 1;
    virtual ~Object() = default;
    void AddRef() { ++refs; }
    void Release() { if (!--refs) delete this; }
};
struct IDirect3DBaseTexture9 : Object { virtual int GetType() { return D3DRTYPE_TEXTURE; } };
struct IDirect3DTexture9 : IDirect3DBaseTexture9 {
    D3DPOOL pool = D3DPOOL_DEFAULT;
    HRESULT GetLevelDesc(unsigned, D3DSURFACE_DESC* out) { out->Pool = pool; return 0; }
};
struct IDirect3DCubeTexture9 : IDirect3DBaseTexture9 {
    D3DPOOL pool = D3DPOOL_DEFAULT;
    int GetType() override { return D3DRTYPE_CUBETEXTURE; }
    HRESULT GetLevelDesc(unsigned, D3DSURFACE_DESC* out) { out->Pool = pool; return 0; }
};
struct IDirect3DVolumeTexture9 : IDirect3DBaseTexture9 {
    D3DPOOL pool = D3DPOOL_DEFAULT;
    int GetType() override { return D3DRTYPE_VOLUMETEXTURE; }
    HRESULT GetLevelDesc(unsigned, D3DVOLUME_DESC* out) { out->Pool = pool; return 0; }
};
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
#include <algorithm>
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
void deviceLoss() {
    auto effect = std::make_shared<wot::StockEffectBinary>();
    wot::StockEffectType textureType; textureType.basic_type = 5;
    effect->types.push_back(textureType);effect->resource_views.resize(5);
    auto* device = new IDirect3DDevice9;
    auto* states = new ID3DXEffectStateManager;
    std::shared_ptr<wot::StockDx9EffectRuntime> runtime;
    std::string error;
    check(wot::StockDx9EffectRuntime::create(device,states,effect,runtime,error),error.c_str());
    wot::StockEffectUniformPool pool;wot::StockEffectUniformContext context(pool);
    auto* depth = new IDirect3DTexture9;
    auto* cube = new IDirect3DCubeTexture9;
    auto* volume = new IDirect3DVolumeTexture9;
    auto* managed = new IDirect3DTexture9;managed->pool = D3DPOOL_MANAGED;
    auto* manual = new IDirect3DTexture9;
    {
        wot::StockDx9EffectMaterial definition(runtime,context);
        wot::StockDx9EffectMaterial inactive(runtime,context);
        for (auto* material : {&definition,&inactive}) {
            check(material->set_texture(0,depth,error,true) && material->set_texture(1,cube,error,true) &&
                material->set_texture(2,volume,error,true) && material->set_texture(3,managed,error,true) &&
                material->set_texture(4,manual,error),error.c_str());
        }
        auto retired = std::make_unique<wot::StockDx9EffectMaterial>(inactive,context);
        check(retired->texture(0)==depth,"Clone must inherit the real automatic texture");
        retired.reset(); // AddressSanitizer checks the removed registration below.
        check(runtime->on_lost_device(error),error.c_str());
        for (auto* material : {&definition,&inactive}) {
            check(material->set_texture(0,depth,error,true) && material->set_texture(1,cube,error,true) &&
                material->set_texture(2,volume,error,true),error.c_str());
        }
        wot::StockDx9EffectMaterial clone(inactive,context);
        check(depth->refs==3 && cube->refs==3 && volume->refs==3,"All bindings retain the actual resources");
        check(runtime->on_lost_device(error),error.c_str());
        for (auto* material : {&definition,&inactive,&clone}) {
            check(!material->texture(0) && !material->texture(1) && !material->texture(2),
                "Device loss must clear automatic DEFAULT 2D, cube and volume refs in every binding");
            check(material->texture(3)==managed && material->texture(4)==manual,
                "Managed automatic and manually owned textures retain their lifecycle");
        }
        check(depth->refs==1 && cube->refs==1 && volume->refs==1,
            "Inactive and cloned bindings cannot block Reset with stale COM references");
        check(runtime->on_lost_device(error) && depth->refs==1,"Repeated loss is harmless");
        auto* replacement = new IDirect3DTexture9;
        check(inactive.set_texture(0,replacement,error,true),error.c_str());
        check(inactive.texture(0)==replacement && replacement->refs==2,
            "The real semantic provider may republish a new target after Reset");
        check(runtime->on_lost_device(error) && replacement->refs==1,
            "Republished targets participate in the next loss");
        replacement->Release();
        // A manual write supersedes automatic provenance even on the same slot.
        check(inactive.set_texture(0,depth,error,true) && inactive.set_texture(0,manual,error),error.c_str());
        check(runtime->on_lost_device(error) && inactive.texture(0)==manual,
            "A private parameter write must not be released as an automatic target");
    }
    check(managed->refs==1 && manual->refs==1,"Material destruction releases preserved references once");
    check(runtime->on_lost_device(error),"Destroyed materials must leave no dangling registry entry");
    depth->Release();cube->Release();volume->Release();managed->Release();manual->Release();
    runtime.reset();device->Release();states->Release();
}
// ADAPTER_BRIDGE
int main() {
    try {
        for (unsigned stage : {1u, 2u}) for (bool hole : {false, true})
            for (bool distinct : {false, true}) exercise(stage, hole, distinct);
        deviceLoss();
        bridgeDeviceLoss();
        std::cout << "Production DX9 binding passed both stages, shared/distinct samplers, and sparse slots\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''

ADAPTER_BRIDGE = r'''
#define STDMETHODCALLTYPE
using D3DXHANDLE = const char*;
constexpr HRESULT S_OK=0,E_INVALIDARG=-1,E_FAIL=-2;
using wot::StockDx9EffectRuntime;
enum class StockEffectParameterKind {resource_view};
struct Reference {StockEffectParameterKind kind=StockEffectParameterKind::resource_view;std::size_t owner=1;};
struct Reflection {
    Reference value;
    const Reference* parameter_reference(unsigned) {return &value;}
};
struct SharedContext {
    struct Impl {
        std::recursive_mutex mutex;
        std::vector<std::weak_ptr<StockDx9EffectRuntime>> runtimes;
    };
    std::unique_ptr<Impl> impl_=std::make_unique<Impl>();
};
// Reflection lookup is the boundary; SetTexture and OnLostDevice below are
// copied verbatim from production, including automatic-producer provenance.
class StockEffectAdapter {
public:
    struct Destination {bool resource=true;std::size_t owner=0;};
    std::shared_ptr<StockDx9EffectRuntime> runtime_;
    SharedContext& context_;
    std::unique_ptr<wot::StockDx9EffectMaterial> material_;
    Reflection reflection_;
    Destination automatic;
    std::optional<unsigned> pass_;
    bool begun_=false;
    std::string capture_error_;
    explicit StockEffectAdapter(SharedContext& context):context_(context) {}
    const Destination* destination(D3DXHANDLE handle) {
        return handle==reinterpret_cast<D3DXHANDLE>(&automatic)?&automatic:nullptr;
    }
    unsigned parameter_number(D3DXHANDLE) {return 0;}
    HRESULT SetTexture(D3DXHANDLE,IDirect3DBaseTexture9*);
    HRESULT OnLostDevice();
    void register_runtime();
};
// PRODUCTION_ADAPTER_METHODS
void bridgeDeviceLoss() {
    auto effect=std::make_shared<wot::StockEffectBinary>();
    wot::StockEffectType textureType;textureType.basic_type=5;
    effect->types.push_back(textureType);effect->resource_views.resize(2);
    auto* device=new IDirect3DDevice9;
    auto* states=new ID3DXEffectStateManager;
    std::shared_ptr<StockDx9EffectRuntime> runtime;
    std::string error;
    check(StockDx9EffectRuntime::create(device,states,effect,runtime,error),error.c_str());
    SharedContext sharedContext;
    // A stale Properties object still owns this runtime after ManagedEffect
    // has reloaded its Definition. Only the new Definition receives loss.
    std::shared_ptr<StockDx9EffectRuntime> reloaded;
    check(StockDx9EffectRuntime::create(device,states,effect,reloaded,error),error.c_str());
    wot::StockEffectUniformPool pool;wot::StockEffectUniformContext context(pool);
    auto* shared=new IDirect3DTexture9;
    auto* privateTexture=new IDirect3DTexture9;
    {
        StockEffectAdapter definition(sharedContext),binding(sharedContext);
        for (auto* adapter:{&definition,&binding}) {
            adapter->runtime_=adapter==&definition?reloaded:runtime;
            adapter->register_runtime();adapter->register_runtime();
            adapter->material_=std::make_unique<wot::StockDx9EffectMaterial>(adapter->runtime_,context);
            check(adapter->SetTexture(reinterpret_cast<D3DXHANDLE>(&adapter->automatic),shared)==S_OK,
                "Actual provider SetTexture must retain automatic provenance");
            check(adapter->SetTexture("private",privateTexture)==S_OK,
                "Actual named material SetTexture remains manually owned");
        }
        check(sharedContext.impl_->runtimes.size()==2,"Material clones must reuse the runtime registration");
        {
            StockEffectAdapter retired(sharedContext);
            check(StockDx9EffectRuntime::create(device,states,effect,retired.runtime_,error),error.c_str());
            retired.register_runtime();
        }
        definition.pass_=0;definition.begun_=true;
        check(definition.OnLostDevice()==S_OK && !definition.pass_ && !definition.begun_,
            "Actual adapter loss closes its pass state and notifies the runtime");
        check(!binding.material_->texture(0)&&binding.material_->texture(1)==privateTexture,
            "Definition notification must reach an inactive binding without losing manual data");
        check(shared->refs==1,"No material may retain the retired automatic target");
        check(sharedContext.impl_->runtimes.size()==2,"Expired runtimes must be removed without retaining them");
        check(definition.OnLostDevice()==S_OK,"Definition loss may be repeated");
    }
    check(shared->refs==1&&privateTexture->refs==1,"All bridge references settle on teardown");
    shared->Release();privateTexture->Release();runtime.reset();device->Release();states->Release();
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
            source = (ROOT / "bigworld/stock_effect_adapter.cpp").read_text()
            methods = []
            first = source.index("void StockEffectAdapter::register_runtime()")
            last = source.index("void StockEffectAdapter::initialise()", first)
            methods.append(source[first:last])
            for begin, end in (("SetTexture(", "GetTexture("),
                               ("OnLostDevice()", "OnResetDevice()")):
                first = source.index("HRESULT STDMETHODCALLTYPE StockEffectAdapter::" + begin)
                last = source.rindex("HRESULT STDMETHODCALLTYPE", first,
                    source.index("StockEffectAdapter::" + end, first))
                methods.append(source[first:last])
            bridge = ADAPTER_BRIDGE.replace("// PRODUCTION_ADAPTER_METHODS", "\n".join(methods))
            (root / "probe.cpp").write_text(PROBE.replace("// ADAPTER_BRIDGE", bridge))
            binary = root / "probe"
            # The Windows CI's MinGW distribution has no ASan/UBSan runtime.
            # Exercise the same production probe there; instrument it on Unix.
            sanitizers = [] if os.name == "nt" else [
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
            compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            *sanitizers,
                            "-I" + str(root), "-I" + str(ROOT / "include"),
                            "-I" + str(ROOT / "bigworld"), str(root / "probe.cpp"),
                            str(ROOT / "bigworld/stock_effect_dx9.cpp"),
                            str(ROOT / "src/stock_effect_binding.cpp"),
                            str(ROOT / "src/stock_shader_runtime.cpp"),
                            str(ROOT / "src/stock_shader_binary.cpp"),
                            "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True,
                                    env=dict(os.environ, ASAN_OPTIONS="detect_stack_use_after_return=1"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
