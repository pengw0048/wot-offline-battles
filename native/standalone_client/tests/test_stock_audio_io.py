"""Compile the actual mounted-resource adapter against guarded filesystem/OS hosts.

This exercises independent handle ownership and read bounds. The POSIX host
models Win32 file operations; it is not Windows/Wwise runtime acceptance.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST = r'''
#define _LIBCPP_DISABLE_DEPRECATION_WARNINGS
#include <algorithm>
#include <cassert>
#include <codecvt>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <fcntl.h>
#include <limits>
#include <locale>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
#define ERROR_MSG(...) std::fprintf(stderr,__VA_ARGS__)
namespace BW {using string=std::string;using wstring=std::wstring;}
std::wstring_convert<std::codecvt_utf8<wchar_t>> converter;
bool bw_utf8tow(const std::string& s,std::wstring& w){w=converter.from_bytes(s);return true;}
bool bw_wtoutf8(const wchar_t* w,std::string& s){s=converter.to_bytes(w);return true;}
using AkUInt32=std::uint32_t;using AkFileID=AkUInt32;using AkDeviceID=int;using AKRESULT=int;
using LONGLONG=std::int64_t;using DWORD=std::uint32_t;using AkInt64=std::int64_t;
using AkOSChar=wchar_t;
struct LARGE_INTEGER{LONGLONG QuadPart;};
struct NativeFile{int fd;};using HANDLE=NativeFile*;using AkFileHandle=HANDLE;
#define INVALID_HANDLE_VALUE reinterpret_cast<NativeFile*>(-1)
constexpr int GENERIC_READ=1,FILE_SHARE_READ=2,OPEN_EXISTING=3,FILE_ATTRIBUTE_NORMAL=4,FILE_BEGIN=0;
static int handles=0,opened=0,closed=0;static bool shortRead=false;
HANDLE CreateFileW(const wchar_t* name,int,int,void*,int,int,void*){
 int fd=open(converter.to_bytes(name).c_str(),O_RDONLY);
 if(fd<0)return INVALID_HANDLE_VALUE;
 ++handles;++opened;return new NativeFile{fd};
}
bool CloseHandle(HANDLE h){assert(h&&h!=INVALID_HANDLE_VALUE);close(h->fd);delete h;--handles;++closed;return true;}
bool GetFileSizeEx(HANDLE h,LARGE_INTEGER* size){size->QuadPart=lseek(h->fd,0,SEEK_END);return size->QuadPart>=0;}
bool SetFilePointerEx(HANDLE h,LARGE_INTEGER pos,void*,int){return lseek(h->fd,pos.QuadPart,SEEK_SET)==pos.QuadPart;}
bool ReadFile(HANDLE h,void* out,DWORD count,DWORD* received,void*){
 auto n=read(h->fd,out,shortRead&&count?count-1:count);*received=n<0?0:static_cast<DWORD>(n);return n>=0;
}
constexpr int AK_INVALID_DEVICE_ID=-1,AK_SCHEDULER_BLOCKING=1,AK_MAX_PATH=1024,AK_MONITOR_DEVICENAME_MAXLENGTH=80;
constexpr int AK_Fail=0,AK_Success=1,AK_InvalidParameter=2,AK_PathNotFound=3,AK_NotImplemented=4,AK_FileNotFound=5,AK_InvalidFile=6;
#define AKTEXT(x) L##x
enum AkOpenMode{AK_OpenModeRead,AK_OpenModeWrite};
struct AkFileSystemFlags{};struct AkIoHeuristics{};
struct AkDeviceSettings{int uSchedulerTypeFlags;};
struct AkFileDesc{HANDLE hFile=nullptr;AkInt64 iFileSize=0;AkUInt32 uSector=0;int deviceID=0;void* pCustomParam=nullptr;AkUInt32 uCustomParamSize=0;};
struct AkIOTransferInfo{std::uint64_t uFilePosition;AkUInt32 uBufferSize,uRequestedSize;};
struct AkDeviceDesc{int deviceID;bool bCanRead,bCanWrite;wchar_t szDeviceName[80];AkUInt32 uStringSize;};
namespace AKPLATFORM{void SafeStrCpy(wchar_t* out,const wchar_t* in,std::size_t n){wcsncpy(out,in,n);out[n-1]=0;}}
namespace AK {namespace StreamMgr{
 static void* resolver=nullptr;
 void SetFileLocationResolver(void* p){resolver=p;}
 void* GetFileLocationResolver(){return resolver;}
 int CreateDevice(const AkDeviceSettings& s,void*){assert(s.uSchedulerTypeFlags==AK_SCHEDULER_BLOCKING);return 7;}
 void DestroyDevice(int id){assert(id==7);assert(handles==0);}
}}
static int retainedArchiveLocks=0;
struct GuardedStreamer{
 GuardedStreamer(){++retainedArchiveLocks;}
 ~GuardedStreamer(){--retainedArchiveLocks;}
 std::size_t read(std::size_t,void*){return 0;}bool setOffset(std::size_t){return true;}
};
using FileStreamerPtr=std::shared_ptr<GuardedStreamer>;
class IFileSystem{
public:
 enum FileType{FT_NOT_FOUND,FT_FILE,FT_ARCHIVE,FT_DIRECTORY};
 struct FileInfo{std::uint64_t size;};
 struct FileDataLocation{std::string hostResourceID;std::uint64_t offset;};
 struct Entry{std::uint64_t offset,size;bool locatable=true;};
 std::string host;std::map<std::string,Entry> entries;
 FileType getFileType(const std::string& p,FileInfo* info=nullptr){
  if(retainedArchiveLocks)throw std::runtime_error("Archive mutex reentered while a streamer remains alive");
  if(p=="audioww")return FT_DIRECTORY;
  auto found=entries.find(p);if(found==entries.end())return FT_NOT_FOUND;
  if(info)info->size=found->second.size;return FT_FILE;
 }
 bool locateFileData(const std::string& p,FileDataLocation* out){
  assert(retainedArchiveLocks==0);auto found=entries.find(p);
  if(found==entries.end()||!found->second.locatable)return false;
  out->hostResourceID=host;out->offset=found->second.offset;return true;
 }
 FileStreamerPtr streamFile(const std::string&){return std::make_shared<GuardedStreamer>();}
};
using FileSystemPtr=IFileSystem*;static IFileSystem mounted;
struct BWResource{static BWResource& instance(){static BWResource r;return r;}IFileSystem* fileSystem(){return &mounted;}};
namespace wot{
 std::vector<std::uint8_t> decode_wot_1513_wwise_bank_header(const std::vector<std::uint8_t>& in){
  if(in.size()!=12||in[8]!=0xaa)throw std::runtime_error("Invalid test bank");
  auto out=in;out[8]=0x55;return out;
 }
}
class StockAudioResourceIO{
public:
 StockAudioResourceIO();~StockAudioResourceIO();
 AKRESULT Init(const AkDeviceSettings&);void Term();AKRESULT setResourcePath(const std::string&);
 AKRESULT Open(const wchar_t*,AkOpenMode,AkFileSystemFlags*,bool&,AkFileDesc&);
 AKRESULT Open(AkUInt32,AkOpenMode,AkFileSystemFlags*,bool&,AkFileDesc&);
 AKRESULT Read(AkFileDesc&,const AkIoHeuristics&,void*,AkIOTransferInfo&);
 AKRESULT Write(AkFileDesc&,const AkIoHeuristics&,void*,AkIOTransferInfo&);
 AKRESULT Close(AkFileDesc&);AkUInt32 GetBlockSize(AkFileDesc&);void GetDeviceDesc(AkDeviceDesc&);AkUInt32 GetDeviceData();
 AKRESULT SetBasePath(const wchar_t*){return AK_PathNotFound;}
 AKRESULT GetFullFilePath(const wchar_t* name,AkFileSystemFlags*,AkOpenMode,wchar_t* out){wcscpy(out,name);return AK_Success;}
 AKRESULT GetFullFilePath(AkUInt32,AkFileSystemFlags*,AkOpenMode,wchar_t* out){wcscpy(out,L"ambient.pck");return AK_Success;}
 int m_deviceID;
private:
 AKRESULT openResource(const wchar_t*,AkOpenMode,AkFileDesc&);FileSystemPtr fileSystem_=nullptr;
};
'''
CASES = r'''
int main(int argc,char** argv){
 assert(argc==2);mounted.host=argv[1];
 mounted.entries={{"ambient.pck",{11,8}},{"combat.pck",{39,8}},{"Init.bnk",{80,12}},
  {"invalid.bnk",{100,12}}, {"bad-magic.bnk",{11,8}},
  {"large.pck",{(std::uint64_t(1)<<32)+25,8}}, {"outside.pck",{(std::uint64_t(1)<<32)+31,8}},
  {"overflow.pck",{std::numeric_limits<std::uint64_t>::max(),8}}, {"compressed.pck",{11,8,false}}};
 StockAudioResourceIO io;assert(io.Init({0})==AK_Success);
 assert(io.setResourcePath("audioww")==AK_Success);
 bool synchronous=false;AkFileDesc first,second,bank,large;
 assert(io.Open(L"ambient.pck",AK_OpenModeRead,nullptr,synchronous,first)==AK_Success&&synchronous);
 assert(io.Open(L"combat.pck",AK_OpenModeRead,nullptr,synchronous,second)==AK_Success);
 assert(handles==2&&retainedArchiveLocks==0);
 auto check=[&](AkFileDesc& desc,std::uint64_t offset,const char* expected,AkUInt32 n){
  char bytes[20]={};AkIOTransferInfo read{offset,sizeof(bytes),n};
  assert(io.Read(desc,{},bytes,read)==AK_Success);assert(std::memcmp(bytes,expected,n)==0);
 };
 // Separate file cursors and per-handle serialization support Wwise's I/O thread.
 std::thread worker([&]{for(int n=0;n<100;++n){check(first,1,"MBIENT!",7);check(second,0,"COMBAT!!",8);}});
 for(int n=0;n<100;++n)check(first,0,"AMBIENT!",8);
 worker.join();
 char output[20]={};AkIOTransferInfo beyond{7,20,2},overflow{std::numeric_limits<std::uint64_t>::max(),20,1},small{0,1,2};
 assert(io.Read(first,{},output,beyond)==AK_Fail);assert(io.Read(first,{},output,overflow)==AK_Fail);
 assert(io.Read(first,{},output,small)==AK_Fail);
 shortRead=true;AkIOTransferInfo normal{0,20,8};assert(io.Read(first,{},output,normal)==AK_Fail);shortRead=false;
 assert(io.Open(L"large.pck",AK_OpenModeRead,nullptr,synchronous,large)==AK_Success);check(large,0,"OVER4GB!",8);
 assert(io.Open(L"Init.bnk",AK_OpenModeRead,nullptr,synchronous,bank)==AK_Success);
 const char decoded[]={char(0x55),2,3,4};check(bank,8,decoded,4);
 for(const wchar_t* name:{L"outside.pck",L"overflow.pck",L"compressed.pck",L"missing.pck",L"invalid.bnk",L"bad-magic.bnk"}){
  const int before=handles;AkFileDesc failed;
  assert(io.Open(name,AK_OpenModeRead,nullptr,synchronous,failed)!=AK_Success);
  assert(handles==before&&failed.hFile==nullptr);
 }
 // Close may run on a different SDK thread; no archive lock changes owners.
 std::thread closeWorker([&]{assert(io.Close(first)==AK_Success);});closeWorker.join();
 assert(first.hFile==nullptr);assert(io.Read(first,{},output,normal)==AK_Fail);
 assert(io.Close(first)==AK_Success);assert(io.Close(second)==AK_Success);
 assert(io.Close(bank)==AK_Success);assert(io.Close(large)==AK_Success);
 assert(handles==0&&opened==closed);io.Term();io.Term();
 std::puts("Mounted audio resource ownership and read bounds passed");
}
'''


class StockAudioIOTest(unittest.TestCase):
    def test_mounted_resources_have_independent_bounded_handles(self):
        source = (ROOT / 'bigworld/stock_audio_io.cpp').read_text()
        source = '\n'.join(line for line in source.splitlines()
                           if not line.startswith('#include'))
        with tempfile.TemporaryDirectory(prefix='stock-audio-io-') as directory:
            path = Path(directory)
            archive = path / '声音资源.pkg'
            with archive.open('wb') as file:
                for offset, data in ((11, b'AMBIENT!'), (39, b'COMBAT!!'),
                                     (80, b'BKHD\x04\0\0\0\xaa\x02\x03\x04'),
                                     (100, b'BKHD\x04\0\0\0\0\x02\x03\x04'),
                                     ((1 << 32) + 25, b'OVER4GB!')):
                    file.seek(offset)
                    file.write(data)
            (path / 'probe.cpp').write_text(HOST + source + CASES)
            subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-pthread',
                            str(path / 'probe.cpp'), '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe'), str(archive)], check=True)


if __name__ == '__main__':
    unittest.main()
