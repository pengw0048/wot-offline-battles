#include "wot/stock_effect_reflection.hpp"

#include <cstring>
#include <iostream>
#include <stdexcept>

namespace {
void check(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
std::shared_ptr<wot::StockEffectBinary> definition() {
    auto effect = std::make_shared<wot::StockEffectBinary>();
    wot::StockEffectType scalar; scalar.name = "float"; scalar.basic_type = 3; scalar.byte_size = 4;
    auto texture = scalar; texture.name = "texture"; texture.basic_type = 5; texture.type_class = 4;
    effect->types = {scalar,texture};
    wot::StockEffectUniform value;
    value.name = "zValue"; value.semantic = "VALUE"; value.semantic_present = true;
    value.elements = 1; value.annotations = 0;
    auto readonly = value; readonly.name = "aValue"; readonly.flags = 2;
    readonly.semantic_present = false;
    wot::StockEffectUniformBuffer local;
    local.name = "$Global"; local.uniforms = {value,readonly};
    local.defaults = {0x78,0x56,0x34,0x12,1,2,3,4,5,6,7,8,9,10,11,12};
    auto shared = local; shared.name = "PerFrame"; shared.flags = 1;
    effect->uniform_buffers = {local,shared};
    wot::StockEffectResource automatic;
    automatic.name = "automaticMap"; automatic.semantic = "AUTO_TEXTURE";
    automatic.semantic_present = true; automatic.type_index = 1; automatic.elements = 1;
    auto material = automatic; material.name = "diffuseMap"; material.semantic.clear();
    material.semantic_present = false; material.annotations = 0;
    effect->resource_views = {automatic,material};
    effect->annotations = {{{"artistEditable",4,{1,0,0,0}}},
                           {{"channel",5,{'n','o','n','e',0}}}};
    effect->techniques = {{"SHADOW",0xffffffff,{}},{"COLOR",1,{}}};
    return effect;
}
void reflection_checks() {
    auto effect = definition();
    wot::StockEffectReflection reflection(effect, {"AUTO_TEXTURE"});
    check(reflection.parameter_count() == 4, "Native shared UBB pseudo parameter and private members");
    check(reflection.parameter_reference(reflection.parameter(0))->name == "PerFrame" &&
          reflection.parameter_reference(reflection.parameter(1))->name == "aValue" &&
          reflection.parameter_reference(reflection.parameter(2))->name == "diffuseMap" &&
          reflection.parameter_reference(reflection.parameter(3))->name == "zValue",
          "Name-sorted native parameter namespace");
    check(reflection.parameter_by_name("automaticMap") == 0 &&
          reflection.parameter_by_name("zValue") == 0x1004 &&
          reflection.parameter_by_name("ZValue") == 0,
          "Registered automatic resources are hidden; names remain case-sensitive");
    check(reflection.parameter_by_semantic("VALUE") == 0x1004 &&
          !reflection.parameter_used(reflection.parameter_by_name("aValue")) &&
          reflection.parameter_used(reflection.parameter_by_name("zValue")),
          "Semantic lookup and native read-only used predicate");
    const auto color = reflection.technique_by_name("COLOR");
    check(color == 0x4001 && reflection.technique_source_index(color) == 1 &&
          reflection.technique_source_index(reflection.technique(1)) == 0,
          "Technique names/handles use sorted runtime order with source-index projection");
    const auto channel = reflection.annotation_by_name(color, "channel");
    check(channel == 0x2002 && reflection.annotation(color,0) == channel &&
          reflection.annotation_reference(channel)->type == 5 &&
          reflection.annotation_reference(channel)->value[0] == 'n',
          "Technique annotation resolves through the original flat annotation namespace");
    const auto z = reflection.parameter_by_name("zValue");
    check(reflection.annotation_by_name(z,"artistEditable") == 0x2001 &&
          reflection.parameter_type(z)->basic_type == 3 && reflection.parameter_elements(z) == 1,
          "Parameter annotations and original type metadata");
    std::uint32_t words[4]{};
    check(reflection.default_value(z,words,sizeof(words)) && words[0] == 0x12345678 &&
          words[1] == 0x04030201, "Vector getter width reads immutable buffer bytes without field clamping");
    effect->uniform_buffers[0].defaults[0] = 0x99;
    // Definition bytes have a single immutable owner once published. This local
    // construction test checks that reflection did not copy a second value table.
    check(reflection.default_value(z,words,4) && words[0] == 0x12345699,
          "Reflection reads the definition's value table");
    check(!reflection.default_value(z,words,17) &&
          !reflection.parameter_reference(0x1000) && !reflection.annotation_reference(z) &&
          !reflection.technique_source_index(0x7001), "Invalid widths/handle kinds are contained");
    wot::StockEffectReflection unregistered(effect, {});
    check(unregistered.parameter_count() == 5 && unregistered.parameter_by_name("automaticMap"),
          "Resource is reflected when its semantic has no registered producer");
}
} // namespace
int main() {
    try {
        reflection_checks();
        std::cout << "Stock effect reflection checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
