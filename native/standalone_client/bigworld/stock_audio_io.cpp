#include "pch.hpp"
#include "stock_audio_io.hpp"

#include "cstdmf/string_utils.hpp"
#include "resmgr/bwresource.hpp"
#include "resmgr/multi_file_system.hpp"
#include "wot/wwise_bank.hpp"
#include <AK/Tools/Common/AkPlatformFuncs.h>

#include <algorithm>
#include <cstring>
#include <limits>
#include <memory>
#include <mutex>
#include <vector>

BW_BEGIN_NAMESPACE
namespace {
struct ResourceHandle {
    HANDLE file = INVALID_HANDLE_VALUE;
    std::uint64_t base = 0, size = 0;
    std::mutex mutex;
    std::vector<std::uint8_t> header;
    ~ResourceHandle() {
        if (file != INVALID_HANDLE_VALUE) ::CloseHandle(file);
    }
    bool read(std::uint64_t offset, AkUInt32 count, void* output) {
        if (offset > size || count > size - offset) return false;
        std::lock_guard<std::mutex> lock(mutex);
        LARGE_INTEGER position;
        position.QuadPart = static_cast<LONGLONG>(base + offset);
        DWORD received = 0;
        return ::SetFilePointerEx(file, position, NULL, FILE_BEGIN) &&
            ::ReadFile(file, output, count, &received, NULL) && received == count;
    }
};
std::uint32_t u32(const std::uint8_t* value) {
    return std::uint32_t(value[0]) | (std::uint32_t(value[1]) << 8) |
        (std::uint32_t(value[2]) << 16) | (std::uint32_t(value[3]) << 24);
}
}

StockAudioResourceIO::StockAudioResourceIO() : m_deviceID(AK_INVALID_DEVICE_ID) {}
StockAudioResourceIO::~StockAudioResourceIO() { Term(); }

AKRESULT StockAudioResourceIO::Init(const AkDeviceSettings& authored) {
    if (m_deviceID != AK_INVALID_DEVICE_ID) return AK_Fail;
    fileSystem_ = BWResource::instance().fileSystem();
    if (!fileSystem_) return AK_Fail;
    AkDeviceSettings settings = authored;
    settings.uSchedulerTypeFlags = AK_SCHEDULER_BLOCKING;
    AK::StreamMgr::SetFileLocationResolver(this);
    m_deviceID = AK::StreamMgr::CreateDevice(settings, this);
    if (m_deviceID == AK_INVALID_DEVICE_ID) {
        AK::StreamMgr::SetFileLocationResolver(NULL);
        fileSystem_ = NULL;
        return AK_Fail;
    }
    return AK_Success;
}

void StockAudioResourceIO::Term() {
    if (m_deviceID != AK_INVALID_DEVICE_ID) {
        AK::StreamMgr::DestroyDevice(m_deviceID);
        m_deviceID = AK_INVALID_DEVICE_ID;
    }
    if (AK::StreamMgr::GetFileLocationResolver() == this)
        AK::StreamMgr::SetFileLocationResolver(NULL);
    fileSystem_ = NULL;
}

AKRESULT StockAudioResourceIO::setResourcePath(const BW::string& path) {
    BW::wstring wide;
    if (!bw_utf8tow(path, wide)) return AK_InvalidParameter;
    const AKRESULT result = SetBasePath(wide.c_str());
    // The SDK's pathname builder stores the path before checking the host OS.
    // A mounted resource directory has no host-directory counterpart.
    if (result == AK_PathNotFound && fileSystem_ &&
        fileSystem_->getFileType(path) == IFileSystem::FT_DIRECTORY)
        return AK_Success;
    return result;
}

AKRESULT StockAudioResourceIO::openResource(const AkOSChar* path,
    AkOpenMode mode, AkFileDesc& output) {
    if (mode != AK_OpenModeRead || !fileSystem_) return AK_NotImplemented;
    BW::string resource;
    if (!bw_wtoutf8(path, resource)) return AK_InvalidParameter;
    std::replace(resource.begin(), resource.end(), '\\', '/');
    IFileSystem::FileInfo info;
    const auto type = fileSystem_->getFileType(resource, &info);
    if (type != IFileSystem::FT_FILE && type != IFileSystem::FT_ARCHIVE)
        return AK_FileNotFound;
    IFileSystem::FileDataLocation location;
    if (!fileSystem_->locateFileData(resource, &location)) return AK_Fail;
    // ZipFileStreamer retains the archive's nonrecursive mutex until close.
    // Wwise keeps every PCK handle until unload and reads it on its I/O thread;
    // resolve the mounted range briefly, then give each resource its own handle.
    const auto maximum = static_cast<std::uint64_t>(std::numeric_limits<LONGLONG>::max());
    if (location.offset > maximum || info.size > maximum - location.offset)
        return AK_InvalidFile;
    BW::wstring host;
    if (!bw_utf8tow(location.hostResourceID, host)) return AK_InvalidParameter;
    std::unique_ptr<ResourceHandle> handle(new ResourceHandle);
    handle->file = ::CreateFileW(host.c_str(), GENERIC_READ, FILE_SHARE_READ,
        NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (handle->file == INVALID_HANDLE_VALUE) return AK_FileNotFound;
    LARGE_INTEGER hostSize;
    if (!::GetFileSizeEx(handle->file, &hostSize) || hostSize.QuadPart < 0 ||
        location.offset + info.size > static_cast<std::uint64_t>(hostSize.QuadPart))
        return AK_InvalidFile;
    handle->base = location.offset;
    handle->size = info.size;
    if (resource.size() >= 4 && resource.substr(resource.size() - 4) == ".bnk") {
        std::uint8_t prefix[8];
        if (!handle->read(0, sizeof(prefix), prefix) ||
            u32(prefix) != 0x44484b42u || info.size < 8 ||
            u32(prefix + 4) > info.size - 8)
            return AK_InvalidFile;
        const std::uint64_t headerSize = std::uint64_t(u32(prefix + 4)) + 8;
        if (headerSize > std::numeric_limits<std::size_t>::max()) return AK_InvalidFile;
        std::vector<std::uint8_t> encoded(static_cast<std::size_t>(headerSize));
        std::copy(prefix, prefix + 8, encoded.begin());
        if (!handle->read(8, u32(prefix + 4), encoded.data() + 8))
            return AK_InvalidFile;
        try { handle->header = wot::decode_wot_1513_wwise_bank_header(encoded); }
        catch (const std::exception& error) {
            ERROR_MSG("StockAudio: bank header %s: %s\n", resource.c_str(), error.what());
            return AK_InvalidFile;
        }
    }
    output.hFile = reinterpret_cast<AkFileHandle>(handle.release());
    output.iFileSize = static_cast<AkInt64>(info.size);
    output.uSector = 0;
    output.deviceID = m_deviceID;
    output.pCustomParam = NULL;
    output.uCustomParamSize = 0;
    return AK_Success;
}

AKRESULT StockAudioResourceIO::Open(const AkOSChar* name, AkOpenMode mode,
    AkFileSystemFlags* flags, bool& synchronous, AkFileDesc& output) {
    AkOSChar path[AK_MAX_PATH];
    const auto result = GetFullFilePath(name, flags, mode, path);
    if (result != AK_Success) return result;
    synchronous = true;
    return openResource(path, mode, output);
}
AKRESULT StockAudioResourceIO::Open(AkFileID id, AkOpenMode mode,
    AkFileSystemFlags* flags, bool& synchronous, AkFileDesc& output) {
    AkOSChar path[AK_MAX_PATH];
    const auto result = GetFullFilePath(id, flags, mode, path);
    if (result != AK_Success) return result;
    synchronous = true;
    return openResource(path, mode, output);
}

AKRESULT StockAudioResourceIO::Read(AkFileDesc& file, const AkIoHeuristics&,
    void* output, AkIOTransferInfo& transfer) {
    auto* handle = reinterpret_cast<ResourceHandle*>(file.hFile);
    if (!handle || !output || transfer.uRequestedSize > transfer.uBufferSize) return AK_Fail;
    const auto offset = transfer.uFilePosition;
    if (!handle->read(offset, transfer.uRequestedSize, output))
        return AK_Fail;
    if (offset < handle->header.size()) {
        const auto headerOffset = static_cast<std::size_t>(offset);
        const auto count = std::min<std::size_t>(transfer.uRequestedSize,
            handle->header.size() - headerOffset);
        std::memcpy(output, handle->header.data() + headerOffset, count);
    }
    return AK_Success;
}
AKRESULT StockAudioResourceIO::Write(AkFileDesc&, const AkIoHeuristics&,
    void*, AkIOTransferInfo&) { return AK_NotImplemented; }
AKRESULT StockAudioResourceIO::Close(AkFileDesc& file) {
    delete reinterpret_cast<ResourceHandle*>(file.hFile);
    file.hFile = NULL;
    return AK_Success;
}
AkUInt32 StockAudioResourceIO::GetBlockSize(AkFileDesc&) { return 1; }
void StockAudioResourceIO::GetDeviceDesc(AkDeviceDesc& output) {
    output.deviceID = m_deviceID;
    output.bCanRead = true;
    output.bCanWrite = false;
    AKPLATFORM::SafeStrCpy(output.szDeviceName, AKTEXT("BigWorld mounted resources"),
        AK_MONITOR_DEVICENAME_MAXLENGTH);
    output.uStringSize = static_cast<AkUInt32>(wcslen(output.szDeviceName) + 1);
}
AkUInt32 StockAudioResourceIO::GetDeviceData() { return 0; }

BW_END_NAMESPACE
