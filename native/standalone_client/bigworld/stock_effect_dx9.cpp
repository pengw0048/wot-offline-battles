#include "stock_effect_dx9.hpp"

#include <algorithm>
#include <sstream>
#include <stdexcept>

namespace wot {
namespace {
bool failed(HRESULT result, const char* operation, std::string& error) {
    if (SUCCEEDED(result)) return false;
    std::ostringstream message;
    message << operation << " failed (0x" << std::hex << static_cast<std::uint32_t>(result) << ')';
    error = message.str(); return true;
}
bool invalid(const char* message, std::string& error) { error = message; return false; }
std::shared_ptr<const StockEffectBinary> required_definition(
    const std::shared_ptr<StockDx9EffectRuntime>& runtime) {
    if (!runtime) throw std::invalid_argument("Material requires an effect runtime");
    return runtime->definition();
}
bool check_vertex_textures(IDirect3DDevice9* device, std::string& error) {
    // b2f9b0 counts vertex sampler bindings; b2fad0 requires the adapter's actual
    // D3DUSAGE_QUERY_VERTEXTEXTURE / R32F capability before creating that VS.
    D3DDEVICE_CREATION_PARAMETERS creation{};
    if (failed(device->GetCreationParameters(&creation), "GetCreationParameters", error)) return false;
    IDirect3D9* object = nullptr;
    if (failed(device->GetDirect3D(&object), "GetDirect3D", error)) return false;
    std::unique_ptr<IDirect3D9, StockDx9Release> direct3d(object);
    D3DDISPLAYMODE mode{};
    if (failed(direct3d->GetAdapterDisplayMode(creation.AdapterOrdinal, &mode),
               "GetAdapterDisplayMode", error)) return false;
    return !failed(direct3d->CheckDeviceFormat(creation.AdapterOrdinal, D3DDEVTYPE_HAL, mode.Format,
        D3DUSAGE_QUERY_VERTEXTEXTURE, D3DRTYPE_TEXTURE, D3DFMT_R32F),
        "CheckDeviceFormat(vertex texture R32F)", error);
}
} // namespace

StockDx9EffectRuntime::StockDx9EffectRuntime(IDirect3DDevice9* device, ID3DXEffectStateManager* state_manager,
    std::shared_ptr<const StockEffectBinary> definition)
    : device_(device), state_manager_(state_manager), definition_(std::move(definition)) {
    device->AddRef(); state_manager->AddRef();
}

bool StockDx9EffectRuntime::create(IDirect3DDevice9* device, ID3DXEffectStateManager* state_manager,
    std::shared_ptr<const StockEffectBinary> definition,
    std::shared_ptr<StockDx9EffectRuntime>& output, std::string& error) {
    if (!device || !state_manager || !definition) return invalid("Effect runtime requires a device, state manager, and definition", error);
    auto runtime = std::shared_ptr<StockDx9EffectRuntime>(new StockDx9EffectRuntime(device, state_manager, definition));
    runtime->vertex_shaders_.resize(definition->shaders.size());
    runtime->pixel_shaders_.resize(definition->shaders.size());
    for (std::size_t i = 0; i < definition->shaders.size(); ++i) {
        const auto& shader = definition->shaders[i];
        if (shader.bytecode.empty()) return invalid("Original shader has no D3D9 bytecode", error);
        const auto* code = reinterpret_cast<const DWORD*>(shader.bytecode.data());
        if (shader.stage == 1) {
            if (std::any_of(shader.resources.begin(), shader.resources.end(),
                    [](const StockShaderResourceBinding& resource) { return resource.kind == 1; }) &&
                !check_vertex_textures(device, error)) return false;
            IDirect3DVertexShader9* object = nullptr;
            const auto result = device->CreateVertexShader(code, &object);
            runtime->vertex_shaders_[i].reset(object);
            if (failed(result, "CreateVertexShader", error)) return false;
        } else if (shader.stage == 2) {
            IDirect3DPixelShader9* object = nullptr;
            const auto result = device->CreatePixelShader(code, &object);
            runtime->pixel_shaders_[i].reset(object);
            if (failed(result, "CreatePixelShader", error)) return false;
        } else return invalid("Original effect requires an unsupported DX9 stage", error);
    }
    output = std::move(runtime); error.clear(); return true;
}

StockDx9EffectMaterial::StockDx9EffectMaterial(std::shared_ptr<StockDx9EffectRuntime> runtime,
    StockEffectUniformContext& uniform_context) : runtime_(std::move(runtime)),
    binding_(required_definition(runtime_), uniform_context),
    textures_(runtime_->definition_->resource_views.size()),
    automatic_textures_(textures_.size(),false) {
    register_material();
}

StockDx9EffectMaterial::StockDx9EffectMaterial(const StockDx9EffectMaterial& source,
    StockEffectUniformContext& uniform_context) : runtime_(source.runtime_),
    binding_(runtime_->definition_, uniform_context), textures_(source.textures_),
    automatic_textures_(source.automatic_textures_) {
    std::string error;
    if (!binding_.copy_values_from(source.binding_, error)) throw std::runtime_error(error);
    register_material();
}

void StockDx9EffectMaterial::register_material() {
    std::lock_guard<std::mutex> lock(runtime_->materials_mutex_);
    runtime_->materials_.push_back(this);
}

StockDx9EffectMaterial::~StockDx9EffectMaterial() {
    std::lock_guard<std::mutex> lock(runtime_->materials_mutex_);
    auto& materials=runtime_->materials_;
    materials.erase(std::remove(materials.begin(),materials.end(),this),materials.end());
}

bool StockDx9EffectRuntime::on_lost_device(std::string& error) {
    std::lock_guard<std::mutex> lock(materials_mutex_);
    bool success=true;
    for (auto* material:materials_) {
        std::string failure;
        if (!material->release_automatic_device_textures(failure)) {
            if (success) error=failure;
            success=false;
        }
    }
    if (success) error.clear();
    return success;
}

bool StockDx9EffectMaterial::release_automatic_device_textures(std::string& error) {
    for (std::size_t i=0;i<textures_.size();++i) {
        auto* texture=textures_[i].get();
        if (!texture || !automatic_textures_[i]) continue;
        D3DPOOL pool;
        HRESULT result;
        switch (texture->GetType()) {
        case D3DRTYPE_TEXTURE: {
            D3DSURFACE_DESC desc{};
            result=static_cast<IDirect3DTexture9*>(texture)->GetLevelDesc(0,&desc);
            pool=desc.Pool;
            break;
        }
        case D3DRTYPE_CUBETEXTURE: {
            D3DSURFACE_DESC desc{};
            result=static_cast<IDirect3DCubeTexture9*>(texture)->GetLevelDesc(0,&desc);
            pool=desc.Pool;
            break;
        }
        case D3DRTYPE_VOLUMETEXTURE: {
            D3DVOLUME_DESC desc{};
            result=static_cast<IDirect3DVolumeTexture9*>(texture)->GetLevelDesc(0,&desc);
            pool=desc.Pool;
            break;
        }
        default:
            return invalid("Automatic effect texture has an invalid resource type",error);
        }
        if (failed(result,"GetLevelDesc(automatic effect texture)",error)) return false;
        // Managed and manually supplied resources keep their own lifecycle.
        // The real semantic producer republishes a new default-pool target on
        // the next draw after Reset; retaining the old target prevents Reset.
        if (pool==D3DPOOL_DEFAULT) textures_[i].reset();
    }
    error.clear();return true;
}

IDirect3DBaseTexture9* StockDx9EffectMaterial::texture(std::size_t resource_view) const {
    return resource_view < textures_.size() ? textures_[resource_view].get() : nullptr;
}

bool StockDx9EffectMaterial::set_texture(std::size_t resource_view,
    IDirect3DBaseTexture9* texture, std::string& error, bool automatic) {
    const auto& definition = *runtime_->definition_;
    if (resource_view >= definition.resource_views.size())
        return invalid("Texture is outside the effect resource table", error);
    const auto type = definition.resource_views[resource_view].type_index;
    if (type >= definition.types.size() || definition.types[type].basic_type != 5)
        return invalid("Effect resource is not a texture view", error);
    if (texture) texture->AddRef();
    textures_[resource_view] = std::shared_ptr<IDirect3DBaseTexture9>(texture,
        [](IDirect3DBaseTexture9* object) { if (object) object->Release(); });
    automatic_textures_[resource_view]=automatic;
    error.clear(); return true;
}

bool StockDx9EffectMaterial::apply_shader(std::uint32_t shader_index, ShaderApply mode, std::string& error) {
    const auto& definition = *runtime_->definition_;
    const auto& shader = definition.shaders.at(shader_index);
    auto* device = runtime_->state_manager_.get();
    const auto stage_base = shader.stage == 1 ? D3DVERTEXTEXTURESAMPLER0 : 0;
    std::vector<StockShaderUniformView> uniform_views;
    std::vector<std::shared_ptr<StockEffectUniformStorage>> storage_owners;
    std::vector<std::optional<std::uint32_t>> resource_slots;
    std::vector<std::optional<std::uint32_t>> sampler_slots;
    std::uint32_t resource_first = 0xffffffff;
    for (const auto& resource : shader.resources) {
        if (mode == ShaderApply::uniforms && resource.kind != 3) continue;
        if (mode == ShaderApply::textures && resource.kind != 2) continue;
        if (mode == ShaderApply::samplers && resource.kind != 1) continue;
        StockEffectResourceTable table;
        // b3b3d0 resolves group 1 through sampler RTB; b3e9a0/b3b1f0
        // resolve group 2 through SRV RTB. Several SRVs can share one sampler.
        if (resource.kind == 1) table = StockEffectResourceTable::sampler_state;
        else if (resource.kind == 2) table = StockEffectResourceTable::resource_view;
        else if (resource.kind == 3) table = StockEffectResourceTable::uniform_buffer;
        else if (resource.kind == 11) return invalid("DX9 shader requires an unordered view", error);
        else continue; // b3e080 ignores unrecognized resource groups.
        std::optional<std::uint32_t> index;
        if (!resolve_stock_effect_reference(definition, binding_.selection(), table,
                resource.resource_index, index, error)) return false;
        if (!index) return invalid("Shader resource reference is absent", error);
        const auto slot = resource.shader_slot;
        if (resource.kind == 3) {
            if (*index >= definition.uniform_buffers.size())
                return invalid("Shader refers to an invalid uniform buffer", error);
            if (slot >= uniform_views.size()) uniform_views.resize(std::size_t(slot)+1);
            auto storage = binding_.uniform_buffer(*index);
            if (!storage) return invalid("Shader attempts to bind selection data as a UBB", error);
            uniform_views[slot] = {storage->bytes().data(), storage->bytes().size()};
            storage_owners.push_back(std::move(storage));
        } else if (resource.kind == 2) {
            if (*index >= textures_.size()) return invalid("Shader refers to an invalid texture view", error);
            if (slot >= resource_slots.size()) resource_slots.resize(std::size_t(slot)+1);
            resource_slots[slot] = index;
            resource_first = (std::min)(resource_first, slot);
        } else {
            if (*index >= definition.sampler_states.size())
                return invalid("Shader refers to an invalid sampler state", error);
            if (slot >= sampler_slots.size()) sampler_slots.resize(std::size_t(slot)+1);
            sampler_slots[slot] = index;
        }
    }
    if (mode == ShaderApply::uniforms) {
        StockShaderRegisters registers;
        if (!stage_stock_shader_registers(shader, uniform_views, registers, error)) return false;
        for (unsigned kind = 0; kind < registers.size(); ++kind) {
            const auto& block = registers[kind];
            if (!block.range.end) continue;
            const auto count = block.range.end-block.range.first;
            const auto* bytes = block.words.data()+block.range.first*(kind == 0 ? 1 : 4);
            HRESULT result;
            if (kind == 0) result = shader.stage == 1 ?
                device->SetVertexShaderConstantB(block.range.first, reinterpret_cast<const BOOL*>(bytes), count) :
                device->SetPixelShaderConstantB(block.range.first, reinterpret_cast<const BOOL*>(bytes), count);
            else if (kind == 1) result = shader.stage == 1 ?
                device->SetVertexShaderConstantI(block.range.first, reinterpret_cast<const int*>(bytes), count) :
                device->SetPixelShaderConstantI(block.range.first, reinterpret_cast<const int*>(bytes), count);
            else result = shader.stage == 1 ?
                device->SetVertexShaderConstantF(block.range.first, reinterpret_cast<const float*>(bytes), count) :
                device->SetPixelShaderConstantF(block.range.first, reinterpret_cast<const float*>(bytes), count);
            if (failed(result, "SetShaderConstant", error)) return false;
        }
        return true;
    }
    // Native staging allocates zeroed entries between the first and last SRV.
    // Bind those holes as NULL too, so a previous material cannot leak a texture.
    if (resource_first != 0xffffffff) {
        for (std::size_t slot = resource_first; slot < resource_slots.size(); ++slot) {
            auto* texture = resource_slots[slot] ? textures_[*resource_slots[slot]].get() : nullptr;
            if (failed(device->SetTexture(stage_base+static_cast<DWORD>(slot), texture),
                       "SetTexture", error)) return false;
        }
    }
    for (std::size_t slot = 0; slot < sampler_slots.size(); ++slot) {
        if (!sampler_slots[slot]) continue; // b2dc00 leaves an absent sampler unchanged.
        const auto states = stock_dx9_sampler_states(definition.sampler_states[*sampler_slots[slot]]);
        for (const auto& state : states)
            if (failed(device->SetSamplerState(stage_base+static_cast<DWORD>(slot),
                    static_cast<D3DSAMPLERSTATETYPE>(state.state), state.value),
                    "SetSamplerState", error)) return false;
    }
    return true;
}

bool StockDx9EffectMaterial::apply(std::size_t technique, std::size_t pass, std::string& error) {
    StockDx9PassSelection selected;
    if (!binding_.select_pass(technique, pass, selected, error)) return false;
    if (!selected.vertex_shader || !selected.pixel_shader)
        return invalid("DX9 material pass has no vertex or pixel shader", error);
    auto* device = runtime_->state_manager_.get();
    if (failed(device->SetVertexShader(runtime_->vertex_shaders_[*selected.vertex_shader].get()),
               "SetVertexShader", error) ||
        failed(device->SetPixelShader(runtime_->pixel_shaders_[*selected.pixel_shader].get()),
               "SetPixelShader", error)) return false;
    if (!apply_shader(*selected.vertex_shader, ShaderApply::uniforms, error) ||
        !apply_shader(*selected.pixel_shader, ShaderApply::uniforms, error))
        return false;
    const auto descriptor = selected.render_state ?
        runtime_->definition_->render_states[*selected.render_state] : stock_dx9_default_render_descriptor();
    std::vector<StockDx9StateValue> states;
    if (!stock_dx9_render_states(descriptor, states, error)) return false;
    for (const auto& state : states)
        if (failed(device->SetRenderState(static_cast<D3DRENDERSTATETYPE>(state.state), state.value),
                   "SetRenderState", error)) return false;
    for (const auto mode : {ShaderApply::textures, ShaderApply::samplers})
        if (!apply_shader(*selected.vertex_shader, mode, error) ||
            !apply_shader(*selected.pixel_shader, mode, error)) return false;
    error.clear(); return true;
}

} // namespace wot
