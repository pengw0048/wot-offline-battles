#include "wot/stock_effect_reflection.hpp"

#include <algorithm>
#include <cstring>
#include <numeric>
#include <stdexcept>

namespace wot {
namespace {
std::optional<std::uint32_t> index_of(std::uint32_t handle, std::uint32_t kind, std::size_t count) {
    if (handle >= 0x10000 || (handle & 0x7000) != kind) return {};
    const auto index = (handle & 0xffff8fff)-1;
    return index < count ? std::optional<std::uint32_t>(index) : std::nullopt;
}
std::uint32_t handle_for(std::size_t index, std::size_t count, std::uint32_t kind) {
    return index < count ? static_cast<std::uint32_t>(index+1) | kind : 0;
}
} // namespace

StockEffectReflection::StockEffectReflection(std::shared_ptr<const StockEffectBinary> definition,
    const std::vector<std::string>& automatic_resource_semantics)
    : definition_(std::move(definition)) {
    if (!definition_) throw std::invalid_argument("Reflection requires an effect definition");
    for (std::size_t i = 0; i < definition_->uniform_buffers.size(); ++i) {
        const auto& buffer = definition_->uniform_buffers[i];
        const auto owner = static_cast<std::uint32_t>(i);
        if (buffer.name != "$Global" && buffer.name != "$Selection")
            parameters_.push_back({StockEffectParameterKind::uniform_buffer, buffer.name, {}, false,
                owner, 0, buffer.flags, buffer.annotations});
        if (buffer.flags & 1) continue;
        for (std::size_t j = 0; j < buffer.uniforms.size(); ++j) {
            const auto& uniform = buffer.uniforms[j];
            parameters_.push_back({StockEffectParameterKind::uniform, uniform.name, uniform.semantic,
                uniform.semantic_present, owner, static_cast<std::uint32_t>(j), uniform.flags,
                uniform.annotations});
        }
    }
    for (unsigned kind = 0; kind < 2; ++kind) {
        const auto& resources = kind == 0 ? definition_->resource_views : definition_->unordered_views;
        for (std::size_t i = 0; i < resources.size(); ++i) {
            const auto& resource = resources[i];
            if (resource.semantic_present && std::find(automatic_resource_semantics.begin(),
                    automatic_resource_semantics.end(), resource.semantic) != automatic_resource_semantics.end())
                continue;
            parameters_.push_back({kind == 0 ? StockEffectParameterKind::resource_view :
                StockEffectParameterKind::unordered_view, resource.name, resource.semantic,
                resource.semantic_present, static_cast<std::uint32_t>(i), 0, resource.flags,
                resource.annotations});
        }
    }
    std::sort(parameters_.begin(), parameters_.end(),
        [](const auto& a, const auto& b) { return a.name < b.name; });
    techniques_.resize(definition_->techniques.size());
    std::iota(techniques_.begin(), techniques_.end(), 0);
    std::sort(techniques_.begin(), techniques_.end(), [&](auto a, auto b) {
        return definition_->techniques[a].name < definition_->techniques[b].name;
    });
    for (std::size_t group = 0; group < definition_->annotations.size(); ++group) {
        annotation_starts_.push_back(static_cast<std::uint32_t>(annotations_.size()));
        for (std::size_t i = 0; i < definition_->annotations[group].size(); ++i)
            annotations_.emplace_back(group, i);
    }
    if (parameters_.size() >= 0x1000 || techniques_.size() >= 0x1000 || annotations_.size() >= 0x1000)
        throw std::runtime_error("Effect exceeds the native encoded handle namespace");
}

std::uint32_t StockEffectReflection::parameter(std::size_t index) const {
    return handle_for(index, parameters_.size(), 0x1000);
}
std::uint32_t StockEffectReflection::parameter_by_name(const std::string& name) const {
    const auto found = std::lower_bound(parameters_.begin(), parameters_.end(), name,
        [](const auto& parameter, const auto& key) { return parameter.name < key; });
    return found != parameters_.end() && found->name == name ? parameter(found-parameters_.begin()) : 0;
}
std::uint32_t StockEffectReflection::parameter_by_semantic(const std::string& semantic) const {
    const auto found = std::find_if(parameters_.begin(), parameters_.end(), [&](const auto& parameter) {
        return parameter.semantic_present && parameter.semantic == semantic;
    });
    return found != parameters_.end() ? parameter(found-parameters_.begin()) : 0;
}
const StockEffectParameterReference* StockEffectReflection::parameter_reference(std::uint32_t handle) const {
    const auto index = index_of(handle, 0x1000, parameters_.size());
    return index ? &parameters_[*index] : nullptr;
}
std::uint32_t StockEffectReflection::technique(std::size_t index) const {
    return handle_for(index, techniques_.size(), 0x4000);
}
std::uint32_t StockEffectReflection::technique_by_name(const std::string& name) const {
    const auto found = std::lower_bound(techniques_.begin(), techniques_.end(), name,
        [&](auto index, const auto& key) { return definition_->techniques[index].name < key; });
    return found != techniques_.end() && definition_->techniques[*found].name == name ?
        technique(found-techniques_.begin()) : 0;
}
std::optional<std::size_t> StockEffectReflection::technique_source_index(std::uint32_t handle) const {
    const auto index = index_of(handle, 0x4000, techniques_.size());
    return index ? std::optional<std::size_t>(techniques_[*index]) : std::nullopt;
}
std::optional<std::uint32_t> StockEffectReflection::annotation_group(std::uint32_t object) const {
    std::uint32_t group = 0xffffffff;
    if (const auto* parameter = parameter_reference(object)) group = parameter->annotations;
    else if (const auto index = technique_source_index(object)) group = definition_->techniques[*index].annotations;
    return group < definition_->annotations.size() ? std::optional<std::uint32_t>(group) : std::nullopt;
}
std::uint32_t StockEffectReflection::annotation(std::uint32_t object, std::size_t index) const {
    const auto group = annotation_group(object);
    return group && index < definition_->annotations[*group].size() ?
        handle_for(annotation_starts_[*group]+index, annotations_.size(), 0x2000) : 0;
}
std::uint32_t StockEffectReflection::annotation_by_name(std::uint32_t object, const std::string& name) const {
    const auto group = annotation_group(object);
    if (!group) return 0;
    const auto& annotations = definition_->annotations[*group];
    for (std::size_t i = 0; i < annotations.size(); ++i)
        if (annotations[i].name == name) return annotation(object, i);
    return 0;
}
const StockEffectAnnotation* StockEffectReflection::annotation_reference(std::uint32_t handle) const {
    const auto index = index_of(handle, 0x2000, annotations_.size());
    if (!index) return nullptr;
    const auto& pair = annotations_[*index];
    return &definition_->annotations[pair.first][pair.second];
}
const StockEffectType* StockEffectReflection::parameter_type(std::uint32_t handle) const {
    const auto* parameter = parameter_reference(handle);
    if (!parameter) return nullptr;
    if (parameter->kind == StockEffectParameterKind::uniform_buffer) {
        static const StockEffectType buffer_type{};
        return &buffer_type; // Original pseudo UBB type descriptor is all zero.
    }
    std::uint32_t type;
    if (parameter->kind == StockEffectParameterKind::uniform)
        type = definition_->uniform_buffers[parameter->owner].uniforms[parameter->member].type_index;
    else if (parameter->kind == StockEffectParameterKind::resource_view)
        type = definition_->resource_views[parameter->owner].type_index;
    else type = definition_->unordered_views[parameter->owner].type_index;
    return type < definition_->types.size() ? &definition_->types[type] : nullptr;
}
std::uint32_t StockEffectReflection::parameter_elements(std::uint32_t handle) const {
    const auto* parameter = parameter_reference(handle);
    if (!parameter) return 0;
    if (parameter->kind == StockEffectParameterKind::uniform_buffer) return 1;
    if (parameter->kind == StockEffectParameterKind::uniform)
        return definition_->uniform_buffers[parameter->owner].uniforms[parameter->member].elements;
    return parameter->kind == StockEffectParameterKind::resource_view ?
        definition_->resource_views[parameter->owner].elements : definition_->unordered_views[parameter->owner].elements;
}
bool StockEffectReflection::default_value(std::uint32_t handle, void* bytes, std::size_t size) const {
    const auto* parameter = parameter_reference(handle);
    if (!parameter || (!bytes && size) || (parameter->kind != StockEffectParameterKind::uniform &&
            parameter->kind != StockEffectParameterKind::uniform_buffer)) return false;
    const auto& buffer = definition_->uniform_buffers[parameter->owner];
    const auto offset = parameter->kind == StockEffectParameterKind::uniform ?
        buffer.uniforms[parameter->member].byte_offset : 0;
    if (offset > buffer.defaults.size() || size > buffer.defaults.size()-offset) return false;
    if (size) std::memcpy(bytes, buffer.defaults.data()+offset, size);
    return true;
}
bool StockEffectReflection::parameter_used(std::uint32_t handle) const {
    const auto* reference = parameter_reference(handle);
    return reference && !(reference->flags & 2);
}

} // namespace wot
