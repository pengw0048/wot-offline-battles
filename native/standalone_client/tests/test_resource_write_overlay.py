"""Exercise patched public-engine write routing and atomic FXO publication.

Set WOT_BIGWORLD_SOURCE to a disposable public-engine source tree. This host
compiles the patched methods with filesystem/failure-injection hosts; it does
not establish Windows rendering or native ZIP/Scaleform acceptance.
"""
from pathlib import Path
import importlib.util
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'resource_overlay_patch', ROOT / 'bigworld/patch_resource_write_overlay.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)

HOST = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <string>
#include <vector>
namespace BW {using string=std::string;using StringRef=std::string;template<class T>using vector=std::vector<T>;}
using LONG=long;using DWORD=unsigned long;
DWORD GetCurrentProcessId(){return 42;}LONG InterlockedIncrement(LONG* value){return ++*value;}
#define bw_snprintf std::snprintf
#define BW_GUARD
#define ERROR_MSG(...) ++errors
int errors=0;
struct ReadWriteLock{struct ReadGuard{explicit ReadGuard(ReadWriteLock&) {}};};
struct Binary{std::string bytes;int len()const{return static_cast<int>(bytes.size());}const char* cdata()const{return bytes.data();}};
using BinaryPtr=std::shared_ptr<Binary>;
BinaryPtr binary(const std::string& s){return std::make_shared<Binary>(Binary{s});}
bool failWrite=false,failFlush=false,failClose=false;
std::size_t cacheWrite(const void* data,std::size_t size,std::size_t count,FILE* file){return std::fwrite(data,size,failWrite&&count?count-1:count,file);}
int cacheFlush(FILE* file){int result=std::fflush(file);return failFlush?EOF:result;}
int cacheClose(FILE* file){int result=std::fclose(file);return failClose?EOF:result;}
struct FileSystem{
 enum FileType{FT_NOT_FOUND,FT_FILE,FT_DIRECTORY};
 std::filesystem::path root;int mutations=0,reads=0;bool reject=false,rejectMove=false;
 FileType getFileType(const std::string& path){auto p=root/path;if(!std::filesystem::exists(p))return FT_NOT_FOUND;return std::filesystem::is_directory(p)?FT_DIRECTORY:FT_FILE;}
 BinaryPtr readFile(const std::string& path){++reads;std::ifstream f(root/path,std::ios::binary);if(!f)return {};return binary({std::istreambuf_iterator<char>(f),{}});}
 bool makeDirectory(const std::string& path){++mutations;if(reject)return false;return std::filesystem::create_directory(root/path);}
 bool writeFile(const std::string& path,BinaryPtr data,bool){++mutations;if(reject)return false;std::ofstream f(root/path,std::ios::binary);if(!f)return false;f<<data->bytes;return bool(f);}
 bool writeFileIfExists(const std::string& p,BinaryPtr data,bool binary){return getFileType(p)!=FT_NOT_FOUND&&writeFile(p,data,binary);}
 bool moveFileOrDirectory(const std::string& a,const std::string& b){++mutations;if(reject||rejectMove)return false;std::error_code e;std::filesystem::rename(root/a,root/b,e);return !e;}
 bool copyFileOrDirectory(const std::string& a,const std::string& b){++mutations;if(reject)return false;std::error_code e;return std::filesystem::copy_file(root/a,root/b,std::filesystem::copy_options::overwrite_existing,e);}
 bool eraseFileOrDirectory(const std::string& p){++mutations;if(reject)return false;std::error_code e;return std::filesystem::remove(root/p,e);}
 FILE* posixFileOpen(const std::string& p,const char* mode){if(strpbrk(mode,"wa+")){++mutations;if(reject)return nullptr;}else ++reads;return std::fopen((root/p).c_str(),mode);}
};
using FileSystemPtr=std::shared_ptr<FileSystem>;
struct MultiFileSystem{
 using FileSystemVector=std::vector<FileSystemPtr>;
 FileSystemVector baseFileSystems_;ReadWriteLock baseFileSystemsLock_;
 using FileType=FileSystem::FileType;static constexpr FileType FT_NOT_FOUND=FileSystem::FT_NOT_FOUND;
 void addModifiedDirectory(const std::string&){}
 FileType getFileType(const std::string& p){for(auto& f:baseFileSystems_){auto t=f->getFileType(p);if(t!=FT_NOT_FOUND)return t;}return FT_NOT_FOUND;}
 BinaryPtr readFile(const std::string& p){for(auto& f:baseFileSystems_){auto b=f->readFile(p);if(b)return b;}return {};}
 bool makeDirectory(const std::string&);bool writeFile(const std::string&,BinaryPtr,bool);
 bool writeFileIfExists(const std::string&,BinaryPtr,bool);
 bool moveFileOrDirectory(const std::string&,const std::string&);bool copyFileOrDirectory(const std::string&,const std::string&);
 bool eraseFileOrDirectory(const std::string&);FILE* posixFileOpen(const std::string&,const char*);
};
struct BWResource{
 static MultiFileSystem* files;static int purges;
 static BWResource& instance(){static BWResource r;return r;}MultiFileSystem* fileSystem(){return files;}
 static std::string getFilePath(const std::string& p){return std::filesystem::path(p).parent_path().string();}
 static bool ensurePathExists(const std::string& p){std::filesystem::create_directories(files->baseFileSystems_.front()->root/std::filesystem::path(p).parent_path());return true;}
 void purge(const std::string&,bool){++purges;}
};
MultiFileSystem* BWResource::files=nullptr;int BWResource::purges=0;
'''
CASES = r'''
int main(int argc,char** argv){
 assert(argc==2);std::filesystem::path root=argv[1];
 MultiFileSystem fs;auto overlay=std::make_shared<FileSystem>();overlay->root=root/"overlay";
 auto original=std::make_shared<FileSystem>();original->root=root/"original";
 fs.baseFileSystems_={overlay,original};BWResource::files=&fs;
 std::filesystem::create_directories(overlay->root);std::filesystem::create_directories(original->root/"shaders/x");
 {std::ofstream f(original->root/"shaders/x/a.1.fxo");f<<"ORIGINAL";}
 {std::ofstream f(original->root/"source.pkg");f<<"UNCHANGED PACKAGE";}
 const std::string name="shaders/x/a.1.fxo";
 assert(fs.readFile(name)->bytes=="ORIGINAL");
 FILE* read=fs.posixFileOpen(name,"rb");assert(read);std::fclose(read);
 const int readCount=original->reads;
 overlay->reject=true;
 assert(!fs.writeFile(name,binary("BAD"),true));assert(!fs.writeFileIfExists(name,binary("BAD"),true));
 assert(!fs.makeDirectory("new"));assert(!fs.moveFileOrDirectory(name,"changed"));
 assert(!fs.copyFileOrDirectory(name,"changed"));assert(!fs.eraseFileOrDirectory(name));
 for(const char* mode:{"wb","a+b","r+"})assert(fs.posixFileOpen(name,mode)==nullptr);
 assert(original->mutations==0&&original->reads==readCount);
 assert(!saveCompiledEffectCache(name,binary("BAD")));
 overlay->reject=false;
 assert(fs.readFile(name)->bytes=="ORIGINAL");
 for(int existing=0;existing<2;++existing){
  if(existing)assert(fs.writeFile(name,binary("OLD CACHE"),true));
  const std::string expected=existing?"OLD CACHE":"ORIGINAL";
  for(int failure=0;failure<4;++failure){
   failWrite=failure==0;failFlush=failure==1;failClose=failure==2;overlay->rejectMove=failure==3;
   assert(!saveCompiledEffectCache(name,binary("INCOMPLETE CACHE")));
   assert(fs.readFile(name)->bytes==expected);
   for(auto& item:std::filesystem::recursive_directory_iterator(overlay->root))assert(item.path().filename().string().find(".tmp.")==std::string::npos);
  }
 }
 failWrite=failFlush=failClose=overlay->rejectMove=false;
 assert(saveCompiledEffectCache(name,binary("COMPLETE CACHE")));
 assert(fs.readFile(name)->bytes=="COMPLETE CACHE"&&BWResource::purges==1);
 assert(saveCompiledEffectCache("terrain/deep/another.7.fxo",binary("SECOND CACHE")));
 assert(fs.readFile("terrain/deep/another.7.fxo")->bytes=="SECOND CACHE");
 assert(original->readFile(name)->bytes=="ORIGINAL");
 assert(original->readFile("source.pkg")->bytes=="UNCHANGED PACKAGE"&&original->mutations==0);
 ZipFileSystem archive;archive.mountedReadOnly_=true;ZipFileSystem child;child.parentZip_=&archive;
 for(ZipFileSystem* f:{&archive,&child}){
  assert(!f->writeFile("new",binary("bad"),true));assert(!f->writeFileIfExists("old",binary("bad"),true));
  assert(!f->writeToStructure("new",binary("bad")));assert(!f->eraseFileOrDirectory("old"));
  assert(!f->createZip());f->clear("dir");assert(!f->posixFileOpen("old","r+"));assert(f->mutations==0);
 }
 ZipFileSystem detached;assert(detached.writeToStructure("effect",binary("new")));assert(detached.mutations==1);
 std::puts("Private overlay routing, immutable archive guards and atomic cache failure tests passed");
}
'''


def extract_function(source, signature):
    opening, end = PATCH.function_span(source, signature)
    start = source.index(signature)
    return source[start:end]


class ResourceWriteOverlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value = os.environ.get('WOT_BIGWORLD_SOURCE')
        if not value:
            raise unittest.SkipTest('WOT_BIGWORLD_SOURCE must name a disposable public-engine tree')
        cls.engine = Path(value)
        PATCH.patch(cls.engine)

    def test_patch_is_idempotent_and_cache_has_no_source_parent(self):
        names = ('lib/resmgr/multi_file_system.cpp', 'lib/resmgr/zip_file_system.cpp',
                 'lib/resmgr/zip_file_system.hpp', 'lib/moo/effect_compiler.cpp')
        before = [(self.engine / name).read_bytes() for name in names]
        PATCH.patch(self.engine)
        self.assertEqual(before, [(self.engine / name).read_bytes() for name in names])
        compiler = (self.engine / names[-1]).read_text()
        body = extract_function(compiler, 'BinaryPtr EffectCompiler::compile(')
        self.assertNotIn('eraseFileOrDirectory', body)
        self.assertNotIn('parentSection', body)
        self.assertNotIn('convertToZip(', body)
        self.assertIn('new ZipSection( fxoName,', body)
        self.assertIn('Could not save recompiled ', body)
        archive = (self.engine / 'lib/resmgr/zip_file_system.cpp').read_text()
        ctor = extract_function(archive, 'ZipFileSystem::ZipFileSystem( const BW::StringRef& zipFile ) :')
        self.assertLess(ctor.index('mountedReadOnly_ = true;'), ctor.index('FileHandleHolder'))

    def test_actual_routing_methods_and_cache_failure_boundaries(self):
        multi = (self.engine / 'lib/resmgr/multi_file_system.cpp').read_text()
        methods = []
        for signature in ('bool MultiFileSystem::makeDirectory(', 'bool MultiFileSystem::writeFile(',
                          'bool MultiFileSystem::writeFileIfExists(', 'bool MultiFileSystem::moveFileOrDirectory(',
                          'bool MultiFileSystem::copyFileOrDirectory(', 'bool MultiFileSystem::eraseFileOrDirectory(',
                          'FILE * MultiFileSystem::posixFileOpen('):
            methods.append(extract_function(multi, signature))
        source = (self.engine / 'lib/resmgr/zip_file_system.cpp').read_text()
        header = (self.engine / 'lib/resmgr/zip_file_system.hpp').read_text()
        guard_host = ['struct ZipFileSystem { bool mountedReadOnly_=false;ZipFileSystem* parentZip_=nullptr;int mutations=0;std::string path_;']
        guard_host.append(extract_function(header, 'bool mountedReadOnly() const'))
        for signature, declaration, result in (
                ('bool ZipFileSystem::writeFile(', 'bool writeFile(const std::string&,BinaryPtr,bool)', 'true'),
                ('bool ZipFileSystem::writeFileIfExists(', 'bool writeFileIfExists(const std::string&,BinaryPtr,bool)', 'true'),
                ('bool ZipFileSystem::writeToStructure(', 'bool writeToStructure(const std::string&,BinaryPtr)', 'true'),
                ('bool ZipFileSystem::eraseFileOrDirectory(', 'bool eraseFileOrDirectory(const std::string&)', 'true'),
                ('BinaryPtr ZipFileSystem::createZip(', 'BinaryPtr createZip()', '{}'),
                ('void ZipFileSystem::clear(const', 'void clear(const std::string&)', ''),
                ('FILE * ZipFileSystem::posixFileOpen(', 'FILE* posixFileOpen(const std::string&,const char* mode)', 'nullptr')):
            function = extract_function(source, signature)
            opening = function.index('{')
            # Compile the actual first guard before the mutation sentinel.
            self.assertTrue(function[opening + 1:].lstrip().startswith('if (this->mountedReadOnly()'))
            guard_start = function.index('if (this->mountedReadOnly()')
            begin, end = PATCH.function_span(function, 'if (this->mountedReadOnly()')
            guard_host.append(declaration + '{' + function[guard_start:end] + '\n++mutations;return ' + result + ';}')
        guard_host.append('};')
        program = HOST + '\n'.join(methods) + '\n'.join(guard_host)
        program += '\n#define fwrite cacheWrite\n#define fflush cacheFlush\n#define fclose cacheClose\n'
        program += PATCH.CACHE_SAVE
        program += '\n#undef fwrite\n#undef fflush\n#undef fclose\n' + CASES
        with tempfile.TemporaryDirectory(prefix='resource-overlay-probe-') as temporary:
            path = Path(temporary)
            (path / 'probe.cpp').write_text(program)
            subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', str(path / 'probe.cpp'),
                            '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe'), str(path)], check=True)


if __name__ == '__main__':
    unittest.main()
