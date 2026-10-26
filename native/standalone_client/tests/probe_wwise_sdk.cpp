// Standalone Win32 probe: use the real SDK and its physical-file I/O sample.
// Run with original encoded bank paths (Init first). It does not post events
// or establish audible/playback acceptance.
#include <windows.h>
#include <AK/AkWwiseSDKVersion.h>
#include <AK/SoundEngine/Common/AkMemoryMgr.h>
#include <AK/SoundEngine/Common/AkModule.h>
#include <AK/SoundEngine/Common/AkSoundEngine.h>
#include <AK/SoundEngine/Common/AkStreamMgrModule.h>
#include <AK/MusicEngine/Common/AkMusicEngine.h>
#include "AkDefaultIOHookBlocking.h"
#include "../bigworld/stock_audio_plugins.hpp"
#include "wot/wwise_bank.hpp"

#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <vector>
#include <malloc.h>

#if AK_WWISESDK_VERSION_MAJOR != 2017 || AK_WWISESDK_VERSION_MINOR != 1 || \
    AK_WWISESDK_VERSION_SUBMINOR != 9 || AK_WWISESDK_VERSION_BUILD != 6501
#error This probe requires the reviewed Wwise 2017.1.9.6501 SDK.
#endif

namespace AK {
void* AllocHook(size_t size) { return std::malloc(size); }
void FreeHook(void* memory) { std::free(memory); }
void* VirtualAllocHook(void* address, size_t size, DWORD allocation, DWORD protection) {
    return ::VirtualAlloc(address, size, allocation, protection);
}
void VirtualFreeHook(void* address, size_t size, DWORD release) {
    ::VirtualFree(address, size, release);
}
}

int main(int argc, char** argv) {
    std::cout << "Wwise SDK " << AK_WWISESDK_VERSIONNAME << " build "
              << AK_WWISESDK_VERSION_BUILD << '\n';
    bool memoryReady = false, soundReady = false, musicReady = false;
    CAkDefaultIOHookBlocking io;
    std::vector<std::vector<std::uint8_t>> banks;
    std::vector<void*> aligned;
    std::vector<AkBankID> ids;
    int outcome = 1;
    try {
        AkMemSettings memory = {};
        memory.uMaxNumPools = 100;
        AKRESULT result = AK::MemoryMgr::Init(&memory);
        std::cout << "MemoryMgr::Init=" << result << '\n';
        if (result != AK_Success) throw std::runtime_error("Memory initialization failed");
        memoryReady = true;
        AkStreamMgrSettings stream;
        AK::StreamMgr::GetDefaultSettings(stream);
        if (!AK::StreamMgr::Create(stream)) throw std::runtime_error("Stream manager initialization failed");
        AkDeviceSettings device;
        AK::StreamMgr::GetDefaultDeviceSettings(device);
        result = io.Init(device, false);
        std::cout << "BlockingIO::Init=" << result << '\n';
        if (result != AK_Success) throw std::runtime_error("I/O initialization failed");
        AkInitSettings settings;
        AkPlatformInitSettings platform;
        AK::SoundEngine::GetDefaultInitSettings(settings);
        AK::SoundEngine::GetDefaultPlatformInitSettings(platform);
        platform.hWnd = ::GetConsoleWindow();
        result = AK::SoundEngine::Init(&settings, &platform);
        std::cout << "SoundEngine::Init=" << result << '\n';
        if (result != AK_Success) throw std::runtime_error("Sound initialization failed");
        soundReady = true;
        AkMusicSettings music;
        AK::MusicEngine::GetDefaultInitSettings(music);
        result = AK::MusicEngine::Init(&music);
        std::cout << "MusicEngine::Init=" << result << '\n';
        if (result != AK_Success) throw std::runtime_error("Music initialization failed");
        musicReady = true;
        result = AK::SoundEngine::RegisterGameObj(1, "SDK probe listener");
        std::cout << "RegisterGameObj listener=" << result << '\n';
        if (result != AK_Success) throw std::runtime_error("Listener registration failed");
        const AkGameObjectID listener = 1;
        result = AK::SoundEngine::SetDefaultListeners(&listener, 1);
        std::cout << "SetDefaultListeners=" << result << '\n';
        if (result != AK_Success) throw std::runtime_error("Listener routing failed");
        result = AK::SoundEngine::RegisterGameObj(2, "SDK probe emitter");
        std::cout << "RegisterGameObj emitter=" << result << '\n';
        if (result != AK_Success) throw std::runtime_error("Emitter registration failed");
        AkTransform listenerPose, sourcePose;
        listenerPose.Set(0.f, 0.f, 0.f, 0.f, 0.f, 1.f, 0.f, 1.f, 0.f);
        sourcePose.Set(0.f, 0.f, 10.f, 0.f, 0.f, 1.f, 0.f, 1.f, 0.f);
        result = AK::SoundEngine::SetPosition(1, listenerPose);
        std::cout << "SetPosition listener=" << result << '\n';
        if (result != AK_Success) throw std::runtime_error("Listener transform failed");
        result = AK::SoundEngine::SetPosition(2, sourcePose);
        std::cout << "SetPosition emitter=" << result << '\n';
        if (result != AK_Success) throw std::runtime_error("Emitter transform failed");
        // Memory-view loads require these bytes to live until UnloadBank.
        banks.reserve(static_cast<std::size_t>(argc - 1));
        for (int index = 1; index < argc; ++index) {
            std::ifstream file(argv[index], std::ios::binary);
            if (!file) throw std::runtime_error("Cannot read original bank");
            std::vector<std::uint8_t> encoded{
                std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
            banks.push_back(wot::decode_wot_1513_wwise_bank_header(encoded));
            void* storage = _aligned_malloc(banks.back().size(), AK_BANK_PLATFORM_DATA_ALIGNMENT);
            if (!storage) throw std::bad_alloc();
            std::memcpy(storage, banks.back().data(), banks.back().size());
            aligned.push_back(storage);
            AkBankID id = AK_INVALID_UNIQUE_ID;
            result = AK::SoundEngine::LoadBank(storage,
                static_cast<AkUInt32>(banks.back().size()), id);
            std::cout << "LoadBank " << argv[index] << " result=" << result << " id=" << id << '\n';
            if (result != AK_Success) throw std::runtime_error("Actual SDK rejected original bank");
            ids.push_back(id);
        }
        result = AK::SoundEngine::RenderAudio(true);
        std::cout << "RenderAudio=" << result << '\n';
        if (result != AK_Success) throw std::runtime_error("Audio rendering command failed");
        outcome = 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
    }
    for (std::size_t index = ids.size(); index > 0; --index)
        AK::SoundEngine::UnloadBank(ids[index - 1], aligned[index - 1]);
    if (soundReady) {
        const AKRESULT emitter = AK::SoundEngine::UnregisterGameObj(2);
        const AKRESULT listener = AK::SoundEngine::UnregisterGameObj(1);
        std::cout << "UnregisterGameObj emitter=" << emitter << '\n';
        std::cout << "UnregisterGameObj listener=" << listener << '\n';
        if (emitter != AK_Success || listener != AK_Success) outcome = 1;
    }
    if (musicReady) AK::MusicEngine::Term();
    if (soundReady) AK::SoundEngine::Term();
    if (AK::IAkStreamMgr::Get()) {
        io.Term();
        AK::IAkStreamMgr::Get()->Destroy();
    }
    if (memoryReady) AK::MemoryMgr::Term();
    for (void* storage : aligned) _aligned_free(storage);
    return outcome;
}
