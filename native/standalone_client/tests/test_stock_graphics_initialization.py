"""Check the real HDR patch and camouflage rule initialization bodies.

The fixtures supply only registry/resource storage; they do not exercise D3D9
rendering or original package IO on Windows.
"""
from pathlib import Path
import importlib.util
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def compile_run(directory, source):
    path = Path(directory)
    (path / 'probe.cpp').write_text(source)
    subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17', '-Wall',
                    '-Wextra', '-Werror', str(path / 'probe.cpp'), '-o',
                    str(path / 'probe')], check=True)
    subprocess.run([str(path / 'probe')], check=True)


class GraphicsInitializationTest(unittest.TestCase):
    def test_hdr_global_and_instance_share_the_actual_owner(self):
        spec = importlib.util.spec_from_file_location('startup_patch',
            ROOT / 'bigworld/patch_stock_startup_services.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='stock-hdr-init-') as directory:
            root = Path(directory)
            location = root / 'lib/romp'
            location.mkdir(parents=True)
            header = location / 'hdr_support.hpp'
            source = location / 'hdr_support.cpp'
            header.write_text('class HDRSupport { public:\n'
                              '\tvoid\t\t\t\t\tenable(bool flag);\n};\n')
            source.write_text('''void HDRSupport::enable(bool flag)
{
    BW_GUARD;
    g_cfg.m_enabled = flag;
}''')
            module.patch(root)
            first = (header.read_text(), source.read_text())
            module.patch(root)
            self.assertEqual(first, (header.read_text(), source.read_text()))
            compile_run(root, '#include <cassert>\n#define BW_GUARD\n'
                'struct { bool m_enabled=false; } g_cfg;\n' + header.read_text() +
                source.read_text() + '''
int main() {
    HDRSupport::setGlobalEnabled(true);
    assert(g_cfg.m_enabled);
    HDRSupport instance;
    instance.enable(false);
    assert(!g_cfg.m_enabled);
    HDRSupport::setGlobalEnabled(true);
    assert(g_cfg.m_enabled);
}
''')

    def test_fashions_consume_the_initialized_rules(self):
        source = (ROOT / 'bigworld/stock_vehicle_fashions.cpp').read_text()
        begin = source.index('bool excludedFromCamouflage(')
        end = source.index('\nMoo::ComplexEffectMaterialPtr cloneMaterial(', begin)
        load_begin = source.index('void initialiseStockVehicleCamouflageConfig()')
        load_end = source.index('\nbool setStockVehiclePartFashion(', load_begin)
        host = r'''
#include <cassert>
#include <memory>
#include <string>
#include <vector>
namespace BW { using string=std::string;template<class T> using vector=std::vector<T>; }
struct Section;
using DataSectionPtr=std::shared_ptr<Section>;
struct Section {
    BW::string text;
    BW::vector<DataSectionPtr> items;
    void openSections(const char*,BW::vector<DataSectionPtr>& out) { out=items; }
    BW::string asString() { return text; }
};
struct Config { BW::string value() { return "scripts_config.xml"; } } s_scriptsConfigXML;
namespace BWResource {
DataSectionPtr section;
unsigned reads=0;
DataSectionPtr openSection(const BW::string& path) {
    assert(path=="scripts_config.xml/camouflage/excludeMaterials");++reads;return section;
}
}
BW::vector<BW::string> camouflageExclusions;
bool camouflageExclusionsLoaded=false;
void initialiseStockVehicleCamouflageConfig();
'''
        cases = r'''
int main() {
    BWResource::section=std::make_shared<Section>();
    for(const char* name:{"*inside","tank_bottom_mat"}) {
        auto item=std::make_shared<Section>();item->text=name;
        BWResource::section->items.push_back(item);
    }
    initialiseStockVehicleCamouflageConfig();
    assert(BWResource::reads==1);
    assert(excludedFromCamouflage("gun_inside_mat"));
    assert(excludedFromCamouflage("tank_bottom_mat"));
    assert(!excludedFromCamouflage("tank_bottom_mat_extra"));
    assert(!excludedFromCamouflage("outside_mat"));
    assert(BWResource::reads==1);
    BWResource::section.reset();
    initialiseStockVehicleCamouflageConfig();
    assert(BWResource::reads==2 && camouflageExclusions.empty());
    assert(!excludedFromCamouflage("gun_inside_mat"));
}
'''
        with tempfile.TemporaryDirectory(prefix='stock-camo-init-') as directory:
            compile_run(directory, host + source[begin:end] +
                        source[load_begin:load_end] + cases)


if __name__ == '__main__':
    unittest.main()
