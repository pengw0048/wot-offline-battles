#pragma once

#include <d3d9.h>
#include <d3dx9effect.h>

#include <functional>
#include <memory>
#include <string>

namespace wot {

// A null effect is a registry lookup only. Otherwise invoke the existing Moo
// EffectConstantValue with the supplied real uniform/resource destination.
using StockEffectConstantProvider =
    std::function<bool(const std::string &, ID3DXEffect *, D3DXHANDLE)>;

class StockEffectAdapter;

// EffectVisualContext owns one context. Material adapters retain it while their
// bindings are alive, and every material keeps its own private value storage.
class StockEffectSharedContext {
public:
  explicit StockEffectSharedContext(
      StockEffectConstantProvider provider,
      std::function<void(const std::string &)> failure_reporter = {});
  ~StockEffectSharedContext();
  void detach_providers();
  StockEffectSharedContext(const StockEffectSharedContext &) = delete;
  StockEffectSharedContext &
  operator=(const StockEffectSharedContext &) = delete;

private:
  friend class StockEffectAdapter;
  friend HRESULT create_stock_effect_material(ID3DXEffect *, ID3DXEffect **);
  friend bool update_stock_effect_shared_constants(StockEffectSharedContext &,
                                                   unsigned, std::string &);
  friend HRESULT
  create_stock_effect(IDirect3DDevice9 *, ID3DXEffectStateManager *,
                      const void *, std::size_t,
                      const std::shared_ptr<StockEffectSharedContext> &,
                      ID3DXEffect **, std::string &);
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

HRESULT
create_stock_effect(IDirect3DDevice9 *device,
                    ID3DXEffectStateManager *state_manager, const void *member,
                    std::size_t size,
                    const std::shared_ptr<StockEffectSharedContext> &context,
                    ID3DXEffect **output, std::string &error);
// Create a material from immutable defaults. CloneEffect separately preserves
// the source material's current private values for an explicit deep copy.
HRESULT create_stock_effect_material(ID3DXEffect *definition,
                                     ID3DXEffect **output);
bool is_stock_effect(ID3DXEffect *effect);
// Masks match Moo CONSTANTS_PER_FRAME/PER_SCREEN/PER_VIEW (1/2/4).
bool update_stock_effect_shared_constants(StockEffectSharedContext &context,
                                          unsigned mask, std::string &error);

} // namespace wot
