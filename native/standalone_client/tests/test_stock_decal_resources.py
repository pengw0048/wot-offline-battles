"""Exercise #1513 trace resource ownership and public renderer patch anchors."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'stock_decal_patch', ROOT / 'bigworld/patch_stock_decals.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


class StockDecalResourcesTest(unittest.TestCase):
    def test_native_texture_ownership_ids_channels_and_failures(self):
        source = r'''
#define WOT_STOCK_DECAL_RESOURCES_PORTABLE
#include "stock_decal_resources.hpp"
#include <cassert>
#include <cmath>
#include <memory>
#include <vector>
struct Resource { static int live; Resource(){++live;} ~Resource(){--live;} };
int Resource::live=0;
using Ptr=std::shared_ptr<Resource>;
int main(){
    std::uint32_t next=0;
    std::vector<std::string> loaded;
    auto load=[&](const std::string& name)->Ptr{
        loaded.push_back(name);
        return name.empty()||name=="missing"?Ptr():std::make_shared<Resource>();
    };
    {
        wot::StockTraceTextureRegistry<Ptr> owner(next);
        assert(owner.add("dust.dds",load)==0);
        assert(owner.textures().count(0x60000000u)==1);
        assert(Resource::live==1);
        assert(owner.add("dust.dds",load)==1); // IDs do not alias by filename.
        assert(owner.add("left.dds; \tright.dds",load)==2);
        assert(owner.textures().count(0x20000002u)==1);
        assert(owner.textures().count(0x40000002u)==1);
        assert(owner.textures().count(0x60000002u)==0);
        assert(loaded[2]=="left.dds"&&loaded[3]=="right.dds");
        const auto size=owner.textures().size();
        assert(owner.add("missing",load)==-1);
        assert(owner.add("left.dds;missing",load)==-1);
        assert(owner.add("missing;right.dds",load)==-1);
        assert(owner.add("",load)==-1);
        assert(owner.textures().size()==size&&next==3&&Resource::live==4);
        // Only spaces/tabs after the first semicolon are skipped. Leading
        // whitespace in the first path and a second semicolon are preserved.
        assert(owner.add(" left.dds ;\nright.dds;third.dds",load)==3);
        assert(loaded[loaded.size()-2]==" left.dds ");
        assert(loaded.back()=="\nright.dds;third.dds");
        const auto calls=loaded.size();
        bool rejected=false;
        try { owner.add("left.dds; \t",load); }
        catch(const std::out_of_range&) { rejected=true; }
        assert(rejected&&loaded.size()==calls&&next==4);
    }
    assert(Resource::live==0);
    { // Renderer recreation releases resources but never recycles IDs.
        wot::StockTraceTextureRegistry<Ptr> owner(next);
        assert(owner.add("fresh.dds",load)==4);
        assert(owner.textures().count(0x60000004u)==1);
    }
    assert(Resource::live==0);
    for(float degrees:{-90.f,0.f,10.f,30.f,45.f,180.f,540.f}){
        const float radians=wot::stockDamageStickerAngle(degrees);
        assert(std::abs(radians-degrees*3.14159265358979323846/180.0)<1e-6);
    }
}
'''
        with tempfile.TemporaryDirectory(prefix='stock-decals-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(source)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17',
                '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'bigworld'),
                str(path / 'probe.cpp'), '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)

    def test_patch_is_idempotent_on_pinned_public_owner(self):
        engine = Path(os.environ.get('WOT_PUBLIC_BIGWORLD',
            '/tmp/wot-bigworld-engine-14.4.1/programming/bigworld'))
        if not (engine / 'lib/moo/renderer.cpp').is_file():
            self.skipTest('Pinned public BigWorld source is unavailable')
        with tempfile.TemporaryDirectory(prefix='stock-decal-patch-') as directory:
            root = Path(directory)
            folder = root / 'lib/moo'
            folder.mkdir(parents=True)
            names = ('renderer.hpp', 'renderer.cpp', 'sticker_decal.cpp')
            for name in names:
                shutil.copyfile(engine / 'lib/moo' / name, folder / name)
            PATCH.patch(root)
            first = {name: (folder / name).read_text() for name in names}
            PATCH.patch(root)
            self.assertEqual(first,
                {name: (folder / name).read_text() for name in names})
            self.assertIn('m_stockTraceTextures.reset(new StockTraceTextureOwner());',
                first['renderer.cpp'])
            self.assertIn('m_stockTraceTextures.reset();', first['renderer.cpp'])
            self.assertIn('s_criticalAngle = 0.7853f', first['sticker_decal.cpp'])
            # A changed upstream declaration must fail loudly.
            (folder / 'renderer.hpp').write_text(first['renderer.hpp'].replace(
                'class StockTraceTextureOwner;', '').replace(
                'class SSAOSupport;', 'class UpstreamSSAO;'))
            with self.assertRaisesRegex(ValueError, 'owner declaration'):
                PATCH.patch(root)


if __name__ == '__main__':
    unittest.main()
