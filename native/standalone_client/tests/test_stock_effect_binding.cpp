#include "wot/stock_effect_binding.hpp"

#include <iostream>
#include <cstring>
#include <stdexcept>

namespace {
using Bytes = std::vector<unsigned char>;
void check(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
void append(Bytes& bytes, std::uint32_t word) {
    for (unsigned i = 0; i < 4; ++i) bytes.push_back(static_cast<unsigned char>(word >> (8*i)));
}
wot::StockEffectBinary definition() {
    wot::StockEffectBinary effect;
    effect.selection_domains = {{0, 4, 1, {0,0,0,0, 0,0,0,0x80}},
                                 {4, 2, 0, {0x11,0x22, 0x33,0x44, 0x55,0x66}}};
    // CODE: byte count, ignored word, DATA offset, coefficient/slot pairs.
    for (auto word : {24u, 123u, 4u, 2u, 1u, 1u, 0u}) append(effect.selector_code, word);
    append(effect.selector_data, 0x9999);
    for (unsigned i = 0; i < 5; ++i) append(effect.selector_data, 10+i);
    effect.resource_indirections[5] = {0x10000, 2};
    return effect;
}
void selector_checks() {
    const auto effect = definition();
    Bytes selection{0,0,0,0x80, 0x55,0x66};
    std::uint32_t value = 0;
    std::string error;
    check(wot::evaluate_stock_effect_selector(effect, selection, 0, value, error) && value == 14,
          "Selection uses exact bytes and coefficient/slot indices");
    selection[0] = 1; // No domain match leaves that slot zero, as in b3a930.
    check(wot::evaluate_stock_effect_selector(effect, selection, 0, value, error) && value == 12,
          "Unmatched domain retains native zero slot");
    std::optional<std::uint32_t> reference;
    check(wot::resolve_stock_effect_reference(effect, selection, wot::StockEffectResourceTable::shader,
          0, reference, error) && reference == 12, "Dynamic RTB selection");
    check(wot::resolve_stock_effect_reference(effect, selection, wot::StockEffectResourceTable::shader,
          1, reference, error) && reference == 2, "Fixed RTB entry");
    check(wot::resolve_stock_effect_reference(effect, selection, wot::StockEffectResourceTable::shader,
          0xffffffff, reference, error) && !reference, "Absent reference remains absent");
    for (unsigned failure = 0; failure < 5; ++failure) {
        auto invalid = effect;
        if (failure == 0) invalid.selection_domains[0].value_size = 0;
        if (failure == 1) invalid.selection_domains[1].slot = 10;
        if (failure == 2) invalid.selector_code.resize(3);
        if (failure == 3) invalid.selector_code[24] = 17;
        if (failure == 4) invalid.selector_data.resize(4);
        value = 0xabcdef;
        check(!wot::evaluate_stock_effect_selector(invalid, selection, 0, value, error) &&
              !error.empty() && value == 0xabcdef, "Invalid selector fails atomically");
    }
}
void pass_checks() {
    wot::StockEffectBinary effect;
    effect.shaders.resize(2); effect.shaders[0].stage = 1; effect.shaders[1].stage = 2;
    effect.render_states.resize(1);
    effect.resource_indirections[4] = {0}; effect.resource_indirections[5] = {0, 1};
    wot::StockEffectPass pass;
    pass.descriptor = {0, 1, 0, 0xffffffff, 0xffffffff, 0xffffffff, 0xffffffff};
    effect.techniques.push_back({"COLOR", 0xffffffff, {pass}});
    wot::StockDx9PassSelection selected;
    std::string error;
    check(wot::select_stock_dx9_pass(effect, {}, 0, 0, selected, error) &&
          selected.render_state == 0 && selected.vertex_shader == 0 && selected.pixel_shader == 1,
          "Pass source order is render-state, pixel, vertex");
    effect.techniques[0].passes[0].descriptor[3] = 0;
    check(!wot::select_stock_dx9_pass(effect, {}, 0, 0, selected, error),
          "Unsupported stage cannot be silently dropped");
    effect.techniques[0].passes[0].descriptor[3] = 0xffffffff;
    effect.shaders[1].stage = 1;
    check(!wot::select_stock_dx9_pass(effect, {}, 0, 0, selected, error),
          "Selected shader stage must match the pass");
}
void storage_checks() {
    std::string error;
    wot::StockEffectUniformStorage storage(Bytes(32, 0xaa), 0);
    check(storage.revision() == 1, "Native storage starts at base plus one");
    const unsigned char value[] = {1,2,3,4,5,6,7,8,9,10};
    wot::StockEffectUniform uniform;
    uniform.elements = 2; uniform.element_padding = 12;
    wot::StockEffectType type; type.byte_size = 4;
    check(storage.write_uniform(uniform, type, value, sizeof(value), error) &&
          storage.revision() == 3 && storage.bytes()[0] == 1 && storage.bytes()[16] == 5 &&
          storage.bytes()[4] == 0xaa && storage.bytes()[20] == 0xaa,
          "Padded writes use complete source elements and preserve destination padding");
    check(storage.write_uniform(uniform, type, value, sizeof(value), error) &&
          storage.revision() == 3, "Identical bytes do not advance revision");
    uniform.flags = 2;
    check(storage.write_uniform(uniform, type, nullptr, 20, error) && storage.revision() == 3,
          "Read-only uniform is a successful no-op");
    uniform.flags = 0; uniform.byte_offset = 17;
    check(!storage.write_uniform(uniform, type, value, sizeof(value), error) &&
          storage.revision() == 3 && storage.bytes()[17] == 6, "Invalid padded write is atomic");
    wot::StockEffectUniformStorage wrap(Bytes(4), 0);
    for (std::uint32_t i = 1; i <= 4095; ++i)
        check(wrap.write(0, &i, sizeof(i), error), "Revision wrap write");
    check(wrap.revision() == 0, "Native 0x1000 revision range wraps to its base");
}
void ownership_checks() {
    auto effect = std::make_shared<wot::StockEffectBinary>();
    wot::StockEffectType type; type.basic_type = 3; type.byte_size = 4;
    effect->types.push_back(type);
    wot::StockEffectUniform uniform;
    uniform.name = "value"; uniform.elements = 1;
    wot::StockEffectUniformBuffer shared;
    shared.name = "PerView"; shared.flags = 1; shared.uniforms = {uniform}; shared.defaults = Bytes(16);
    auto local = shared; local.name = "$Global"; local.flags = 0;
    auto selection = local; selection.name = "$Selection";
    effect->uniform_buffers = {shared, local, selection};
    wot::StockEffectUniformPool pool;
    wot::StockEffectUniformContext context(pool);
    wot::StockEffectBinding first(effect, context), second(effect, context);
    check(pool.declaration_count() == 2, "Selection is not a uniform storage declaration");
    check(first.uniform_buffer(0) == second.uniform_buffer(0), "Shared UBB has one context owner");
    check(first.uniform_buffer(1) != second.uniform_buffer(1) &&
          first.uniform_buffer(1)->revision() == 1 && second.uniform_buffer(1)->revision() == 0x1001,
          "Each private clone receives its own native revision range");
    wot::StockEffectUniformContext other_context(pool);
    wot::StockEffectBinding third(effect, other_context);
    check(third.uniform_buffer(0) != first.uniform_buffer(0), "Shared storage is per render context");
    const std::uint32_t words[] = {0x12345678, 0xabcdef01};
    std::string error;
    check(first.set_uniform(1, 0, words, sizeof(words), 3, error) &&
          first.uniform_buffer(1)->bytes()[4] == 0 && second.uniform_buffer(1)->bytes()[0] == 0,
          "Private UBB setter clamps to declared bytes and isolates materials");
    check(first.set_uniform(2, 0, words, sizeof(words), 3, error) &&
          first.selection()[4] == 1 && second.selection()[4] == 0,
          "Selection direct copy preserves native caller byte count");
    check(!first.set_uniform(2, 0, words, 4, 2, error), "Typed setter checks basic type");
    check(!first.set_uniform(0, 0, words, 4, 3, error), "Shared UBB members are not material handles");
    check(second.copy_values_from(first,error) && second.selection() == first.selection() &&
          second.uniform_buffer(1)->bytes() == first.uniform_buffer(1)->bytes() &&
          second.uniform_buffer(1) != first.uniform_buffer(1),
          "Material value clone retains independent private storage and copies selection");
    auto changed = shared; changed.defaults[0] = 7;
    check(pool.register_buffer(changed) == pool.register_buffer(shared),
          "Declaration identity excludes defaults");
    check(context.shared(pool.register_buffer(changed))->bytes()[0] == 0,
          "Existing declaration retains the first source defaults");
    changed = shared; changed.uniforms[0].semantic_present = true;
    check(wot::stock_effect_uniform_template_key(changed) !=
          wot::stock_effect_uniform_template_key(shared), "Absent semantic differs from present empty semantic");
}
} // namespace

int main() {
    try {
        selector_checks(); pass_checks(); storage_checks(); ownership_checks();
        std::cout << "Stock effect binding checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
