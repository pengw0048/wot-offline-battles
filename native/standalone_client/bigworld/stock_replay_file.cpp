#if !defined(WOT_REPLAY_HOST_BUILD)
#include "pch.hpp"
#include "network/symmetric_block_cipher.hpp"
#else
#include <openssl/blowfish.h>
#endif
#if defined(snprintf)
#undef snprintf
#endif
#if defined(vsnprintf)
#undef vsnprintf
#endif
#if defined(hypot)
#undef hypot
#endif
#include "stock_replay_file.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <climits>
#include <cstdio>
#include <cstring>
#include <limits>
#include <memory>
#include <numeric>
#include <stdexcept>
#include <utility>
#include <zlib.h>
#if defined(_WIN32)
#include <windows.h>
#else
#include <unistd.h>
#endif

namespace wot { namespace {
using Bytes=std::vector<std::uint8_t>;
constexpr std::uint32_t legacyMagic=0x12343210, encryptedMagic=0x11343212;
constexpr std::uint32_t maximumExpandedSize=0x3200000;
// Exact #1513 DAT_01a0f490 initializer, after 0x6fd9da and 0x9710b5.
const std::uint8_t replayKey[]={0xde,0x72,0xbe,0xa0,0xde,0xad,0xbe,0xef,0xde,0xad,0xbe,0xef,0xde,0xad,0xbe,0xef};
bool fail(std::string& error,const char* message){error=message;return false;}
template<class T> void reserveFor(std::vector<T>& values,std::size_t required){
    if(values.capacity()>=required)return;
    const auto increment=std::max<std::size_t>(values.capacity()/2,64);
    const auto grown=values.capacity()>values.max_size()-increment?values.max_size():values.capacity()+increment;
    values.reserve(std::max(required,grown));
}
std::uint32_t u32(const std::uint8_t* p){return std::uint32_t(p[0])|(std::uint32_t(p[1])<<8)|(std::uint32_t(p[2])<<16)|(std::uint32_t(p[3])<<24);}
void put(Bytes& bytes,std::uint32_t value){for(unsigned shift=0;shift<32;shift+=8)bytes.push_back(static_cast<std::uint8_t>(value>>shift));}
struct Cursor {
    const Bytes& bytes;std::size_t offset=0;
    std::uint32_t word(){if(bytes.size()-offset<4)throw std::runtime_error("Truncated replay container header");const auto value=u32(bytes.data()+offset);offset+=4;return value;}
    std::string string(){const auto length=word();if(length>bytes.size()-offset)throw std::runtime_error("Truncated replay metadata");std::string value(reinterpret_cast<const char*>(bytes.data()+offset),length);offset+=length;return value;}
};
class Cipher {
#if defined(WOT_REPLAY_HOST_BUILD)
    BF_KEY key_;
#else
    BW::Mercury::BlockCipherPtr cipher_;
#endif
public:
    Cipher(){
#if defined(WOT_REPLAY_HOST_BUILD)
        BF_set_key(&key_,static_cast<int>(sizeof(replayKey)),replayKey);
#else
        BW::Mercury::BlockCipher::Key key(reinterpret_cast<const char*>(replayKey),sizeof(replayKey));
        cipher_=BW::Mercury::SymmetricBlockCipher::create(key);
        if(!cipher_||cipher_->blockSize()!=8||cipher_->key()!=key)throw std::runtime_error("The replay owner requires the real Blowfish block cipher");
#endif
    }
    void block(const std::uint8_t* input,std::uint8_t* output,bool encrypt){
#if defined(WOT_REPLAY_HOST_BUILD)
        BF_ecb_encrypt(input,output,&key_,encrypt?BF_ENCRYPT:BF_DECRYPT);
#else
        if(!(encrypt?cipher_->encryptBlock(input,output):cipher_->decryptBlock(input,output)))throw std::runtime_error("Replay Blowfish block operation failed");
#endif
    }
};
// This is the original plaintext-feedback chain (0xf265c0 / 0xf26700),
// whose previous block is the plaintext, rather than the ciphertext.
Bytes crypt(const Bytes& input,bool encrypt){
    if(input.size()%8)throw std::runtime_error("Replay ciphertext is not block aligned");
    Cipher cipher;Bytes output(input.size());std::array<std::uint8_t,8> previous{},block{};
    for(std::size_t offset=0;offset<input.size();offset+=8){
        if(encrypt){for(std::size_t i=0;i<8;++i)block[i]=input[offset+i]^previous[i];cipher.block(block.data(),output.data()+offset,true);
            std::copy_n(input.data()+offset,8,previous.data());}
        else{cipher.block(input.data()+offset,block.data(),false);for(std::size_t i=0;i<8;++i)output[offset+i]=block[i]^previous[i];
            std::copy_n(output.data()+offset,8,previous.data());}
    }
    return output;
}
Bytes expand(const Bytes& packed,std::uint32_t expected){
    if(expected>maximumExpandedSize)throw std::runtime_error("Replay expanded size exceeds the #1513 limit");
    if(packed.empty()||packed.size()>std::numeric_limits<uInt>::max())throw std::runtime_error("Invalid replay zlib input length");
    Bytes output(std::max<std::size_t>(expected,1));z_stream stream{};
    stream.next_in=const_cast<Bytef*>(packed.data());stream.avail_in=static_cast<uInt>(packed.size());stream.next_out=output.data();stream.avail_out=static_cast<uInt>(output.size());
    if(inflateInit(&stream)!=Z_OK)throw std::runtime_error("Cannot initialise replay zlib decoder");
    const int result=inflate(&stream,Z_FINISH);const auto inputSize=stream.total_in,outputSize=stream.total_out;inflateEnd(&stream);
    if(result!=Z_STREAM_END||outputSize!=expected||inputSize!=packed.size())throw std::runtime_error("Replay zlib stream or declared size is invalid");
    output.resize(expected);return output;
}
Bytes compress(const Bytes& plain){
    if(plain.size()>maximumExpandedSize)throw std::runtime_error("Replay expanded size exceeds the #1513 limit");
    uLongf size=compressBound(static_cast<uLong>(plain.size()));Bytes output(size);const std::uint8_t empty=0;
    if(compress2(output.data(),&size,plain.empty()?&empty:plain.data(),static_cast<uLong>(plain.size()),9)!=Z_OK)throw std::runtime_error("Replay zlib compression failed");
    output.resize(size);return output;
}
#if defined(_WIN32)
std::wstring wide(const std::string& path){
    if(path.empty()||path.find('\0')!=std::string::npos||path.size()>INT_MAX)throw std::runtime_error("Invalid UTF-8 replay path");
    const int size=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,path.data(),static_cast<int>(path.size()),NULL,0);
    if(!size)throw std::runtime_error("Invalid UTF-8 replay path");
    std::wstring value(static_cast<std::size_t>(size),L'\0');
    if(MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,path.data(),static_cast<int>(path.size()),&value[0],size)!=size)throw std::runtime_error("Cannot convert replay path");
    return value;
}
struct Handle {HANDLE value=INVALID_HANDLE_VALUE;~Handle(){if(value!=INVALID_HANDLE_VALUE)CloseHandle(value);}bool close(){if(value==INVALID_HANDLE_VALUE)return true;const HANDLE handle=value;value=INVALID_HANDLE_VALUE;return CloseHandle(handle)!=0;}};
#endif
Bytes readFile(const std::string& path){
#if defined(_WIN32)
    Handle handle;handle.value=CreateFileW(wide(path).c_str(),GENERIC_READ,FILE_SHARE_READ,NULL,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,NULL);
    if(handle.value==INVALID_HANDLE_VALUE)throw std::runtime_error("Cannot open replay file");
    LARGE_INTEGER size{};
    if(!GetFileSizeEx(handle.value,&size)||size.QuadPart<0||static_cast<unsigned long long>(size.QuadPart)>UINT32_MAX)throw std::runtime_error("Invalid replay file length");
    Bytes bytes(static_cast<std::size_t>(size.QuadPart));std::size_t offset=0;
    while(offset<bytes.size()){DWORD read=0;const DWORD amount=static_cast<DWORD>(std::min<std::size_t>(bytes.size()-offset,0x100000));
        if(!ReadFile(handle.value,bytes.data()+offset,amount,&read,NULL)||!read)throw std::runtime_error("Replay file read failed");
        offset+=read;}
#else
    if(path.empty()||path.find('\0')!=std::string::npos)throw std::runtime_error("Invalid replay path");
    std::unique_ptr<FILE,decltype(&std::fclose)> file(std::fopen(path.c_str(),"rb"),&std::fclose);if(!file)throw std::runtime_error("Cannot open replay file");
    if(fseeko(file.get(),0,SEEK_END)!=0)throw std::runtime_error("Cannot measure replay file");
    const auto size=ftello(file.get());
    if(size<0||static_cast<unsigned long long>(size)>UINT32_MAX||fseeko(file.get(),0,SEEK_SET)!=0)throw std::runtime_error("Invalid replay file length");
    Bytes bytes(static_cast<std::size_t>(size));std::size_t offset=0;
    while(offset<bytes.size()){const auto read=std::fread(bytes.data()+offset,1,bytes.size()-offset,file.get());if(!read)throw std::runtime_error("Replay file read failed");offset+=read;}
#endif
    return bytes;
}
void publish(const std::string& path,const Bytes& bytes){
#if defined(_WIN32)
    const std::wstring destination=wide(path);std::wstring temporary;Handle handle;
    for(unsigned attempt=0;attempt<128;++attempt){temporary=destination+L".tmp."+std::to_wstring(GetCurrentProcessId())+L"."+std::to_wstring(attempt);
        handle.value=CreateFileW(temporary.c_str(),GENERIC_WRITE,0,NULL,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,NULL);if(handle.value!=INVALID_HANDLE_VALUE)break;
        if(GetLastError()!=ERROR_FILE_EXISTS)throw std::runtime_error("Cannot create private replay temporary file");}
    if(handle.value==INVALID_HANDLE_VALUE)throw std::runtime_error("Cannot reserve private replay temporary file");
    try{std::size_t offset=0;while(offset<bytes.size()){DWORD written=0;const DWORD amount=static_cast<DWORD>(std::min<std::size_t>(bytes.size()-offset,0x100000));
            if(!WriteFile(handle.value,bytes.data()+offset,amount,&written,NULL)||!written)throw std::runtime_error("Replay file write failed");
            offset+=written;}
        if(!FlushFileBuffers(handle.value))throw std::runtime_error("Replay file flush failed");
        if(!handle.close())throw std::runtime_error("Replay file close failed");
        if(!MoveFileExW(temporary.c_str(),destination.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH))throw std::runtime_error("Cannot publish replay file");
    }catch(...){handle.close();DeleteFileW(temporary.c_str());throw;}
#else
    if(path.empty()||path.find('\0')!=std::string::npos)throw std::runtime_error("Invalid replay path");
    std::string temporary=path+".tmp.XXXXXX";std::vector<char> name(temporary.begin(),temporary.end());name.push_back('\0');const int fd=mkstemp(name.data());
    if(fd<0)throw std::runtime_error("Cannot create private replay temporary file");
    bool open=true;
    try{std::size_t offset=0;while(offset<bytes.size()){const auto written=::write(fd,bytes.data()+offset,bytes.size()-offset);if(written<=0)throw std::runtime_error("Replay file write failed");offset+=static_cast<std::size_t>(written);}
        if(fsync(fd)!=0)throw std::runtime_error("Replay file flush failed");
        const int result=::close(fd);open=false;if(result!=0)throw std::runtime_error("Replay file close failed");
        if(std::rename(name.data(),path.c_str())!=0)throw std::runtime_error("Cannot publish replay file");
    }catch(...){if(open)::close(fd);::unlink(name.data());throw;}
#endif
}
} // namespace

void StockReplayStream::clear(){format_=ReplayContainer::PacketStream;metadata_.clear();bytes_.clear();packets_.clear();timeIndex_.clear();duration_=0;terminalOffset_=std::numeric_limits<std::size_t>::max();}
bool StockReplayStream::index(std::string& error){
    packets_.clear();timeIndex_.clear();duration_=0;terminalOffset_=std::numeric_limits<std::size_t>::max();std::size_t offset=0;
    while(offset<bytes_.size()){
        if(bytes_.size()-offset<12)return fail(error,"Truncated replay packet header");
        const auto* header=bytes_.data()+offset;
        ReplayPacketIndex packet;packet.headerOffset=offset;packet.payloadOffset=offset+12;packet.size=u32(header);packet.type=u32(header+4);
        // #1513 stops scanning before indexing the explicit -1 type. Keep
        // the marker and any trailer bytes verbatim for container round trips.
        if(packet.type==UINT32_MAX){terminalOffset_=offset;break;}
        const auto timeBits=u32(header+8);static_assert(sizeof(packet.time)==4,"Replay timestamps require 32-bit floats");std::memcpy(&packet.time,&timeBits,4);
        if(!std::isfinite(packet.time))return fail(error,"Replay packet timestamp is not finite");
        if(packet.size>bytes_.size()-packet.payloadOffset)return fail(error,"Truncated replay packet payload");
        packets_.push_back(packet);duration_=std::max(duration_,packet.time);offset=packet.payloadOffset+packet.size;
    }
    timeIndex_.resize(packets_.size());std::iota(timeIndex_.begin(),timeIndex_.end(),std::size_t(0));
    std::stable_sort(timeIndex_.begin(),timeIndex_.end(),[this](std::size_t a,std::size_t b){return packets_[a].time<packets_[b].time;});return true;
}
bool StockReplayStream::decode(const Bytes& file,std::string& error){
    try{StockReplayStream next;Cursor cursor{file};const std::uint32_t magic=file.size()>=4?u32(file.data()):0;
        if(magic==legacyMagic||magic==encryptedMagic){cursor.word();next.format_=magic==legacyMagic?ReplayContainer::LegacyZlib:ReplayContainer::EncryptedZlib;
            if(magic==encryptedMagic){const auto count=cursor.word();if(count>(file.size()-cursor.offset)/4)throw std::runtime_error("Invalid replay metadata count");
                for(std::uint32_t item=0;item<count;++item)next.metadata_.push_back(cursor.string());}
            const auto expected=cursor.word();if(expected>maximumExpandedSize)throw std::runtime_error("Replay expanded size exceeds the #1513 limit");
            const auto packedSize=magic==encryptedMagic?cursor.word():static_cast<std::uint32_t>(file.size()-cursor.offset);
            Bytes packed(file.begin()+static_cast<std::ptrdiff_t>(cursor.offset),file.end());
            if(magic==encryptedMagic){if(!packedSize||packedSize>packed.size()||packed.size()-packedSize>7)throw std::runtime_error("Invalid encrypted replay compressed length");
                packed=crypt(packed,false);for(std::size_t i=packedSize;i<packed.size();++i)if(packed[i]!=0)throw std::runtime_error("Replay ciphertext padding is not zero");packed.resize(packedSize);}
            next.bytes_=expand(packed,expected);
        }else next.bytes_=file;
        if(!next.index(error))return false;
        *this=std::move(next);error.clear();return true;
    }catch(const std::exception& exception){error=exception.what();return false;}
}
bool StockReplayStream::encode(ReplayContainer format,Bytes& file,std::string& error) const{
    try{Bytes output;if(format==ReplayContainer::PacketStream){if(!metadata_.empty())return fail(error,"Uncompressed replay streams cannot contain metadata");output=bytes_;}
        else{if(format!=ReplayContainer::LegacyZlib&&format!=ReplayContainer::EncryptedZlib)return fail(error,"Unknown replay container");
            if(format==ReplayContainer::LegacyZlib&&!metadata_.empty())return fail(error,"Legacy replay containers cannot contain metadata");
            Bytes packed=compress(bytes_);const auto packedSize=static_cast<std::uint32_t>(packed.size());put(output,format==ReplayContainer::LegacyZlib?legacyMagic:encryptedMagic);
            if(format==ReplayContainer::EncryptedZlib){if(metadata_.size()>UINT32_MAX)return fail(error,"Too many replay metadata blocks");put(output,static_cast<std::uint32_t>(metadata_.size()));
                for(const auto& metadata:metadata_){if(metadata.size()>UINT32_MAX)return fail(error,"Replay metadata block is too large");put(output,static_cast<std::uint32_t>(metadata.size()));output.insert(output.end(),metadata.begin(),metadata.end());}}
            put(output,static_cast<std::uint32_t>(bytes_.size()));if(format==ReplayContainer::EncryptedZlib){put(output,packedSize);packed.resize((packed.size()+7)&~std::size_t(7),0);packed=crypt(packed,true);}
            output.insert(output.end(),packed.begin(),packed.end());}
        if(output.size()>UINT32_MAX)return fail(error,"Replay file is too large");
        file=std::move(output);error.clear();return true;
    }catch(const std::exception& exception){error=exception.what();return false;}
}
bool StockReplayStream::append(std::uint32_t type,float time,const Bytes& payload,std::string& error){
    if(terminalOffset_!=std::numeric_limits<std::size_t>::max())return fail(error,"Cannot append after the replay end marker");
    if(!std::isfinite(time))return fail(error,"Replay packet timestamp is not finite");
    if(payload.size()>UINT32_MAX||bytes_.size()>UINT32_MAX-12||payload.size()>UINT32_MAX-12-bytes_.size())return fail(error,"Replay packet stream is too large");
    try{Bytes alias;if(&payload==&bytes_)alias=payload;const Bytes& source=&payload==&bytes_?alias:payload;
        const std::size_t number=packets_.size();const auto position=std::upper_bound(timeIndex_.begin(),timeIndex_.end(),time,[this](float value,std::size_t index){return value<packets_[index].time;});
        const auto timePosition=static_cast<std::size_t>(position-timeIndex_.begin());
        ReplayPacketIndex packet;packet.headerOffset=bytes_.size();packet.payloadOffset=bytes_.size()+12;packet.size=static_cast<std::uint32_t>(source.size());packet.type=type;packet.time=time;
        // Reserve before publishing any bytes. Monotonic recording appends to
        // both indices without copying or rescanning the accumulated stream.
        reserveFor(bytes_,bytes_.size()+12+source.size());reserveFor(packets_,number+1);reserveFor(timeIndex_,number+1);
        put(bytes_,packet.size);put(bytes_,type);std::uint32_t bits=0;std::memcpy(&bits,&time,4);put(bytes_,bits);bytes_.insert(bytes_.end(),source.begin(),source.end());
        if(type==UINT32_MAX)terminalOffset_=packet.headerOffset;
        else{packets_.push_back(packet);timeIndex_.insert(timeIndex_.begin()+static_cast<std::ptrdiff_t>(timePosition),number);duration_=std::max(duration_,time);}error.clear();return true;
    }catch(const std::exception& exception){error=exception.what();return false;}
}
bool StockReplayStream::packetString(std::size_t packet,std::string& value,std::string& error) const{
    if(packet>=packets_.size())return fail(error,"Replay packet index is out of range");
    const auto& entry=packets_[packet];
    if(entry.size<4)return fail(error,"Truncated replay string length");
    const auto length=u32(bytes_.data()+entry.payloadOffset);
    if(length!=entry.size-4)return fail(error,"Replay string length does not match packet payload");
    value.assign(reinterpret_cast<const char*>(bytes_.data()+entry.payloadOffset+4),length);error.clear();return true;
}
std::size_t StockReplayStream::lowerBoundTime(float target) const{
    const auto found=std::lower_bound(timeIndex_.begin(),timeIndex_.end(),target,[this](std::size_t index,float value){return packets_[index].time<value;});
    return found==timeIndex_.end()?packets_.size():*found;
}
bool StockReplayFile::open(const std::string& path,std::string& error){try{const Bytes bytes=readFile(path);if(!stream_.decode(bytes,error))return false;cursor_=0;return true;}catch(const std::exception& exception){error=exception.what();return false;}}
bool StockReplayFile::save(const std::string& path,ReplayContainer format,std::string& error) const{Bytes bytes;if(!stream_.encode(format,bytes,error))return false;try{publish(path,bytes);error.clear();return true;}catch(const std::exception& exception){error=exception.what();return false;}}
bool StockReplayFile::seekPacket(std::size_t index){if(index>stream_.packets().size())return false;cursor_=index;return true;}
bool StockReplayFile::next(ReplayPacketIndex& packet){if(cursor_>=stream_.packets().size())return false;packet=stream_.packets()[cursor_++];return true;}
} // namespace wot
