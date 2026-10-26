#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "resmgr/file_system.hpp"
#include <AK/SoundEngine/Common/AkStreamMgrModule.h>
#include "AkFileLocationBase.h"
#include "AkFilePackageLowLevelIO.h"

BW_BEGIN_NAMESPACE

// Adapt only the physical file operations. The official SDK still owns PCK
// tables, language selection, package offsets and the streaming scheduler.
class StockAudioResourceIO : public AK::StreamMgr::IAkFileLocationResolver,
    public AK::StreamMgr::IAkIOHookBlocking, public CAkFileLocationBase
{
public:
    StockAudioResourceIO();
    virtual ~StockAudioResourceIO();
    AKRESULT Init(const AkDeviceSettings& settings);
    void Term();
    AKRESULT setResourcePath(const BW::string& path);

    virtual AKRESULT Open(const AkOSChar*, AkOpenMode, AkFileSystemFlags*,
        bool&, AkFileDesc&) override;
    virtual AKRESULT Open(AkFileID, AkOpenMode, AkFileSystemFlags*,
        bool&, AkFileDesc&) override;
    virtual AKRESULT Read(AkFileDesc&, const AkIoHeuristics&, void*,
        AkIOTransferInfo&) override;
    virtual AKRESULT Write(AkFileDesc&, const AkIoHeuristics&, void*,
        AkIOTransferInfo&) override;
    virtual AKRESULT Close(AkFileDesc&) override;
    virtual AkUInt32 GetBlockSize(AkFileDesc&) override;
    virtual void GetDeviceDesc(AkDeviceDesc&) override;
    virtual AkUInt32 GetDeviceData() override;

protected:
    // Required by CAkFilePackageLowLevelIO's native descriptor producer.
    AkDeviceID m_deviceID;
private:
    AKRESULT openResource(const AkOSChar*, AkOpenMode, AkFileDesc&);
    FileSystemPtr fileSystem_;
};

typedef CAkFilePackageLowLevelIO<StockAudioResourceIO> StockAudioIO;

BW_END_NAMESPACE
