"""Exercise the production Moo owner switch with a strict effect lifecycle."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PATCH_PATH = Path(__file__).parents[1] / "bigworld" / "patch_stock_effect_owners.py"
spec = importlib.util.spec_from_file_location("stock_effect_owners_patch", PATCH_PATH)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class StockEffectOwnerTests(unittest.TestCase):
    def test_changed_source_is_rejected_and_reapplication_is_stable(self):
        self.assertEqual(patch.replace_once("before", "before", "after", "probe"), "after")
        self.assertEqual(patch.replace_once("after", "before", "after", "probe"), "after")
        for text in ("missing", "before before"):
            with self.assertRaises(RuntimeError):
                patch.replace_once(text, "before", "after", "probe")

    def test_material_switch_preserves_pass_and_independent_values(self):
        compiler = shutil.which("c++")
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        # Compile the actual generated owner class/function. The effect double
        # rejects incorrect Begin/Pass order rather than accepting every call.
        prefix = r'''
#include <cassert>
#include <climits>
#include <cstdint>
#include <map>
#include <string>
using uint32 = std::uint32_t;
using UINT = unsigned;
using HRESULT = int;
using D3DXHANDLE = const char*;
constexpr HRESULT E_FAIL = -1;
constexpr unsigned D3DXFX_DONOTSAVESTATE = 1;
#define FAILED(value) ((value) < 0)
#define ERROR_MSG(...) ((void)0)
namespace BW { template<class K,class V> using map = std::map<K,V>; }
struct ID3DXEffect {
    int refs = 1;
    int value = 0;
    bool begun = false;
    bool pass = false;
    void AddRef() { ++refs; }
    void Release() { if (!--refs) delete this; }
    HRESULT SetTechnique(D3DXHANDLE) { return begun ? E_FAIL : 0; }
    HRESULT Begin(UINT* count, unsigned) {
        if (begun) return E_FAIL;
        begun = true; *count = 1; return 0;
    }
    HRESULT BeginPass(unsigned index) {
        if (!begun || pass || index != 0) return E_FAIL;
        pass = true; return 0;
    }
    HRESULT EndPass() {
        if (!begun || !pass) return E_FAIL;
        pass = false; return 0;
    }
    HRESULT End() {
        if (!begun || pass) return E_FAIL;
        begun = false; return 0;
    }
};
template<class T> struct ComObjectWrap {
    T* pointer = nullptr;
    ComObjectWrap() = default;
    ComObjectWrap(T* value) : pointer(value) { if (pointer) pointer->AddRef(); }
    ComObjectWrap(const ComObjectWrap& value) : ComObjectWrap(value.pointer) {}
    ~ComObjectWrap() { if (pointer) pointer->Release(); }
    ComObjectWrap& operator=(const ComObjectWrap& value) {
        T* next = value.pointer;
        if (next) next->AddRef();
        if (pointer) pointer->Release();
        pointer = next; return *this;
    }
    T* pComObject() const { return pointer; }
    T* operator->() const { return pointer; }
    explicit operator bool() const { return pointer != nullptr; }
};
namespace wot {
    bool fail_create = false;
    bool is_stock_effect(ID3DXEffect*) { return true; }
    HRESULT create_stock_effect_material(ID3DXEffect*, ID3DXEffect** output) {
        if (fail_create) return E_FAIL;
        *output = new ID3DXEffect; return 0;
    }
}
using EffectPropertyPtr = int;
struct ManagedEffect {
    using CompileMark = uint32;
    ComObjectWrap<ID3DXEffect> pEffect_;
    ComObjectWrap<ID3DXEffect> activeBinding_;
    CompileMark compileMark_ = 1;
    unsigned numPasses_ = 0;
    unsigned stockPass_ = UINT_MAX;
    bool stockBindingValid_ = true;
    std::string resourceID_ = "original.fx";
    ID3DXEffect* pEffect() { return activeBinding_ ? activeBinding_.pComObject() : pEffect_.pComObject(); }
    D3DXHANDLE currentTechnique() { return "OriginalTechnique"; }
    bool selectMaterialBinding(ComObjectWrap<ID3DXEffect>&, ComObjectWrap<ID3DXEffect>&, CompileMark&);
};
struct EffectMaterial {
'''
        suffix = r'''
int main() {
    ManagedEffect effect;
    auto* definition = new ID3DXEffect;
    effect.pEffect_ = definition;
    definition->Release();
    definition->value = 99; // An independent utility draw must not seed materials.
    EffectMaterial::Properties first, second;
    assert(first.select(effect));
    auto* first_binding = effect.pEffect();
    assert(first_binding != definition && first_binding->value == 0);
    first_binding->value = 11;
    assert(!FAILED(first_binding->SetTechnique(effect.currentTechnique())));
    assert(!FAILED(first_binding->Begin(&effect.numPasses_, D3DXFX_DONOTSAVESTATE)));
    effect.stockPass_ = 0;
    assert(!FAILED(first_binding->BeginPass(0)));
    assert(second.select(effect));
    auto* second_binding = effect.pEffect();
    assert(second_binding != first_binding && second_binding->value == 0);
    assert(second_binding->begun && second_binding->pass);
    assert(!first_binding->begun && !first_binding->pass);
    second_binding->value = 22;
    assert(first.select(effect));
    assert(effect.pEffect() == first_binding && first_binding->value == 11);
    assert(first_binding->begun && first_binding->pass);
    assert(!second_binding->begun && !second_binding->pass);
    first["OriginalProperty"] = 7;
    EffectMaterial::Properties copied(first);
    assert(copied["OriginalProperty"] == 7);
    assert(copied.select(effect));
    assert(effect.pEffect() != first_binding && effect.pEffect()->value == 0);
    EffectMaterial::Properties failure;
    wot::fail_create = true;
    assert(!failure.select(effect));
    assert(!effect.stockBindingValid_);
    wot::fail_create = false;
    assert(failure.select(effect) && effect.stockBindingValid_);
    assert(effect.pEffect()->begun && effect.pEffect()->pass);
    const auto* before_recompile = effect.pEffect();
    ++effect.compileMark_;
    assert(failure.select(effect));
    assert(effect.pEffect() != before_recompile);
    assert(effect.pEffect()->begun && effect.pEffect()->pass);
    assert(!FAILED(effect.pEffect()->EndPass()));
    assert(!FAILED(effect.pEffect()->End()));
}
'''
        source = prefix + patch.PROPERTIES + "\n};\n" + patch.SELECT_BINDING + suffix
        with tempfile.TemporaryDirectory(prefix="wot-effect-owner-test-") as directory:
            path = Path(directory)
            (path / "test.cpp").write_text(source)
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pedantic",
                       str(path / "test.cpp"), "-o", str(path / "test")]
            subprocess.run(command, check=True, capture_output=True, text=True)
            subprocess.run([str(path / "test")], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
