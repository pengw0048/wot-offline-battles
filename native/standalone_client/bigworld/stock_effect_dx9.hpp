#pragma once

#include "wot/stock_effect_binding.hpp"
#include "wot/stock_shader_runtime.hpp"

#include <d3d9.h>
#include <d3dx9effect.h>
#include <memory>

namespace wot {

struct StockDx9Release {
    template<class Interface> void operator()(Interface* object) const {
        if (object) object->Release();
    }
};

// Device-owned original ARIS bytecode. This is deliberately separate from
// mutable material bindings and from Moo's vertex/stream/draw ownership.
class StockDx9EffectRuntime {
public:
    static bool create(IDirect3DDevice9* device, ID3DXEffectStateManager* state_manager,
        std::shared_ptr<const StockEffectBinary> definition,
        std::shared_ptr<StockDx9EffectRuntime>& output, std::string& error);
    const std::shared_ptr<const StockEffectBinary>& definition() const { return definition_; }
    IDirect3DDevice9* device() const { return device_.get(); }
    ID3DXEffectStateManager* state_manager() const { return state_manager_.get(); }
private:
    friend class StockDx9EffectMaterial;
    StockDx9EffectRuntime(IDirect3DDevice9* device, ID3DXEffectStateManager* state_manager,
        std::shared_ptr<const StockEffectBinary> definition);
    std::unique_ptr<IDirect3DDevice9, StockDx9Release> device_;
    std::unique_ptr<ID3DXEffectStateManager, StockDx9Release> state_manager_;
    std::shared_ptr<const StockEffectBinary> definition_;
    std::vector<std::unique_ptr<IDirect3DVertexShader9, StockDx9Release>> vertex_shaders_;
    std::vector<std::unique_ptr<IDirect3DPixelShader9, StockDx9Release>> pixel_shaders_;
};

class StockDx9EffectMaterial {
public:
    StockDx9EffectMaterial(std::shared_ptr<StockDx9EffectRuntime> runtime,
                          StockEffectUniformContext& uniform_context);
    StockDx9EffectMaterial(const StockDx9EffectMaterial& source,
                          StockEffectUniformContext& uniform_context);
    StockEffectBinding& binding() { return binding_; }
    bool set_texture(std::size_t resource_view, IDirect3DBaseTexture9* texture, std::string& error);
    IDirect3DBaseTexture9* texture(std::size_t resource_view) const;
    // Applies real VS/PS, numeric registers, render states, textures, and sampler
    // states. The caller keeps ownership of render targets, streams, declaration,
    // instancing, and draw calls, matching Moo's material boundary.
    bool apply(std::size_t technique, std::size_t pass, std::string& error);
private:
    enum class ShaderApply { uniforms, textures, samplers };
    bool apply_shader(std::uint32_t shader, ShaderApply mode, std::string& error);
    std::shared_ptr<StockDx9EffectRuntime> runtime_;
    StockEffectBinding binding_;
    std::vector<std::shared_ptr<IDirect3DBaseTexture9>> textures_;
};

} // namespace wot
