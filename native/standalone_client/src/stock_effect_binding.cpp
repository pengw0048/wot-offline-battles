#include "wot/stock_effect_binding.hpp"

#include <algorithm>
#include <limits>
#include <cstring>
#include <stdexcept>

namespace wot {
namespace {
bool contains(const std::vector<unsigned char>& bytes, std::uint64_t at, std::uint64_t size) {
    return at <= bytes.size() && size <= bytes.size()-at;
}
std::uint32_t word(const std::vector<unsigned char>& bytes, std::uint64_t at) {
    const auto offset = static_cast<std::size_t>(at);
    return std::uint32_t(bytes[offset]) | (std::uint32_t(bytes[offset+1]) << 8) |
        (std::uint32_t(bytes[offset+2]) << 16) | (std::uint32_t(bytes[offset+3]) << 24);
}
bool invalid(std::string& error, const char* message) { error = message; return false; }
} // namespace

bool evaluate_stock_effect_selector(const StockEffectBinary& definition,
    const std::vector<unsigned char>& selection, std::uint32_t code_offset,
    std::uint32_t& value, std::string& error) {
    const auto& code = definition.selector_code;
    if (!contains(code, code_offset, 4)) return invalid(error, "Invalid ARIE selector offset");
    const auto size = word(code, code_offset);
    const auto begin = std::uint64_t(code_offset)+4;
    if (size < 8 || !contains(code, begin, size))
        return invalid(error, "Truncated ARIE selector coefficient table");
    std::vector<std::uint32_t> slots(definition.selection_domains.size(), 0);
    for (const auto& domain : definition.selection_domains) {
        if (!domain.value_size || domain.slot >= slots.size() ||
            !contains(selection, domain.source_offset, domain.value_size))
            return invalid(error, "Invalid ARIE selection domain");
        // Native b3a930 leaves the zero-initialized slot unchanged on no match.
        const auto count = domain.values.size()/domain.value_size;
        const auto first = selection.begin()+domain.source_offset;
        for (std::size_t index = 0; index < count; ++index) {
            const auto at = index*domain.value_size;
            if (std::equal(first, first+domain.value_size, domain.values.begin()+at)) {
                slots[domain.slot] = static_cast<std::uint32_t>(index);
                break;
            }
        }
    }
    // The original arithmetic is uint32 wraparound (IMUL/ADD). CODE's first
    // word is not consumed by b3a930; its second is a byte offset into DATA.
    std::uint32_t index = 0;
    const auto count = size/4;
    for (std::uint32_t i = 2; i < count; i += 2) {
        if (!contains(code, begin+std::uint64_t(i+1)*4, 4))
            return invalid(error, "Truncated ARIE selector slot pair");
        const auto slot = word(code, begin+std::uint64_t(i+1)*4);
        if (slot >= slots.size()) return invalid(error, "Invalid ARIE selector slot");
        index += word(code, begin+std::uint64_t(i)*4)*slots[slot];
    }
    const auto at = std::uint64_t(word(code, begin+4))+std::uint64_t(index)*4;
    if (!contains(definition.selector_data, at, 4))
        return invalid(error, "ARIE selector result is outside DATA");
    value = word(definition.selector_data, at);
    error.clear(); return true;
}

bool resolve_stock_effect_reference(const StockEffectBinary& definition,
    const std::vector<unsigned char>& selection, StockEffectResourceTable table,
    std::uint32_t reference, std::optional<std::uint32_t>& value, std::string& error) {
    if (reference == 0xffffffff) { value.reset(); error.clear(); return true; }
    const auto index = static_cast<std::size_t>(table);
    if (index >= definition.resource_indirections.size() ||
        reference >= definition.resource_indirections[index].size())
        return invalid(error, "ARIE resource reference is outside RTB");
    const auto encoded = definition.resource_indirections[index][reference];
    std::uint32_t resolved = encoded;
    if (encoded >= 0x10000 && !evaluate_stock_effect_selector(
            definition, selection, encoded-0x10000, resolved, error)) return false;
    value = resolved; error.clear(); return true;
}

bool select_stock_dx9_pass(const StockEffectBinary& definition,
    const std::vector<unsigned char>& selection, std::size_t technique,
    std::size_t pass, StockDx9PassSelection& output, std::string& error) {
    if (technique >= definition.techniques.size() ||
        pass >= definition.techniques[technique].passes.size())
        return invalid(error, "ARIE pass is outside its technique");
    const auto& descriptor = definition.techniques[technique].passes[pass].descriptor;
    // Full #1513 DX9 corpus uses only render-state, pixel, and vertex fields.
    // A future unsupported stage must fail explicitly rather than being dropped.
    if (std::any_of(descriptor.begin()+3, descriptor.end(),
            [](std::uint32_t field) { return field != 0xffffffff; }))
        return invalid(error, "ARIE pass requires a non-DX9 shader stage");
    StockDx9PassSelection selected;
    if (!resolve_stock_effect_reference(definition, selection, StockEffectResourceTable::render_state,
            descriptor[0], selected.render_state, error) ||
        !resolve_stock_effect_reference(definition, selection, StockEffectResourceTable::shader,
            descriptor[1], selected.pixel_shader, error) ||
        !resolve_stock_effect_reference(definition, selection, StockEffectResourceTable::shader,
            descriptor[2], selected.vertex_shader, error)) return false;
    if (selected.render_state && *selected.render_state >= definition.render_states.size())
        return invalid(error, "Selected ARIE render-state index is invalid");
    for (const auto& shader : {selected.vertex_shader, selected.pixel_shader})
        if (shader && *shader >= definition.shaders.size())
            return invalid(error, "Selected ARIE shader index is invalid");
    if ((selected.vertex_shader && definition.shaders[*selected.vertex_shader].stage != 1) ||
        (selected.pixel_shader && definition.shaders[*selected.pixel_shader].stage != 2))
        return invalid(error, "Selected ARIE shader stage does not match its pass");
    output = selected; error.clear(); return true;
}

namespace {
std::uint32_t name_hash(const std::string& text) {
    std::uint32_t hash = 0x811c9dc5;
    for (unsigned char value : text) hash = (hash ^ value)*0x1000193;
    return hash;
}
std::uint32_t word_hash(std::uint32_t word) {
    std::uint32_t hash = 0x811c9dc5;
    for (unsigned i = 0; i < 4; ++i) hash = (hash ^ ((word >> (i*8)) & 0xff))*0x1000193;
    return hash;
}
} // namespace

std::uint32_t stock_effect_uniform_template_key(const StockEffectUniformBuffer& buffer) {
    std::uint32_t hash = name_hash(buffer.name);
    for (const auto& uniform : buffer.uniforms) {
        hash += name_hash(uniform.name);
        if (uniform.semantic_present) hash += name_hash(uniform.semantic);
        hash += word_hash(uniform.type_index) + word_hash(uniform.byte_offset) +
            word_hash(uniform.elements) + word_hash(uniform.element_padding);
    }
    return hash + word_hash(static_cast<std::uint32_t>(buffer.defaults.size()));
}

StockEffectUniformStorage::StockEffectUniformStorage(std::vector<unsigned char> defaults,
    std::uint32_t revision_base) : bytes_(std::move(defaults)), revision_(revision_base),
        revision_limit_(revision_base+0x1000) { advance_revision(); }

void StockEffectUniformStorage::advance_revision() {
    ++revision_;
    if (revision_ >= revision_limit_) revision_ = revision_limit_-0x1000;
}

bool StockEffectUniformStorage::write(std::size_t offset, const void* data,
    std::size_t size, std::string& error) {
    if ((!data && size) || !contains(bytes_, offset, size))
        return invalid(error, "Uniform write is outside its storage");
    if (size && std::memcmp(bytes_.data()+offset, data, size) != 0) {
        std::memcpy(bytes_.data()+offset, data, size);
        advance_revision();
    }
    error.clear(); return true;
}

bool StockEffectUniformStorage::write_uniform(const StockEffectUniform& uniform,
    const StockEffectType& type, const void* data, std::size_t size, std::string& error) {
    if (uniform.flags & 2) { error.clear(); return true; }
    if (!data && size) return invalid(error, "Uniform write requires source bytes");
    if (uniform.element_padding && uniform.elements > 1) {
        if (!type.byte_size) return invalid(error, "Padded uniform element has zero size");
        const auto count = std::min<std::size_t>(uniform.elements, size/type.byte_size);
        if (count && !contains(bytes_, std::uint64_t(uniform.byte_offset)+
                std::uint64_t(count-1)*(std::uint64_t(type.byte_size)+uniform.element_padding),
                type.byte_size))
            return invalid(error, "Padded uniform write is outside its storage");
        const auto* source = static_cast<const unsigned char*>(data);
        for (std::size_t i = 0; i < count; ++i) {
            const auto at = std::uint64_t(uniform.byte_offset)+
                i*(std::uint64_t(type.byte_size)+uniform.element_padding);
            if (!write(static_cast<std::size_t>(at), source+i*type.byte_size,
                       type.byte_size, error)) return false;
        }
        error.clear(); return true;
    }
    const auto count = std::min<std::uint64_t>(size, std::uint64_t(type.byte_size)*uniform.elements);
    return write(uniform.byte_offset, data, static_cast<std::size_t>(count), error);
}

std::size_t StockEffectUniformPool::register_buffer(const StockEffectUniformBuffer& buffer) {
    auto key = stock_effect_uniform_template_key(buffer);
    while (true) {
        const auto found = keys_.find(key);
        if (found == keys_.end()) {
            const auto index = declarations_.size();
            declarations_.push_back({buffer.name, buffer.uniforms.size(), buffer.defaults, 0});
            keys_.emplace(key, index);
            return index;
        }
        const auto& declaration = declarations_[found->second];
        // b33e30 probes key+1 on collision; an existing matching declaration
        // retains its original defaults, even when another effect supplies new ones.
        if (declaration.name == buffer.name && declaration.member_count == buffer.uniforms.size() &&
            declaration.defaults.size() == buffer.defaults.size()) return found->second;
        ++key;
    }
}

std::shared_ptr<StockEffectUniformStorage> StockEffectUniformPool::clone(std::size_t declaration) {
    auto& source = declarations_.at(declaration);
    const auto base = source.next_revision_base;
    source.next_revision_base += 0x1000;
    if (source.next_revision_base < base) source.next_revision_base = 0;
    return std::make_shared<StockEffectUniformStorage>(source.defaults, base);
}

std::shared_ptr<StockEffectUniformStorage> StockEffectUniformContext::shared(std::size_t declaration) {
    if (declaration >= shared_.size()) shared_.resize(declaration+1);
    auto& storage = shared_[declaration];
    if (!storage) storage = pool_.clone(declaration);
    return storage;
}

StockEffectBinding::StockEffectBinding(std::shared_ptr<const StockEffectBinary> definition,
    StockEffectUniformContext& context) : definition_(std::move(definition)), context_(context) {
    if (!definition_) throw std::invalid_argument("Effect binding requires a definition");
    buffers_.resize(definition_->uniform_buffers.size());
    for (std::size_t i = 0; i < buffers_.size(); ++i) {
        const auto& buffer = definition_->uniform_buffers[i];
        if (buffer.name == "$Selection") {
            selection_ = buffer.defaults; selection_buffer_ = i;
            continue;
        }
        auto& binding = buffers_[i];
        binding.declaration = context_.pool().register_buffer(buffer);
        if (!(buffer.flags & 1)) binding.storage = context_.pool().clone(binding.declaration);
    }
}

std::shared_ptr<StockEffectUniformStorage> StockEffectBinding::uniform_buffer(std::size_t index) {
    if (selection_buffer_ == index) return {};
    auto& buffer = buffers_.at(index);
    if (!buffer.storage) buffer.storage = context_.shared(buffer.declaration);
    return buffer.storage;
}

bool StockEffectBinding::set_uniform(std::size_t buffer, std::size_t uniform,
    const void* data, std::size_t size, std::optional<std::uint32_t> basic_type, std::string& error) {
    if (buffer >= definition_->uniform_buffers.size() ||
        uniform >= definition_->uniform_buffers[buffer].uniforms.size())
        return invalid(error, "Uniform is outside its effect definition");
    const auto& declaration = definition_->uniform_buffers[buffer];
    const auto& parameter = declaration.uniforms[uniform];
    if (declaration.flags & 1)
        return invalid(error, "Shared UBB members are not material parameters");
    if (parameter.type_index >= definition_->types.size())
        return invalid(error, "Uniform has an invalid type index");
    const auto& type = definition_->types[parameter.type_index];
    if (basic_type && type.basic_type != *basic_type)
        return invalid(error, "Uniform basic type does not match the setter");
    if (parameter.flags & 2) { error.clear(); return true; }
    if (selection_buffer_ == buffer) {
        // b3dd40/b3a550's directly addressed $Selection path copies the caller's
        // byte count without per-parameter clamping; retain the enclosing bound.
        if ((!data && size) || !contains(selection_, parameter.byte_offset, size))
            return invalid(error, "Selection write is outside its storage");
        if (size) std::memcpy(selection_.data()+parameter.byte_offset, data, size);
        error.clear(); return true;
    }
    return uniform_buffer(buffer)->write_uniform(parameter, type, data, size, error);
}

bool StockEffectBinding::select_pass(std::size_t technique, std::size_t pass,
    StockDx9PassSelection& output, std::string& error) const {
    return select_stock_dx9_pass(*definition_, selection_, technique, pass, output, error);
}

bool StockEffectBinding::copy_values_from(const StockEffectBinding& source, std::string& error) {
    if (definition_.get() != source.definition_.get())
        return invalid(error, "Cannot copy values between different effect definitions");
    for (std::size_t i = 0; i < buffers_.size(); ++i) {
        if ((definition_->uniform_buffers[i].flags & 1) || selection_buffer_ == i) continue;
        const auto& bytes = source.buffers_[i].storage->bytes();
        if (!buffers_[i].storage->write(0, bytes.data(), bytes.size(), error)) return false;
    }
    selection_ = source.selection_;
    error.clear(); return true;
}

} // namespace wot
