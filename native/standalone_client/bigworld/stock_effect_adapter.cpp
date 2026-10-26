#include "stock_effect_adapter.hpp"
#include "stock_effect_dx9.hpp"
#include "wot/stock_effect_reflection.hpp"

#include <algorithm>
#include <cstring>
#include <limits>
#include <map>
#include <mutex>
#include <vector>

namespace wot {
namespace {
const GUID stock_effect_iid = {
    0x3e46f381,
    0x6728,
    0x4f04,
    {0x8c, 0xa2, 0xb2, 0x91, 0x31, 0x59, 0xaa, 0x47}};
std::uint32_t handle_number(D3DXHANDLE handle) {
  const auto value = reinterpret_cast<std::uintptr_t>(handle);
  return value < 0x10000 ? static_cast<std::uint32_t>(value) : 0;
}
D3DXHANDLE effect_handle(std::uint32_t handle) {
  return reinterpret_cast<D3DXHANDLE>(static_cast<std::uintptr_t>(handle));
}
D3DXPARAMETER_TYPE parameter_type(unsigned basic) {
  const D3DXPARAMETER_TYPE types[] = {
      D3DXPT_VOID, D3DXPT_INT,     D3DXPT_INT,    D3DXPT_FLOAT,
      D3DXPT_BOOL, D3DXPT_TEXTURE, D3DXPT_TEXTURE};
  return basic < 7 ? types[basic] : D3DXPT_UNSUPPORTED;
}
D3DXPARAMETER_CLASS parameter_class(unsigned value) {
  const D3DXPARAMETER_CLASS types[] = {D3DXPC_SCALAR, D3DXPC_SCALAR,
                                       D3DXPC_VECTOR, D3DXPC_MATRIX_COLUMNS,
                                       D3DXPC_OBJECT, D3DXPC_STRUCT};
  return value < 6 ? types[value] : D3DXPC_SCALAR;
}
} // namespace

struct StockEffectSharedContext::Impl {
  explicit Impl(StockEffectConstantProvider value,
                std::function<void(const std::string &)> reporter)
      : uniforms(pool), provider(std::move(value)),
        failure_reporter(std::move(reporter)) {}
  std::recursive_mutex mutex;
  bool providers_alive = true;
  StockEffectUniformPool pool;
  StockEffectUniformContext uniforms;
  StockEffectConstantProvider provider;
  std::function<void(const std::string &)> failure_reporter;
  struct Shared {
    std::shared_ptr<const StockEffectBinary> definition;
    std::size_t buffer = 0;
    bool initialised = false;
  };
  std::map<std::size_t, Shared> declarations;
  std::map<std::pair<std::size_t, std::size_t>, std::string> failures;
  // A Properties owner may retain a previous Definition after shader reload.
  // Current Moo Definitions still notify those live runtimes at device loss.
  std::vector<std::weak_ptr<StockDx9EffectRuntime>> runtimes;
};

class StockEffectAdapter final : public ID3DXEffect {
  friend HRESULT create_stock_effect_material(ID3DXEffect *, ID3DXEffect **);

public:
  StockEffectAdapter(std::shared_ptr<StockDx9EffectRuntime> runtime,
                     std::shared_ptr<StockEffectSharedContext> context);
  StockEffectAdapter(const StockEffectAdapter &source);
  // Internal producer writer: no GPU runtime and no successful Begin/Apply.
  StockEffectAdapter(std::shared_ptr<const StockEffectBinary> definition,
                     StockEffectSharedContext &context);
  D3DXHANDLE capture(std::size_t buffer, std::size_t member) const;
  std::string capture_error_;
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **out) override;
  ULONG STDMETHODCALLTYPE AddRef() override;
  ULONG STDMETHODCALLTYPE Release() override;
  HRESULT STDMETHODCALLTYPE GetDesc(D3DXEFFECT_DESC *desc) override;
  HRESULT STDMETHODCALLTYPE GetParameterDesc(D3DXHANDLE parameter,
                                             D3DXPARAMETER_DESC *desc) override;
  HRESULT STDMETHODCALLTYPE GetTechniqueDesc(D3DXHANDLE technique,
                                             D3DXTECHNIQUE_DESC *desc) override;
  HRESULT STDMETHODCALLTYPE GetPassDesc(D3DXHANDLE pass,
                                        D3DXPASS_DESC *desc) override;
  HRESULT STDMETHODCALLTYPE GetFunctionDesc(D3DXHANDLE shader,
                                            D3DXFUNCTION_DESC *desc) override;
  D3DXHANDLE STDMETHODCALLTYPE GetParameter(D3DXHANDLE parameter,
                                            UINT index) override;
  D3DXHANDLE STDMETHODCALLTYPE GetParameterByName(D3DXHANDLE parameter,
                                                  const char *name) override;
  D3DXHANDLE STDMETHODCALLTYPE
  GetParameterBySemantic(D3DXHANDLE parameter, const char *semantic) override;
  D3DXHANDLE STDMETHODCALLTYPE GetParameterElement(D3DXHANDLE parameter,
                                                   UINT index) override;
  D3DXHANDLE STDMETHODCALLTYPE GetTechnique(UINT index) override;
  D3DXHANDLE STDMETHODCALLTYPE GetTechniqueByName(const char *name) override;
  D3DXHANDLE STDMETHODCALLTYPE GetPass(D3DXHANDLE technique,
                                       UINT index) override;
  D3DXHANDLE STDMETHODCALLTYPE GetPassByName(D3DXHANDLE technique,
                                             const char *name) override;
  D3DXHANDLE STDMETHODCALLTYPE GetFunction(UINT index) override;
  D3DXHANDLE STDMETHODCALLTYPE GetFunctionByName(const char *name) override;
  D3DXHANDLE STDMETHODCALLTYPE GetAnnotation(D3DXHANDLE object,
                                             UINT index) override;
  D3DXHANDLE STDMETHODCALLTYPE GetAnnotationByName(D3DXHANDLE object,
                                                   const char *name) override;
  HRESULT STDMETHODCALLTYPE SetValue(D3DXHANDLE parameter, const void *data,
                                     UINT bytes) override;
  HRESULT STDMETHODCALLTYPE GetValue(D3DXHANDLE parameter, void *data,
                                     UINT bytes) override;
  HRESULT STDMETHODCALLTYPE SetBool(D3DXHANDLE parameter, BOOL b) override;
  HRESULT STDMETHODCALLTYPE GetBool(D3DXHANDLE parameter, BOOL *b) override;
  HRESULT STDMETHODCALLTYPE SetBoolArray(D3DXHANDLE parameter, const BOOL *b,
                                         UINT count) override;
  HRESULT STDMETHODCALLTYPE GetBoolArray(D3DXHANDLE parameter, BOOL *b,
                                         UINT count) override;
  HRESULT STDMETHODCALLTYPE SetInt(D3DXHANDLE parameter, INT n) override;
  HRESULT STDMETHODCALLTYPE GetInt(D3DXHANDLE parameter, INT *n) override;
  HRESULT STDMETHODCALLTYPE SetIntArray(D3DXHANDLE parameter, const INT *n,
                                        UINT count) override;
  HRESULT STDMETHODCALLTYPE GetIntArray(D3DXHANDLE parameter, INT *n,
                                        UINT count) override;
  HRESULT STDMETHODCALLTYPE SetFloat(D3DXHANDLE parameter, FLOAT f) override;
  HRESULT STDMETHODCALLTYPE GetFloat(D3DXHANDLE parameter, FLOAT *f) override;
  HRESULT STDMETHODCALLTYPE SetFloatArray(D3DXHANDLE parameter, const FLOAT *f,
                                          UINT count) override;
  HRESULT STDMETHODCALLTYPE GetFloatArray(D3DXHANDLE parameter, FLOAT *f,
                                          UINT count) override;
  HRESULT STDMETHODCALLTYPE SetVector(D3DXHANDLE parameter,
                                      const D3DXVECTOR4 *vector) override;
  HRESULT STDMETHODCALLTYPE GetVector(D3DXHANDLE parameter,
                                      D3DXVECTOR4 *vector) override;
  HRESULT STDMETHODCALLTYPE SetVectorArray(D3DXHANDLE parameter,
                                           const D3DXVECTOR4 *vector,
                                           UINT count) override;
  HRESULT STDMETHODCALLTYPE GetVectorArray(D3DXHANDLE parameter,
                                           D3DXVECTOR4 *vector,
                                           UINT count) override;
  HRESULT STDMETHODCALLTYPE SetMatrix(D3DXHANDLE parameter,
                                      const D3DXMATRIX *matrix) override;
  HRESULT STDMETHODCALLTYPE GetMatrix(D3DXHANDLE parameter,
                                      D3DXMATRIX *matrix) override;
  HRESULT STDMETHODCALLTYPE SetMatrixArray(D3DXHANDLE parameter,
                                           const D3DXMATRIX *matrix,
                                           UINT count) override;
  HRESULT STDMETHODCALLTYPE GetMatrixArray(D3DXHANDLE parameter,
                                           D3DXMATRIX *matrix,
                                           UINT count) override;
  HRESULT STDMETHODCALLTYPE SetMatrixPointerArray(D3DXHANDLE parameter,
                                                  const D3DXMATRIX **matrix,
                                                  UINT count) override;
  HRESULT STDMETHODCALLTYPE GetMatrixPointerArray(D3DXHANDLE parameter,
                                                  D3DXMATRIX **matrix,
                                                  UINT count) override;
  HRESULT STDMETHODCALLTYPE
  SetMatrixTranspose(D3DXHANDLE parameter, const D3DXMATRIX *matrix) override;
  HRESULT STDMETHODCALLTYPE GetMatrixTranspose(D3DXHANDLE parameter,
                                               D3DXMATRIX *matrix) override;
  HRESULT STDMETHODCALLTYPE SetMatrixTransposeArray(D3DXHANDLE parameter,
                                                    const D3DXMATRIX *matrix,
                                                    UINT count) override;
  HRESULT STDMETHODCALLTYPE GetMatrixTransposeArray(D3DXHANDLE parameter,
                                                    D3DXMATRIX *matrix,
                                                    UINT count) override;
  HRESULT STDMETHODCALLTYPE SetMatrixTransposePointerArray(
      D3DXHANDLE parameter, const D3DXMATRIX **matrix, UINT count) override;
  HRESULT STDMETHODCALLTYPE GetMatrixTransposePointerArray(D3DXHANDLE parameter,
                                                           D3DXMATRIX **matrix,
                                                           UINT count) override;
  HRESULT STDMETHODCALLTYPE SetString(D3DXHANDLE parameter,
                                      const char *string) override;
  HRESULT STDMETHODCALLTYPE GetString(D3DXHANDLE parameter,
                                      const char **string) override;
  HRESULT STDMETHODCALLTYPE SetTexture(
      D3DXHANDLE parameter, struct IDirect3DBaseTexture9 *texture) override;
  HRESULT STDMETHODCALLTYPE GetTexture(
      D3DXHANDLE parameter, struct IDirect3DBaseTexture9 **texture) override;
  HRESULT STDMETHODCALLTYPE GetPixelShader(
      D3DXHANDLE parameter, struct IDirect3DPixelShader9 **shader) override;
  HRESULT STDMETHODCALLTYPE GetVertexShader(
      D3DXHANDLE parameter, struct IDirect3DVertexShader9 **shader) override;
  HRESULT STDMETHODCALLTYPE SetArrayRange(D3DXHANDLE parameter, UINT start,
                                          UINT end) override;
  HRESULT STDMETHODCALLTYPE GetPool(ID3DXEffectPool **pool) override;
  HRESULT STDMETHODCALLTYPE SetTechnique(D3DXHANDLE technique) override;
  D3DXHANDLE STDMETHODCALLTYPE GetCurrentTechnique() override;
  HRESULT STDMETHODCALLTYPE ValidateTechnique(D3DXHANDLE technique) override;
  HRESULT STDMETHODCALLTYPE FindNextValidTechnique(
      D3DXHANDLE technique, D3DXHANDLE *next_technique) override;
  BOOL STDMETHODCALLTYPE IsParameterUsed(D3DXHANDLE parameter,
                                            D3DXHANDLE technique) override;
  HRESULT STDMETHODCALLTYPE Begin(UINT *passes, DWORD flags) override;
  HRESULT STDMETHODCALLTYPE BeginPass(UINT pass) override;
  HRESULT STDMETHODCALLTYPE CommitChanges() override;
  HRESULT STDMETHODCALLTYPE EndPass() override;
  HRESULT STDMETHODCALLTYPE End() override;
  HRESULT STDMETHODCALLTYPE
  GetDevice(struct IDirect3DDevice9 **device) override;
  HRESULT STDMETHODCALLTYPE OnLostDevice() override;
  HRESULT STDMETHODCALLTYPE OnResetDevice() override;
  HRESULT STDMETHODCALLTYPE
  SetStateManager(ID3DXEffectStateManager *manager) override;
  HRESULT STDMETHODCALLTYPE
  GetStateManager(ID3DXEffectStateManager **manager) override;
  HRESULT STDMETHODCALLTYPE BeginParameterBlock() override;
  D3DXHANDLE STDMETHODCALLTYPE EndParameterBlock() override;
  HRESULT STDMETHODCALLTYPE
  ApplyParameterBlock(D3DXHANDLE parameter_block) override;
  HRESULT STDMETHODCALLTYPE
  DeleteParameterBlock(D3DXHANDLE parameter_block) override;
  HRESULT STDMETHODCALLTYPE CloneEffect(struct IDirect3DDevice9 *device,
                                        struct ID3DXEffect **effect) override;
  HRESULT STDMETHODCALLTYPE SetRawValue(D3DXHANDLE parameter, const void *data,
                                        UINT byte_offset, UINT bytes) override;

private:
  struct Destination {
    bool resource = false;
    std::size_t owner = 0;
    std::size_t member = 0;
    std::size_t declaration = 0;
  };
  void initialise();
  void register_runtime();
  std::vector<std::string> automatic_semantics() const;
  const Destination *destination(D3DXHANDLE handle) const;
  std::uint32_t parameter_number(D3DXHANDLE handle) const;
  std::uint32_t technique_number(D3DXHANDLE handle) const;
  HRESULT write_capture(const Destination &target, const void *data, UINT size);
  HRESULT write(D3DXHANDLE parameter, const void *data, UINT bytes,
                std::optional<std::uint32_t> type);
  HRESULT read(D3DXHANDLE parameter, void *data, UINT bytes,
               unsigned annotation_type = 0);
  HRESULT apply_current();
  bool update_resources();
  bool validate_shared(const StockDx9PassSelection &selected,
                       std::string &error);
  std::shared_ptr<const StockEffectBinary> definition_;
  std::shared_ptr<StockDx9EffectRuntime> runtime_;
  std::shared_ptr<StockEffectSharedContext> context_owner_;
  StockEffectSharedContext &context_;
  StockEffectReflection reflection_;
  std::unique_ptr<StockDx9EffectMaterial> material_;
  std::vector<std::unique_ptr<Destination>> destinations_;
  LONG references_ = 1;
  D3DXHANDLE technique_ = nullptr;
  bool begun_ = false;
  std::optional<UINT> pass_;
};

StockEffectAdapter::StockEffectAdapter(
    std::shared_ptr<StockDx9EffectRuntime> runtime,
    std::shared_ptr<StockEffectSharedContext> context)
    : definition_(runtime->definition()), runtime_(std::move(runtime)),
      context_owner_(std::move(context)), context_(*context_owner_),
      reflection_(definition_, automatic_semantics()),
      material_(
          new StockDx9EffectMaterial(runtime_, context_.impl_->uniforms)) {
  initialise();
}
StockEffectAdapter::StockEffectAdapter(const StockEffectAdapter &source)
    : definition_(source.definition_), runtime_(source.runtime_),
      context_owner_(source.context_owner_), context_(source.context_),
      reflection_(source.reflection_),
      material_(new StockDx9EffectMaterial(*source.material_,
                                           context_.impl_->uniforms)),
      technique_(source.technique_) {
  initialise();
}
StockEffectAdapter::StockEffectAdapter(
    std::shared_ptr<const StockEffectBinary> definition,
    StockEffectSharedContext &context)
    : definition_(std::move(definition)), context_(context),
      reflection_(definition_, {}) {
  initialise();
}
std::vector<std::string> StockEffectAdapter::automatic_semantics() const {
  std::vector<std::string> names;
  if (context_.impl_->provider)
    for (const auto &resource : definition_->resource_views)
      if (resource.semantic_present &&
          context_.impl_->provider(resource.semantic, nullptr, nullptr))
        names.push_back(resource.semantic);
  return names;
}
void StockEffectAdapter::register_runtime() {
  std::lock_guard<std::recursive_mutex> lock(context_.impl_->mutex);
  if (runtime_) {
    auto& runtimes = context_.impl_->runtimes;
    runtimes.erase(std::remove_if(runtimes.begin(), runtimes.end(),
        [](const auto& entry) { return entry.expired(); }), runtimes.end());
    if (std::none_of(runtimes.begin(), runtimes.end(),
        [this](const auto& entry) { return entry.lock() == runtime_; }))
      runtimes.emplace_back(runtime_);
  }
}
void StockEffectAdapter::initialise() {
  register_runtime();
  std::lock_guard<std::recursive_mutex> lock(context_.impl_->mutex);
  if (!technique_)
    technique_ = effect_handle(reflection_.technique(0));
  for (std::size_t i = 0; i < definition_->uniform_buffers.size(); ++i) {
    const auto &buffer = definition_->uniform_buffers[i];
    if (!(buffer.flags & 1))
      continue;
    const auto declaration = context_.impl_->pool.register_buffer(buffer);
    if (runtime_)
      context_.impl_->declarations.emplace(
          declaration, StockEffectSharedContext::Impl::Shared{definition_, i});
    for (std::size_t j = 0; j < buffer.uniforms.size(); ++j)
      destinations_.emplace_back(new Destination{false, i, j, declaration});
  }
  if (runtime_ && context_.impl_->provider)
    for (std::size_t i = 0; i < definition_->resource_views.size(); ++i) {
      const auto &resource = definition_->resource_views[i];
      if (resource.semantic_present &&
          context_.impl_->provider(resource.semantic, nullptr, nullptr))
        destinations_.emplace_back(new Destination{true, i, 0, 0});
    }
}
const StockEffectAdapter::Destination *
StockEffectAdapter::destination(D3DXHANDLE handle) const {
  for (const auto &target : destinations_)
    if (reinterpret_cast<D3DXHANDLE>(target.get()) == handle)
      return target.get();
  return nullptr;
}
D3DXHANDLE StockEffectAdapter::capture(std::size_t buffer,
                                       std::size_t member) const {
  for (const auto &target : destinations_)
    if (!target->resource && target->owner == buffer &&
        target->member == member)
      return reinterpret_cast<D3DXHANDLE>(target.get());
  return nullptr;
}
std::uint32_t StockEffectAdapter::parameter_number(D3DXHANDLE handle) const {
  if (reinterpret_cast<std::uintptr_t>(handle) >= 0x10000)
    return handle ? reflection_.parameter_by_name(handle) : 0;
  return handle_number(handle);
}
std::uint32_t StockEffectAdapter::technique_number(D3DXHANDLE handle) const {
  if (reinterpret_cast<std::uintptr_t>(handle) >= 0x10000)
    return handle ? reflection_.technique_by_name(handle) : 0;
  return handle_number(handle);
}
HRESULT StockEffectAdapter::write_capture(const Destination &target,
                                          const void *data, UINT size) {
  if (target.resource)
    return E_INVALIDARG;
  const auto &uniform =
      definition_->uniform_buffers[target.owner].uniforms[target.member];
  const auto &type = definition_->types[uniform.type_index];
  std::string error;
  auto storage = context_.impl_->uniforms.shared(target.declaration);
  std::vector<unsigned char> projected;
  if (uniform.semantic == "SunLight" && type.name == "SunLight" && size == 44 &&
      data) {
    // Public Moo layout: dir@0/color@12/ambient@28. Original #1513 layout is
    // authored in TTB members; project fields instead of copying the struct.
    projected.resize(type.byte_size);
    for (const auto &member : type.members) {
      std::size_t from = 0, count = 0;
      if (member.name == "m_dir") {
        from = 0;
        count = 12;
      } else if (member.name == "m_color") {
        from = 12;
        count = 16;
      } else if (member.name == "m_ambient") {
        from = 28;
        count = 16;
      } else {
        capture_error_ = "Unsupported SunLight member";
        return E_INVALIDARG;
      }
      if (member.byte_offset > projected.size() ||
          count > projected.size() - member.byte_offset) {
        capture_error_ = "Invalid SunLight member layout";
        return E_INVALIDARG;
      }
      std::memcpy(projected.data() + member.byte_offset,
                  static_cast<const unsigned char *>(data) + from, count);
    }
    data = projected.data();
    size = static_cast<UINT>(projected.size());
  } else if (type.type_class == 5 &&
             size != type.byte_size * uniform.elements) {
    capture_error_ = "Moo producer struct size does not match original ARIE " +
                     uniform.semantic;
    return E_INVALIDARG;
  }
  auto writable = uniform;
  writable.flags &= ~2u;
  if (!storage->write_uniform(writable, type, data, size, error)) {
    capture_error_ = error;
    return E_INVALIDARG;
  }
  return S_OK;
}
HRESULT StockEffectAdapter::write(D3DXHANDLE parameter, const void *data,
                                  UINT size,
                                  std::optional<std::uint32_t> type) {
  if (const auto *target = destination(parameter))
    return write_capture(*target, data, size);
  if (!material_)
    return E_INVALIDARG;
  const auto handle = parameter_number(parameter);
  const auto *reference = reflection_.parameter_reference(handle);
  if (!reference || reference->kind != StockEffectParameterKind::uniform)
    return E_INVALIDARG;
  // D3DX has no UInt setter. Its integer API carries the original uint bits.
  if (type == 1 && reflection_.parameter_type(handle)->basic_type == 2)
    type = 2;
  std::string error;
  return material_->binding().set_uniform(reference->owner, reference->member,
                                          data, size, type, error)
             ? S_OK
             : E_INVALIDARG;
}
HRESULT StockEffectAdapter::read(D3DXHANDLE parameter, void *data, UINT size,
                                 unsigned annotation_type) {
  if (!data && size)
    return E_INVALIDARG;
  const auto handle = handle_number(parameter);
  if (const auto *annotation = reflection_.annotation_reference(handle)) {
    if (annotation->type != annotation_type || size > annotation->value.size())
      return E_INVALIDARG;
    if (size)
      std::memcpy(data, annotation->value.data(), size);
    return S_OK;
  }
  return reflection_.default_value(parameter_number(parameter), data, size)
             ? S_OK
             : E_INVALIDARG;
}
bool StockEffectAdapter::update_resources() {
  capture_error_.clear();
  if (!context_.impl_->provider)
    return true;
  for (const auto &target : destinations_)
    if (target->resource) {
      const auto &resource = definition_->resource_views[target->owner];
      if (!context_.impl_->provider(
              resource.semantic, this,
              reinterpret_cast<D3DXHANDLE>(target.get())) ||
          !capture_error_.empty())
        return false;
    }
  return true;
}
bool StockEffectAdapter::validate_shared(const StockDx9PassSelection &selected,
                                         std::string &error) {
  if (context_.impl_->failures.empty())
    return true;
  for (const auto shader : {selected.vertex_shader, selected.pixel_shader}) {
    if (!shader)
      continue;
    const auto &binary = definition_->shaders[*shader];
    for (const auto &resource : binary.resources)
      if (resource.kind == 3) {
        std::optional<std::uint32_t> buffer;
        if (!resolve_stock_effect_reference(
                *definition_, material_->binding().selection(),
                StockEffectResourceTable::uniform_buffer,
                resource.resource_index, buffer, error) ||
            !buffer)
          return false;
        const auto &declaration = definition_->uniform_buffers[*buffer];
        if (!(declaration.flags & 1))
          continue;
        const auto id = context_.impl_->pool.register_buffer(declaration);
        for (std::size_t i = 0; i < declaration.uniforms.size(); ++i) {
          const auto failed = context_.impl_->failures.find({id, i});
          if (failed == context_.impl_->failures.end())
            continue;
          const auto &member = declaration.uniforms[i];
          const auto size =
              std::uint64_t(definition_->types[member.type_index].byte_size) *
              member.elements;
          for (const auto &copy : binary.register_copies)
            if (copy.uniform_slot == resource.shader_slot &&
                copy.source_offset < std::uint64_t(member.byte_offset) + size &&
                member.byte_offset <
                    std::uint64_t(copy.source_offset) + copy.size) {
              error = failed->second;
              return false;
            }
        }
      }
  }
  return true;
}
HRESULT StockEffectAdapter::apply_current() {
  if (!material_ || !pass_)
    return D3DERR_INVALIDCALL;
  const auto technique =
      reflection_.technique_source_index(technique_number(technique_));
  if (!technique)
    return D3DERR_INVALIDCALL;
  std::lock_guard<std::recursive_mutex> lock(context_.impl_->mutex);
  if (!context_.impl_->providers_alive)
    return D3DERR_INVALIDCALL;
  // A material may first load after the frame's shared update. Publish its
  // actual current producer values on first use, rather than drawing defaults.
  if (std::any_of(context_.impl_->declarations.begin(),
                  context_.impl_->declarations.end(), [](const auto &entry) {
                    return !entry.second.initialised;
                  })) {
    std::string initial_error;
    update_stock_effect_shared_constants(context_, 7, initial_error);
  }
  StockDx9PassSelection selected;
  std::string error;
  if (!material_->binding().select_pass(*technique, *pass_, selected, error) ||
      !validate_shared(selected, error) || !update_resources() ||
      !material_->apply(*technique, *pass_, error)) {
    if (context_.impl_->failure_reporter)
      context_.impl_->failure_reporter(error.empty() ? capture_error_ : error);
    return E_FAIL;
  }
  return S_OK;
}

// Tool-only APIs absent from the original Wgfx interface fail explicitly.
HRESULT STDMETHODCALLTYPE StockEffectAdapter::QueryInterface(REFIID riid,
                                                             void **out) {
  if (!out)
    return E_POINTER;
  *out = nullptr;
  if (IsEqualGUID(riid, IID_IUnknown) || IsEqualGUID(riid, IID_ID3DXEffect) ||
      IsEqualGUID(riid, IID_ID3DXBaseEffect) ||
      IsEqualGUID(riid, stock_effect_iid)) {
    *out = static_cast<ID3DXEffect *>(this);
    AddRef();
    return S_OK;
  }
  return E_NOINTERFACE;
}
ULONG STDMETHODCALLTYPE StockEffectAdapter::AddRef() {
  return static_cast<ULONG>(InterlockedIncrement(&references_));
}
ULONG STDMETHODCALLTYPE StockEffectAdapter::Release() {
  const auto count = InterlockedDecrement(&references_);
  if (!count)
    delete this;
  return static_cast<ULONG>(count);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetDesc(D3DXEFFECT_DESC *desc) {
  if (!desc)
    return E_POINTER;
  desc->Creator = "WgFx compiler.";
  desc->Parameters = static_cast<UINT>(reflection_.parameter_count());
  desc->Techniques = static_cast<UINT>(definition_->techniques.size());
  desc->Functions = static_cast<UINT>(definition_->shaders.size());
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetParameterDesc(
    D3DXHANDLE parameter, D3DXPARAMETER_DESC *desc) {
  if (!desc)
    return E_POINTER;
  std::memset(desc, 0, sizeof(*desc));
  if (const auto *annotation =
          reflection_.annotation_reference(handle_number(parameter))) {
    desc->Name = annotation->name.c_str();
    desc->Rows = desc->Columns = desc->Elements = 1;
    desc->Class = annotation->type == 5 ? D3DXPC_OBJECT : D3DXPC_SCALAR;
    const D3DXPARAMETER_TYPE types[] = {D3DXPT_VOID, D3DXPT_INT,
                                        D3DXPT_INT,  D3DXPT_FLOAT,
                                        D3DXPT_BOOL, D3DXPT_STRING};
    if (annotation->type > 5)
      return E_INVALIDARG;
    desc->Type = types[annotation->type];
    desc->Bytes = annotation->type == 5
                      ? static_cast<UINT>(annotation->value.empty()
                                              ? 0
                                              : annotation->value.size() - 1)
                      : 4;
    return S_OK;
  }
  const auto number = parameter_number(parameter);
  const auto *reference = reflection_.parameter_reference(number);
  const auto *type = reflection_.parameter_type(number);
  if (!reference || !type)
    return E_INVALIDARG;
  desc->Name = reference->name.c_str();
  desc->Semantic =
      reference->semantic_present ? reference->semantic.c_str() : nullptr;
  desc->Class = parameter_class(type->type_class);
  desc->Type = parameter_type(type->basic_type);
  desc->Elements = reflection_.parameter_elements(number);
  desc->Bytes = type->byte_size * desc->Elements;
  if (type->type_class == 5) {
    desc->Rows = desc->Columns = 1;
    desc->StructMembers = type->columns_or_member_count;
  } else {
    desc->Rows = type->columns_or_member_count;
    desc->Columns = type->rows_or_members_offset;
  }
  if (reference->annotations < definition_->annotations.size())
    desc->Annotations = static_cast<UINT>(
        definition_->annotations[reference->annotations].size());
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetTechniqueDesc(
    D3DXHANDLE technique, D3DXTECHNIQUE_DESC *desc) {
  if (!desc)
    return E_POINTER;
  const auto source =
      reflection_.technique_source_index(technique_number(technique));
  if (!source)
    return E_INVALIDARG;
  const auto &value = definition_->techniques[*source];
  desc->Name = value.name.c_str();
  desc->Passes = static_cast<UINT>(value.passes.size());
  desc->Annotations =
      value.annotations < definition_->annotations.size()
          ? static_cast<UINT>(
                definition_->annotations[value.annotations].size())
          : 0;
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetPassDesc(D3DXHANDLE pass,
                                                          D3DXPASS_DESC *desc) {
  (void)pass;
  (void)desc;
  return E_NOTIMPL;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetFunctionDesc(
    D3DXHANDLE shader, D3DXFUNCTION_DESC *desc) {
  (void)shader;
  (void)desc;
  return E_NOTIMPL;
}
D3DXHANDLE STDMETHODCALLTYPE
StockEffectAdapter::GetParameter(D3DXHANDLE parameter, UINT index) {
  return parameter ? nullptr : effect_handle(reflection_.parameter(index));
}
D3DXHANDLE STDMETHODCALLTYPE
StockEffectAdapter::GetParameterByName(D3DXHANDLE parameter, const char *name) {
  return parameter || !name
             ? nullptr
             : effect_handle(reflection_.parameter_by_name(name));
}
D3DXHANDLE STDMETHODCALLTYPE StockEffectAdapter::GetParameterBySemantic(
    D3DXHANDLE parameter, const char *semantic) {
  return parameter || !semantic
             ? nullptr
             : effect_handle(reflection_.parameter_by_semantic(semantic));
}
D3DXHANDLE STDMETHODCALLTYPE
StockEffectAdapter::GetParameterElement(D3DXHANDLE parameter, UINT index) {
  (void)parameter;
  (void)index;
  return nullptr;
}
D3DXHANDLE STDMETHODCALLTYPE StockEffectAdapter::GetTechnique(UINT index) {
  return effect_handle(reflection_.technique(index));
}
D3DXHANDLE STDMETHODCALLTYPE
StockEffectAdapter::GetTechniqueByName(const char *name) {
  return name ? effect_handle(reflection_.technique_by_name(name)) : nullptr;
}
D3DXHANDLE STDMETHODCALLTYPE StockEffectAdapter::GetPass(D3DXHANDLE technique,
                                                         UINT index) {
  (void)technique;
  (void)index;
  return nullptr;
}
D3DXHANDLE STDMETHODCALLTYPE
StockEffectAdapter::GetPassByName(D3DXHANDLE technique, const char *name) {
  (void)technique;
  (void)name;
  return nullptr;
}
D3DXHANDLE STDMETHODCALLTYPE StockEffectAdapter::GetFunction(UINT index) {
  (void)index;
  return nullptr;
}
D3DXHANDLE STDMETHODCALLTYPE
StockEffectAdapter::GetFunctionByName(const char *name) {
  (void)name;
  return nullptr;
}
D3DXHANDLE STDMETHODCALLTYPE
StockEffectAdapter::GetAnnotation(D3DXHANDLE object, UINT index) {
  return effect_handle(reflection_.annotation(handle_number(object), index));
}
D3DXHANDLE STDMETHODCALLTYPE
StockEffectAdapter::GetAnnotationByName(D3DXHANDLE object, const char *name) {
  return name ? effect_handle(
                    reflection_.annotation_by_name(handle_number(object), name))
              : nullptr;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetValue(D3DXHANDLE parameter,
                                                       const void *data,
                                                       UINT bytes) {
  return write(parameter, data, bytes, {});
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetValue(D3DXHANDLE parameter,
                                                       void *data, UINT bytes) {
  return read(parameter, data, bytes);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetBool(D3DXHANDLE parameter,
                                                      BOOL b) {
  return write(parameter, &b, sizeof(BOOL), 4);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetBool(D3DXHANDLE parameter,
                                                      BOOL *b) {
  return read(parameter, b, sizeof(BOOL), 4);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetBoolArray(D3DXHANDLE parameter,
                                                           const BOOL *b,
                                                           UINT count) {
  if (count > UINT_MAX / sizeof(BOOL))
    return E_INVALIDARG;
  return write(parameter, b, count * sizeof(BOOL), 4);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetBoolArray(D3DXHANDLE parameter,
                                                           BOOL *b,
                                                           UINT count) {
  if (count > UINT_MAX / sizeof(BOOL))
    return E_INVALIDARG;
  return read(parameter, b, count * sizeof(BOOL), 4);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetInt(D3DXHANDLE parameter,
                                                     INT n) {
  return write(parameter, &n, sizeof(INT), 1);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetInt(D3DXHANDLE parameter,
                                                     INT *n) {
  return read(parameter, n, sizeof(INT), 1);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetIntArray(D3DXHANDLE parameter,
                                                          const INT *n,
                                                          UINT count) {
  if (count > UINT_MAX / sizeof(INT))
    return E_INVALIDARG;
  return write(parameter, n, count * sizeof(INT), 1);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetIntArray(D3DXHANDLE parameter,
                                                          INT *n, UINT count) {
  if (count > UINT_MAX / sizeof(INT))
    return E_INVALIDARG;
  return read(parameter, n, count * sizeof(INT), 1);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetFloat(D3DXHANDLE parameter,
                                                       FLOAT f) {
  return write(parameter, &f, sizeof(FLOAT), 3);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetFloat(D3DXHANDLE parameter,
                                                       FLOAT *f) {
  return read(parameter, f, sizeof(FLOAT), 3);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetFloatArray(
    D3DXHANDLE parameter, const FLOAT *f, UINT count) {
  if (count > UINT_MAX / sizeof(FLOAT))
    return E_INVALIDARG;
  return write(parameter, f, count * sizeof(FLOAT), 3);
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::GetFloatArray(D3DXHANDLE parameter, FLOAT *f, UINT count) {
  if (count > UINT_MAX / sizeof(FLOAT))
    return E_INVALIDARG;
  return read(parameter, f, count * sizeof(FLOAT), 3);
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::SetVector(D3DXHANDLE parameter, const D3DXVECTOR4 *vector) {
  return write(parameter, vector, sizeof(D3DXVECTOR4), 3);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetVector(D3DXHANDLE parameter,
                                                        D3DXVECTOR4 *vector) {
  return read(parameter, vector, sizeof(D3DXVECTOR4));
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetVectorArray(
    D3DXHANDLE parameter, const D3DXVECTOR4 *vector, UINT count) {
  if (count > UINT_MAX / sizeof(D3DXVECTOR4))
    return E_INVALIDARG;
  return write(parameter, vector, count * sizeof(D3DXVECTOR4), 3);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetVectorArray(
    D3DXHANDLE parameter, D3DXVECTOR4 *vector, UINT count) {
  if (count > UINT_MAX / sizeof(D3DXVECTOR4))
    return E_INVALIDARG;
  return read(parameter, vector, count * sizeof(D3DXVECTOR4));
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::SetMatrix(D3DXHANDLE parameter, const D3DXMATRIX *matrix) {
  return write(parameter, matrix, sizeof(D3DXMATRIX), 3);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetMatrix(D3DXHANDLE parameter,
                                                        D3DXMATRIX *matrix) {
  return read(parameter, matrix, sizeof(D3DXMATRIX));
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetMatrixArray(
    D3DXHANDLE parameter, const D3DXMATRIX *matrix, UINT count) {
  if (count > UINT_MAX / sizeof(D3DXMATRIX))
    return E_INVALIDARG;
  return write(parameter, matrix, count * sizeof(D3DXMATRIX), 3);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetMatrixArray(
    D3DXHANDLE parameter, D3DXMATRIX *matrix, UINT count) {
  if (count > UINT_MAX / sizeof(D3DXMATRIX))
    return E_INVALIDARG;
  return read(parameter, matrix, count * sizeof(D3DXMATRIX));
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetMatrixPointerArray(
    D3DXHANDLE parameter, const D3DXMATRIX **matrix, UINT count) {
  if ((!matrix && count) || count > UINT_MAX / sizeof(D3DXMATRIX))
    return E_INVALIDARG;
  std::vector<D3DXMATRIX> values(count);
  for (UINT i = 0; i < count; ++i) {
    if (!matrix[i])
      return E_INVALIDARG;
    values[i] = *matrix[i];
  }
  return SetMatrixArray(parameter, values.data(), count);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetMatrixPointerArray(
    D3DXHANDLE parameter, D3DXMATRIX **matrix, UINT count) {
  (void)parameter;
  (void)matrix;
  (void)count;
  return E_NOTIMPL;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetMatrixTranspose(
    D3DXHANDLE parameter, const D3DXMATRIX *matrix) {
  if ((!matrix && 1) || 1 > UINT_MAX / sizeof(D3DXMATRIX))
    return E_INVALIDARG;
  std::vector<D3DXMATRIX> values(1);
  for (UINT i = 0; i < 1; ++i)
    for (unsigned r = 0; r < 4; ++r)
      for (unsigned c = 0; c < 4; ++c)
        values[i].m[r][c] = matrix[i].m[c][r];
  return SetMatrixArray(parameter, values.data(), 1);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetMatrixTranspose(
    D3DXHANDLE parameter, D3DXMATRIX *matrix) {
  const auto result = GetMatrixArray(parameter, matrix, 1);
  if (FAILED(result))
    return result;
  for (UINT i = 0; i < 1; ++i)
    for (unsigned r = 0; r < 4; ++r)
      for (unsigned c = r + 1; c < 4; ++c)
        std::swap(matrix[i].m[r][c], matrix[i].m[c][r]);
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetMatrixTransposeArray(
    D3DXHANDLE parameter, const D3DXMATRIX *matrix, UINT count) {
  if ((!matrix && count) || count > UINT_MAX / sizeof(D3DXMATRIX))
    return E_INVALIDARG;
  std::vector<D3DXMATRIX> values(count);
  for (UINT i = 0; i < count; ++i)
    for (unsigned r = 0; r < 4; ++r)
      for (unsigned c = 0; c < 4; ++c)
        values[i].m[r][c] = matrix[i].m[c][r];
  return SetMatrixArray(parameter, values.data(), count);
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetMatrixTransposeArray(
    D3DXHANDLE parameter, D3DXMATRIX *matrix, UINT count) {
  const auto result = GetMatrixArray(parameter, matrix, count);
  if (FAILED(result))
    return result;
  for (UINT i = 0; i < count; ++i)
    for (unsigned r = 0; r < 4; ++r)
      for (unsigned c = r + 1; c < 4; ++c)
        std::swap(matrix[i].m[r][c], matrix[i].m[c][r]);
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetMatrixTransposePointerArray(
    D3DXHANDLE parameter, const D3DXMATRIX **matrix, UINT count) {
  (void)parameter;
  (void)matrix;
  (void)count;
  return E_NOTIMPL;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetMatrixTransposePointerArray(
    D3DXHANDLE parameter, D3DXMATRIX **matrix, UINT count) {
  (void)parameter;
  (void)matrix;
  (void)count;
  return E_NOTIMPL;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetString(D3DXHANDLE parameter,
                                                        const char *string) {
  (void)parameter;
  (void)string;
  return E_NOTIMPL;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetString(D3DXHANDLE parameter,
                                                        const char **string) {
  if (!string)
    return E_POINTER;
  const auto *annotation =
      reflection_.annotation_reference(handle_number(parameter));
  if (!annotation || annotation->type != 5 || annotation->value.empty())
    return E_INVALIDARG;
  *string = reinterpret_cast<const char *>(annotation->value.data());
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetTexture(
    D3DXHANDLE parameter, struct IDirect3DBaseTexture9 *texture) {
  if (!material_)
    return E_INVALIDARG;
  std::size_t index;
  if (const auto *target = destination(parameter)) {
    if (!target->resource)
      return E_INVALIDARG;
    index = target->owner;
  } else {
    const auto *reference =
        reflection_.parameter_reference(parameter_number(parameter));
    if (!reference ||
        reference->kind != StockEffectParameterKind::resource_view)
      return E_INVALIDARG;
    index = reference->owner;
  }
  std::string error;
  const auto result = material_->set_texture(index, texture, error,
      destination(parameter) != nullptr);
  if (!result && destination(parameter))
    capture_error_ = error;
  return result ? S_OK : E_INVALIDARG;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetTexture(
    D3DXHANDLE parameter, struct IDirect3DBaseTexture9 **texture) {
  if (!texture)
    return E_POINTER;
  *texture = nullptr;
  const auto *reference =
      reflection_.parameter_reference(parameter_number(parameter));
  if (!material_ || !reference ||
      reference->kind != StockEffectParameterKind::resource_view)
    return E_INVALIDARG;
  *texture = material_->texture(reference->owner);
  if (*texture)
    (*texture)->AddRef();
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetPixelShader(
    D3DXHANDLE parameter, struct IDirect3DPixelShader9 **shader) {
  (void)parameter;
  (void)shader;
  return E_NOTIMPL;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetVertexShader(
    D3DXHANDLE parameter, struct IDirect3DVertexShader9 **shader) {
  (void)parameter;
  (void)shader;
  return E_NOTIMPL;
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::SetArrayRange(D3DXHANDLE parameter, UINT start, UINT end) {
  (void)parameter;
  (void)start;
  (void)end;
  return E_NOTIMPL;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::GetPool(ID3DXEffectPool **pool) {
  if (!pool)
    return E_POINTER;
  *pool = nullptr;
  return S_OK; // ARIE has its real UniformContext, not a D3DX pool.
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::SetTechnique(D3DXHANDLE technique) {
  if (begun_)
    return D3DERR_INVALIDCALL;
  const auto number = technique_number(technique);
  if (!reflection_.technique_source_index(number))
    return E_INVALIDARG;
  technique_ = effect_handle(number);
  return S_OK;
}
D3DXHANDLE STDMETHODCALLTYPE StockEffectAdapter::GetCurrentTechnique() {
  return technique_;
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::ValidateTechnique(D3DXHANDLE technique) {
  const auto source =
      reflection_.technique_source_index(technique_number(technique));
  if (!source || !material_)
    return E_INVALIDARG;
  for (std::size_t i = 0; i < definition_->techniques[*source].passes.size();
       ++i) {
    StockDx9PassSelection selected;
    std::string error;
    if (!material_->binding().select_pass(*source, i, selected, error))
      return E_FAIL;
  }
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::FindNextValidTechnique(
    D3DXHANDLE technique, D3DXHANDLE *next_technique) {
  if (!next_technique)
    return E_POINTER;
  *next_technique = nullptr;
  auto index = technique ? ((technique_number(technique) & 0xfff)) : 0;
  for (; index < definition_->techniques.size(); ++index) {
    const auto candidate = GetTechnique(index);
    if (SUCCEEDED(ValidateTechnique(candidate))) {
      *next_technique = candidate;
      return S_OK;
    }
  }
  return S_FALSE;
}
BOOL STDMETHODCALLTYPE StockEffectAdapter::IsParameterUsed(
    D3DXHANDLE parameter, D3DXHANDLE technique) {
  (void)technique;
  return reflection_.parameter_used(parameter_number(parameter)) ? TRUE : FALSE;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::Begin(UINT *passes, DWORD flags) {
  if (!passes || !material_ || begun_)
    return D3DERR_INVALIDCALL;
  if (!(flags & D3DXFX_DONOTSAVESTATE))
    return E_NOTIMPL;
  const auto source =
      reflection_.technique_source_index(technique_number(technique_));
  if (!source)
    return E_INVALIDARG;
  *passes = static_cast<UINT>(definition_->techniques[*source].passes.size());
  begun_ = true;
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::BeginPass(UINT pass) {
  if (!begun_ || pass_)
    return D3DERR_INVALIDCALL;
  const auto source =
      reflection_.technique_source_index(technique_number(technique_));
  if (!source || pass >= definition_->techniques[*source].passes.size())
    return E_INVALIDARG;
  pass_ = pass;
  const auto result = apply_current();
  // Keep the selected pass so a later CommitChanges can apply object values.
  return result;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::CommitChanges() {
  return begun_ && pass_ ? apply_current() : D3DERR_INVALIDCALL;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::EndPass() {
  if (!begun_ || !pass_)
    return D3DERR_INVALIDCALL;
  pass_.reset();
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::End() {
  if (!begun_ || pass_)
    return D3DERR_INVALIDCALL;
  begun_ = false;
  return S_OK;
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::GetDevice(struct IDirect3DDevice9 **device) {
  if (!device)
    return E_POINTER;
  *device = runtime_ ? runtime_->device() : nullptr;
  if (!*device)
    return E_FAIL;
  (*device)->AddRef();
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::OnLostDevice() {
  // D3D9 shaders survive Reset, but all inactive Properties-owned Bindings
  // must relinquish their automatic default-pool textures before it starts.
  pass_.reset();
  begun_ = false;
  std::lock_guard<std::recursive_mutex> lock(context_.impl_->mutex);
  bool success = true;
  auto& runtimes = context_.impl_->runtimes;
  for (auto it = runtimes.begin(); it != runtimes.end();) {
    if (auto runtime = it->lock()) {
      std::string error;
      if (!runtime->on_lost_device(error)) success = false;
      ++it;
    } else {
      it = runtimes.erase(it);
    }
  }
  return success ? S_OK : E_FAIL;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::OnResetDevice() {
  return runtime_ ? S_OK : D3DERR_INVALIDCALL;
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::SetStateManager(ID3DXEffectStateManager *manager) {
  return !manager || (runtime_ && manager == runtime_->state_manager())
             ? S_OK
             : E_INVALIDARG;
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::GetStateManager(ID3DXEffectStateManager **manager) {
  if (!manager)
    return E_POINTER;
  *manager = runtime_ ? runtime_->state_manager() : nullptr;
  if (*manager)
    (*manager)->AddRef();
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::BeginParameterBlock() {
  return E_NOTIMPL;
}
D3DXHANDLE STDMETHODCALLTYPE StockEffectAdapter::EndParameterBlock() {
  return nullptr;
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::ApplyParameterBlock(D3DXHANDLE parameter_block) {
  (void)parameter_block;
  return E_NOTIMPL;
}
HRESULT STDMETHODCALLTYPE
StockEffectAdapter::DeleteParameterBlock(D3DXHANDLE parameter_block) {
  (void)parameter_block;
  return E_NOTIMPL;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::CloneEffect(
    struct IDirect3DDevice9 *device, struct ID3DXEffect **effect) {
  if (!effect)
    return E_POINTER;
  *effect = nullptr;
  if (!runtime_ || !material_ || device != runtime_->device())
    return E_INVALIDARG;
  std::lock_guard<std::recursive_mutex> lock(context_.impl_->mutex);
  *effect = new StockEffectAdapter(*this);
  return S_OK;
}
HRESULT STDMETHODCALLTYPE StockEffectAdapter::SetRawValue(D3DXHANDLE parameter,
                                                          const void *data,
                                                          UINT byte_offset,
                                                          UINT bytes) {
  (void)parameter;
  (void)data;
  (void)byte_offset;
  (void)bytes;
  return E_NOTIMPL;
}

StockEffectSharedContext::StockEffectSharedContext(
    StockEffectConstantProvider provider,
    std::function<void(const std::string &)> failure_reporter)
    : impl_(new Impl(std::move(provider), std::move(failure_reporter))) {}
StockEffectSharedContext::~StockEffectSharedContext() = default;
void StockEffectSharedContext::detach_providers() {
  std::lock_guard<std::recursive_mutex> lock(impl_->mutex);
  impl_->providers_alive = false;
  impl_->provider = {};
  impl_->failure_reporter = {};
}
HRESULT
create_stock_effect(IDirect3DDevice9 *device,
                    ID3DXEffectStateManager *state_manager, const void *member,
                    std::size_t size,
                    const std::shared_ptr<StockEffectSharedContext> &context,
                    ID3DXEffect **output, std::string &error) {
  if (!output || !context)
    return E_INVALIDARG;
  *output = nullptr;
  auto definition = std::make_shared<StockEffectBinary>();
  if (!decode_stock_dx9_effect(member, size, *definition, error))
    return E_FAIL;
  std::shared_ptr<StockDx9EffectRuntime> runtime;
  if (!StockDx9EffectRuntime::create(device, state_manager, definition, runtime,
                                     error))
    return E_FAIL;
  std::lock_guard<std::recursive_mutex> lock(context->impl_->mutex);
  if (!context->impl_->providers_alive) {
    error = "Effect visual context has been destroyed";
    return E_FAIL;
  }
  *output = new StockEffectAdapter(std::move(runtime), context);
  return S_OK;
}
HRESULT create_stock_effect_material(ID3DXEffect *definition,
                                     ID3DXEffect **output) {
  if (!output)
    return E_POINTER;
  *output = nullptr;
  void *queried = nullptr;
  if (!definition ||
      FAILED(definition->QueryInterface(stock_effect_iid, &queried)))
    return E_INVALIDARG;
  auto *source =
      static_cast<StockEffectAdapter *>(static_cast<ID3DXEffect *>(queried));
  HRESULT result = E_INVALIDARG;
  if (source->runtime_ && source->context_owner_) {
    std::lock_guard<std::recursive_mutex> lock(source->context_.impl_->mutex);
    *output = new StockEffectAdapter(source->runtime_, source->context_owner_);
    result = S_OK;
  }
  source->Release();
  return result;
}
bool is_stock_effect(ID3DXEffect *effect) {
  if (!effect)
    return false;
  void *value = nullptr;
  if (FAILED(effect->QueryInterface(stock_effect_iid, &value)))
    return false;
  static_cast<ID3DXEffect *>(value)->Release();
  return true;
}
bool update_stock_effect_shared_constants(StockEffectSharedContext &context,
                                          unsigned mask, std::string &error) {
  std::lock_guard<std::recursive_mutex> lock(context.impl_->mutex);
  bool success = true;
  if (!context.impl_->providers_alive) {
    error = "Effect visual context has been destroyed";
    return false;
  }
  if (!context.impl_->provider) {
    error.clear();
    return true;
  }
  for (auto &entry : context.impl_->declarations) {
    const auto &shared = entry.second;
    const auto &buffer = shared.definition->uniform_buffers[shared.buffer];
    const auto flag = buffer.name == "PerFrame"    ? 1u
                      : buffer.name == "PerScreen" ? 2u
                      : buffer.name == "PerView"   ? 4u
                                                   : 0u;
    if (!(mask & flag))
      continue;
    entry.second.initialised = true;
    StockEffectAdapter writer(shared.definition, context);
    for (std::size_t i = 0; i < buffer.uniforms.size(); ++i) {
      const auto &member = buffer.uniforms[i];
      if (!member.semantic_present ||
          !context.impl_->provider(member.semantic, nullptr, nullptr))
        continue;
      writer.capture_error_.clear();
      const auto result = context.impl_->provider(
          member.semantic, &writer, writer.capture(shared.buffer, i));
      if (!result || !writer.capture_error_.empty()) {
        error = writer.capture_error_.empty()
                    ? "Automatic constant provider failed: " + member.semantic
                    : writer.capture_error_;
        context.impl_->failures[{entry.first, i}] = error;
        success = false;
      } else
        context.impl_->failures.erase({entry.first, i});
    }
  }
  if (success)
    error.clear();
  return success;
}
} // namespace wot
