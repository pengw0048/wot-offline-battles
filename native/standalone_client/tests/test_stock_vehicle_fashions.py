"""Compile the real track kernel and material clone against shared properties.

The host reproduces the public SDK's shallow EffectPropertyPtr copy. This
checks per-vehicle isolation and original signed scroll receipts; it cannot
prove DX9 shader selection or rendering on Windows.
"""
from pathlib import Path
import importlib.util
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VehicleFashionsTest(unittest.TestCase):
    def test_original_handler_shader_receipts(self):
        source = (ROOT / 'bigworld/stock_vehicle_fashions.cpp').read_text()
        begin = source.index('Vector4 packedVehicleColour(')
        end = source.index('\nstruct VehicleMaterialHandlerEntry', begin)
        host = r'''
#include <cassert>
#include <cstdint>
#include <map>
#include <memory>
#include <string>
using uint32=std::uint32_t;
struct Vector4 {
    float x,y,z,w;
    Vector4(float a=0,float b=0,float c=0,float d=0):x(a),y(b),z(c),w(d) {}
    static Vector4 zero() { return {}; }
    float operator[](std::size_t i) const { return (&x)[i]; }
};
namespace Moo {
using BaseTexturePtr=std::shared_ptr<int>;
struct Property {
    BaseTexturePtr texture;
    bool getTexture(BaseTexturePtr& out) { out=texture;return true; }
};
using EffectPropertyPtr=std::shared_ptr<Property>;
struct EffectMaterial {
    std::map<std::string,Vector4> vectors;
    std::map<std::string,bool> booleans;
    std::map<std::string,BaseTexturePtr> textures;
    EffectPropertyPtr id;
    void setProperty(const std::string& n,const Vector4& v,bool) { vectors[n]=v; }
    void setProperty(const std::string& n,bool v,bool) { booleans[n]=v; }
    void setProperty(const std::string& n,const BaseTexturePtr& v,bool) { textures[n]=v; }
    EffectPropertyPtr getProperty(const std::string&) { return id; }
};
}
'''
        cases = r'''
int main() {
    auto red=packedVehicleColour(0xff0000ffU);
    assert(red.x==1.f && red.y==0.f && red.z==0.f && red.w==1.f);
    VehicleCamoState camo;
    camo.tiling={2.f,3.f,4.f,5.f};camo.weights={0.1f,0.2f,0.3f,0.4f};
    camo.colours[0]=0x00332211U;
    camo.tint=std::make_shared<int>(1);
    Moo::EffectMaterial legacy;
    camo.apply(legacy);
    assert(legacy.textures.at("tintMap")==camo.tint);
    assert(legacy.vectors.at("tintTiling").w==5.f);
    assert(legacy.vectors.at("colorTint1").w==0.1f);
    assert(legacy.booleans.at("g_useOldCamoDetail"));
    legacy.id=std::make_shared<Moo::Property>();
    legacy.id->texture=std::make_shared<int>(2);
    camo.apply(legacy);
    assert(!legacy.booleans.at("g_useOldCamoDetail"));
    VehicleRepaintState repaint;
    repaint.base=packedVehicleColour(0xff0000ffU);
    Moo::EffectMaterial disabled;
    repaint.apply(disabled);
    assert(!disabled.booleans.at("g_useRepaint"));
    assert(disabled.vectors.size()==1);
    repaint.enabled=true;repaint.metallic={1,2,3,4};repaint.gloss={5,6,7,8};
    repaint.fading=0.25f;repaint.quality=0.75f;
    repaint.apply(disabled);
    assert(disabled.booleans.at("g_useRepaint"));
    assert(disabled.vectors.at("g_repaintMetallic").w==0.75f);
    assert(disabled.vectors.at("g_repaintGloss").w==0.25f);
}
'''
        with tempfile.TemporaryDirectory(prefix='stock-handler-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(host + source[begin:end] + cases)
            subprocess.run([
                os.environ.get('CXX', 'clang++'), '-std=c++17', '-Wall',
                '-Wextra', '-Werror', '-ffp-contract=off', str(path / 'probe.cpp'),
                '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)

    def test_track_receipts_and_property_isolation(self):
        source = (ROOT / 'bigworld/stock_vehicle_fashions.cpp').read_text()
        begin = source.index('struct VehicleTrackTextureState\n')
        end = source.index('\nbool excludedFromCamouflage(', begin)
        clone_begin = source.index('Moo::ComplexEffectMaterialPtr cloneMaterial(')
        clone_end = source.index('\nMoo::ComplexEffectMaterialPtr materialVariant(', clone_begin)
        host = r'''
#include <cassert>
#include <cmath>
#include <map>
#include <memory>
template<class T> struct Ptr : std::shared_ptr<T> {
    Ptr()=default;
    Ptr(T* p):std::shared_ptr<T>(p) {}
    Ptr& operator=(T* p) { this->reset(p);return *this; }
};
namespace Moo {
struct Property {
    float value=0.f;
    Property* clone() const { return new Property(*this); }
};
struct EffectMaterial {
    using Properties=std::map<int,Ptr<Property>>;
    Properties values;
    Properties& properties() { return values; }
};
struct ComplexEffectMaterial {
    Ptr<EffectMaterial> material=new EffectMaterial();
    ComplexEffectMaterial()=default;
    ComplexEffectMaterial(const ComplexEffectMaterial& other):
        material(new EffectMaterial(*other.material)) {}
    Ptr<EffectMaterial>& baseMaterial() { return material; }
};
using ComplexEffectMaterialPtr=Ptr<ComplexEffectMaterial>;
}
'''
        cases = r'''
int main() {
    Moo::ComplexEffectMaterialPtr source=new Moo::ComplexEffectMaterial();
    source->baseMaterial()->values[0]=new Moo::Property();
    source->baseMaterial()->values[0]->value=0.75f;
    auto first=cloneMaterial(source);
    auto second=cloneMaterial(source);
    first->baseMaterial()->values[0]->value=0.125f;
    assert(source->baseMaterial()->values[0]->value==0.75f);
    assert(second->baseMaterial()->values[0]->value==0.75f);
    assert(first->baseMaterial()->values[0].get()!=source->baseMaterial()->values[0].get());

    VehicleTrackTextureState tracks;
    tracks.update(1.25f,-1.25f,1.f,0);
    assert(tracks.left==0.25f && tracks.right==-0.25f);
    tracks.update(-0.5f,0.5f,1.f,0);
    assert(tracks.left==-0.25f && tracks.right==0.25f);
    tracks.update(0.5f,0.5f,1.f,1);
    assert(tracks.left==-0.25f && tracks.right==0.75f);
    tracks.update(-150.f,150.f,1.f,0);
    assert(tracks.left==-0.25f && tracks.right==0.75f);
    tracks.update(101.f,0.f,1.f,3);
    assert(tracks.left==0.f && tracks.right==0.f);
}
'''
        with tempfile.TemporaryDirectory(prefix='stock-fashion-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(
                host + source[begin:end] + source[clone_begin:clone_end] + cases)
            subprocess.run([
                os.environ.get('CXX', 'clang++'), '-std=c++17', '-Wall',
                '-Wextra', '-Werror', '-ffp-contract=off', str(path / 'probe.cpp'),
                '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)

    def test_null_material_patch_is_narrow_and_idempotent(self):
        spec = importlib.util.spec_from_file_location(
            'fashion_patch', ROOT / 'bigworld/patch_vehicle_fashions.py')
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        before = ('\tconst bool canUseHWInstancing = this->hwInstancingAvailable() &&\n'
                  '\t\t\t\t\t\t\t\t\timmediateModeCounter_ == 0;')
        with tempfile.TemporaryDirectory(prefix='stock-fashion-patch-') as directory:
            path = Path(directory) / 'lib/moo/draw_context.cpp'
            path.parent.mkdir(parents=True)
            path.write_text(before)
            patch.patch_engine(directory)
            first = path.read_text()
            self.assertIn('if (!material)\n\t\treturn;', first)
            patch.patch_engine(directory)
            self.assertEqual(path.read_text(), first)


if __name__ == '__main__':
    unittest.main()
