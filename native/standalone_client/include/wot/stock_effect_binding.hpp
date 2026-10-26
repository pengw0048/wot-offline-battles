#pragma once

#include "wot/stock_shader_binary.hpp"

#include <optional>
#include <memory>
#include <map>

namespace wot {

// #1513 ARIE RTB order, independent of shader stage numbering.
enum class StockEffectResourceTable : std::uint32_t {
    sampler_state, resource_view, unordered_view, uniform_buffer, render_state, shader
};

// b3a930: match each STB domain against the material's $Selection bytes, then
// evaluate the CODE coefficient table and read one uint32 from DATA.
bool evaluate_stock_effect_selector(const StockEffectBinary& definition,
    const std::vector<unsigned char>& selection, std::uint32_t code_offset,
    std::uint32_t& value, std::string& error);

// An absent (-1) source reference remains absent. Fixed RTB entries are returned
// directly; entries >= 0x10000 execute the original selector table.
bool resolve_stock_effect_reference(const StockEffectBinary& definition,
    const std::vector<unsigned char>& selection, StockEffectResourceTable table,
    std::uint32_t reference, std::optional<std::uint32_t>& value, std::string& error);

struct StockDx9PassSelection {
    std::optional<std::uint32_t> render_state;
    std::optional<std::uint32_t> vertex_shader;
    std::optional<std::uint32_t> pixel_shader;
};

// Resolves the original pass against current material selection values. It does
// not create or apply device state and does not invent shaders for absent stages.
bool select_stock_dx9_pass(const StockEffectBinary& definition,
    const std::vector<unsigned char>& selection, std::size_t technique,
    std::size_t pass, StockDx9PassSelection& output, std::string& error);

// Native template key b34180. Name, optional semantics, type/offset/array
// layout, and byte size contribute; default values do not contribute.
std::uint32_t stock_effect_uniform_template_key(const StockEffectUniformBuffer& buffer);

class StockEffectUniformStorage {
public:
    StockEffectUniformStorage(std::vector<unsigned char> defaults, std::uint32_t revision_base);
    const std::vector<unsigned char>& bytes() const { return bytes_; }
    std::uint32_t revision() const { return revision_; }
    // b30d30 changes revision only when the actual bytes change.
    bool write(std::size_t offset, const void* data, std::size_t size, std::string& error);
    // b3a590: padded arrays consume complete source elements; nonarrays clamp
    // to the declared byte count. Read-only uniforms are successful no-ops.
    bool write_uniform(const StockEffectUniform& uniform, const StockEffectType& type,
                       const void* data, std::size_t size, std::string& error);
private:
    void advance_revision();
    std::vector<unsigned char> bytes_;
    std::uint32_t revision_ = 0;
    std::uint32_t revision_limit_ = 0;
};

// b33df0/b33e30 own declaration/default templates; b34130 allocates a distinct
// 0x1000 revision range for every mutable clone of a template.
class StockEffectUniformPool {
public:
    std::size_t register_buffer(const StockEffectUniformBuffer& buffer);
    std::shared_ptr<StockEffectUniformStorage> clone(std::size_t declaration);
    std::size_t declaration_count() const { return declarations_.size(); }
private:
    struct Declaration {
        std::string name;
        std::size_t member_count = 0;
        std::vector<unsigned char> defaults;
        std::uint32_t next_revision_base = 0;
    };
    std::map<std::uint32_t, std::size_t> keys_;
    std::vector<Declaration> declarations_;
};

// Native b32910 separates shared instances from global declarations. One
// render context lazily creates one shared storage for each declaration.
class StockEffectUniformContext {
public:
    explicit StockEffectUniformContext(StockEffectUniformPool& pool) : pool_(pool) {}
    std::shared_ptr<StockEffectUniformStorage> shared(std::size_t declaration);
    StockEffectUniformPool& pool() { return pool_; }
private:
    StockEffectUniformPool& pool_;
    std::vector<std::shared_ptr<StockEffectUniformStorage>> shared_;
};

// Per-material owner corresponding to b3a620: shared UBBs use the context;
// private UBBs clone templates; $Selection has its own directly writable bytes.
class StockEffectBinding {
public:
    StockEffectBinding(std::shared_ptr<const StockEffectBinary> definition,
                       StockEffectUniformContext& context);
    const StockEffectBinary& definition() const { return *definition_; }
    const std::vector<unsigned char>& selection() const { return selection_; }
    std::shared_ptr<StockEffectUniformStorage> uniform_buffer(std::size_t index);
    bool set_uniform(std::size_t buffer, std::size_t uniform, const void* data,
        std::size_t size, std::optional<std::uint32_t> basic_type, std::string& error);
    bool select_pass(std::size_t technique, std::size_t pass,
                     StockDx9PassSelection& output, std::string& error) const;
private:
    struct Buffer {
        std::size_t declaration = 0;
        std::shared_ptr<StockEffectUniformStorage> storage;
    };
    std::shared_ptr<const StockEffectBinary> definition_;
    StockEffectUniformContext& context_;
    std::vector<Buffer> buffers_;
    std::vector<unsigned char> selection_;
    std::optional<std::size_t> selection_buffer_;
};

} // namespace wot
