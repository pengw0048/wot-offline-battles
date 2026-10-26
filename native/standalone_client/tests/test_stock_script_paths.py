"""Exercise the transformed public startup path producer in its original order."""
from pathlib import Path
import importlib.util
import os
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'stock_script_paths', ROOT / 'bigworld/patch_stock_script_paths.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


class ScriptPathsTest(unittest.TestCase):
    def test_actual_startup_publishes_client_common_before_script_init(self):
        archive = Path(os.environ.get('WOT_BIGWORLD_ARCHIVE',
                                     '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.exists():
            self.skipTest('Pinned public-source archive unavailable')
        with tempfile.TemporaryDirectory(prefix='stock-script-paths-') as temp:
            root = Path(temp)
            path = root / 'client/script_bigworld.cpp'
            path.parent.mkdir()
            with tarfile.open(archive) as source:
                name = next(n for n in source.getnames()
                            if n.endswith('/client/script_bigworld.cpp'))
                path.write_bytes(source.extractfile(name).read())
            PATCH.patch(root)
            first = path.read_bytes()
            PATCH.patch(root)
            self.assertEqual(path.read_bytes(), first)
            source = first.decode()
            start = source.index('\tPyImportPaths paths;')
            end = source.index('\n\tif (!MaterialKinds::init())', start)
            body = source[start:end]
            host = r'''
#include <cassert>
#include <string>
#include <vector>
namespace EntityDef { namespace Constants {
const char* entitiesClientPath() { return "scripts/client"; }
const char* userDataObjectsClientPath() { return "scripts/client"; }
} }
struct PyImportPaths {
    std::vector<std::string> entries;
    void addResPath(const char* path) { entries.push_back(path); }
};
namespace Script {
bool accepted=true;
bool init(const PyImportPaths& paths,const char* component) {
    assert(std::string(component)=="client");
    assert(paths.entries==std::vector<std::string>({
        "scripts/client","scripts/client","scripts/client_common"}));
    return accepted;
}
}
bool startup() {
''' + body + r'''
    return true;
}
int main() {
    assert(startup());
    Script::accepted=false;
    assert(!startup());
}
'''
            probe = root / 'probe.cpp'
            probe.write_text(host)
            subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++14',
                            str(probe), '-o', str(root / 'probe')], check=True)
            subprocess.run([str(root / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
