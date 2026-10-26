#pragma once

#include "wot/stock_shader_binary.hpp"

#include <memory>
#include <optional>

namespace wot {

enum class StockEffectParameterKind { uniform, uniform_buffer, resource_view, unordered_view };

struct StockEffectParameterReference {
    StockEffectParameterKind kind = StockEffectParameterKind::uniform;
    std::string name;
    std::string semantic;
    bool semantic_present = false;
    std::uint32_t owner = 0;
    std::uint32_t member = 0;
    std::uint32_t flags = 0;
    std::uint32_t annotations = 0xffffffff;
};

// Immutable #1513 Effect handle namespace and default-value reflection.
// A mutable material must retain its own separate StockEffectBinding.
class StockEffectReflection {
public:
    // b39970 hides resources whose semantic is registered with the real effect
    // context; those resources are supplied by its automatic binding callbacks.
    StockEffectReflection(std::shared_ptr<const StockEffectBinary> definition,
                          const std::vector<std::string>& automatic_resource_semantics);
    const StockEffectBinary& definition() const { return *definition_; }
    std::size_t parameter_count() const { return parameters_.size(); }
    std::uint32_t parameter(std::size_t index) const;
    std::uint32_t parameter_by_name(const std::string& name) const;
    std::uint32_t parameter_by_semantic(const std::string& semantic) const;
    const StockEffectParameterReference* parameter_reference(std::uint32_t handle) const;
    std::uint32_t technique(std::size_t index) const;
    std::uint32_t technique_by_name(const std::string& name) const;
    std::optional<std::size_t> technique_source_index(std::uint32_t handle) const;
    std::uint32_t annotation(std::uint32_t object, std::size_t index) const;
    std::uint32_t annotation_by_name(std::uint32_t object, const std::string& name) const;
    const StockEffectAnnotation* annotation_reference(std::uint32_t handle) const;
    const StockEffectType* parameter_type(std::uint32_t handle) const;
    std::uint32_t parameter_elements(std::uint32_t handle) const;
    // Native numeric/vector/matrix getters copy from immutable defaults, not
    // from mutable Binding storage, and use their caller-selected byte width.
    bool default_value(std::uint32_t handle, void* bytes, std::size_t size) const;
    bool parameter_used(std::uint32_t handle) const;
private:
    std::optional<std::uint32_t> annotation_group(std::uint32_t object) const;
    std::shared_ptr<const StockEffectBinary> definition_;
    std::vector<StockEffectParameterReference> parameters_;
    std::vector<std::size_t> techniques_;
    std::vector<std::pair<std::size_t, std::size_t>> annotations_;
    std::vector<std::uint32_t> annotation_starts_;
};

} // namespace wot
