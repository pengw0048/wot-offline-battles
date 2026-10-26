"""Validate the native MetaParticleSystem inverse-group guard and teardown hooks."""
import importlib.util
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('patch_stock_tps', ROOT / 'bigworld/patch_stock_tps.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def extract_method(source, signature):
    begin = source.index(signature)
    brace = source.index('{', begin)
    depth = 1
    cursor = brace + 1
    while depth:
        depth += (source[cursor] == '{') - (source[cursor] == '}')
        cursor += 1
    return source[begin:cursor]


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <memory>
#include <vector>
#define BW_GUARD
using uint32=std::uint32_t;
unsigned inverseMask=0xffffu;
bool stockTPSGroupDisabled(uint32 groups){return (groups&inverseMask)!=0;}
struct System {
    static unsigned live,clones,ticks;
    System(){++live;}~System(){--live;}
    std::shared_ptr<System> clone(){++clones;return std::make_shared<System>();}
    bool tick(float dt){assert(dt==.25f);++ticks;return true;}
};
unsigned System::live=0,System::clones=0,System::ticks=0;
class MetaParticleSystem;
using MetaParticleSystemPtr=std::shared_ptr<MetaParticleSystem>;
class MetaParticleSystem {
public:
    using ParticleSystems=std::vector<std::shared_ptr<System>>;
    uint32 inverseRenderGroups_=0;
    ParticleSystems systems_;
    MetaParticleSystemPtr clone()const;
    bool tick(float);
    void addSystem(std::shared_ptr<System> system){systems_.push_back(system);}
    void removeAllSystems(){systems_.clear();}
};
@CLONE@
@TICK@
int main(){
    for(uint32 groups:{0u,1u,2u,0x10000u,0xffff0000u,0xffffffffu}){
        MetaParticleSystem owner;
        owner.inverseRenderGroups_=groups;
        owner.addSystem(std::make_shared<System>());
        inverseMask=0xffffu;
        const unsigned clones=System::clones;
        auto copied=owner.clone();
        if(groups&inverseMask){assert(!copied&&System::clones==clones);assert(!owner.tick(.25f)&&owner.systems_.empty());}
        else {assert(copied&&copied->inverseRenderGroups_==groups&&copied->systems_.size()==1);assert(owner.tick(.25f));}
    }
    assert(System::live==0);
    MetaParticleSystem owner;owner.inverseRenderGroups_=2;owner.addSystem(std::make_shared<System>());
    inverseMask=0;assert(owner.tick(.25f));
    inverseMask=2;assert(!owner.tick(.25f)&&owner.systems_.empty());
    assert(!owner.tick(.25f));assert(System::live==0);
}
'''


class StockTPSOwnerTests(unittest.TestCase):
    def test_production_guards_and_idempotent_patch(self):
        archive = Path('/tmp/wot-bigworld-engine-14.4.1.tar.gz')
        if not archive.exists():
            self.skipTest('Pristine public engine archive is required')
        with tempfile.TemporaryDirectory(prefix='wot-tps-owner-') as directory:
            work = Path(directory)
            with tarfile.open(archive) as tar:
                for suffix in ('lib/particle/meta_particle_system.cpp', 'lib/particle/meta_particle_system.hpp'):
                    member = next(member for member in tar if member.name.endswith('/' + suffix))
                    destination = work / suffix
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(tar.extractfile(member).read())
            patch.patch(work)
            source = (work / 'lib/particle/meta_particle_system.cpp').read_text()
            header = (work / 'lib/particle/meta_particle_system.hpp').read_text()
            patch.patch(work)
            self.assertEqual(source, (work / 'lib/particle/meta_particle_system.cpp').read_text())
            self.assertEqual(header, (work / 'lib/particle/meta_particle_system.hpp').read_text())
            self.assertEqual(source.count('writeUInt( "addition/inv_render_groups"'), 1)
            self.assertNotRegex(source, r'readUInt\(\s*"addition/inv_render_groups"')
            harness = HARNESS.replace('@CLONE@', extract_method(source, 'MetaParticleSystemPtr MetaParticleSystem::clone() const'))
            harness = harness.replace('@TICK@', extract_method(source, 'bool MetaParticleSystem::tick( float dTime )'))
            # Public SmartPointer accepts raw new; adapt only the harness pointer alias.
            harness = harness.replace('MetaParticleSystemPtr result = new MetaParticleSystem();',
                                      'MetaParticleSystemPtr result(new MetaParticleSystem());')
            (work / 'probe.cpp').write_text(harness)
            subprocess.run(['c++', '-std=c++17', '-fsanitize=address,undefined', '-Wall', '-Wextra', '-Werror',
                            str(work / 'probe.cpp'), '-o', str(work / 'probe')], check=True)
            subprocess.run([str(work / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
