#include "pch.hpp"
#include "stock_audio.hpp"
#include "stock_audio_io.hpp"
#include "stock_audio_plugins.hpp"

#include "cstdmf/string_utils.hpp"
#include "pyscript/script.hpp"
#include "pyscript/script_math.hpp"
#include "romp/enviro_minder.hpp"
#include "romp/weather.hpp"
#include "space/client_space.hpp"
#include "space/deprecated_space_helpers.hpp"
#include "terrain/base_terrain_block.hpp"
#include <AK/SoundEngine/Common/AkMemoryMgr.h>
#include <AK/SoundEngine/Common/AkModule.h>
#include <AK/SoundEngine/Common/AkSoundEngine.h>
#include <AK/MusicEngine/Common/AkMusicEngine.h>
#include <d3dx9math.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cfloat>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <utility>
#include <vector>

// Required SDK platform allocation hooks, as in its Windows IntegrationDemo.
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

BW_BEGIN_NAMESPACE
namespace StockAudioDetail {

struct Source;
struct Voice;
typedef std::shared_ptr<Source> SourcePtr;
typedef std::shared_ptr<Voice> VoicePtr;
void updateSources();
void deliverCallbacks();
void releaseSources();

struct LoadedBank {
    AkUInt32 id;
    bool package;
};
struct Manager {
    bool memory = false, sound = false, music = false, ready = false, com = false;
    bool ioReady = false;
    AkMemPoolId preparePool = AK_INVALID_POOL_ID;
    StockAudioIO io;
    DataSectionPtr configuration, profile;
    std::map<BW::string, LoadedBank> banks;
    std::set<BW::string> permanent, current;
    Matrix camera;
    Vector3 previousCamera = Vector3::zero();
    bool hasPreviousCamera = false;
    float cameraRTPCElapsed = 0.f;
    MatrixProviderPtr cameraShift;
    float masterVolume = 1.f;
    AkUniqueID auxiliaryBus = 0;
    float auxiliaryGain = 0.f;
    std::vector<AkUniqueID> pendingEvents, persistentEvents;
    std::set<AkUniqueID> failedEvents;
    AkGameObjectID listener = AK_INVALID_GAME_OBJECT;
    AkGameObjectID global = AK_INVALID_GAME_OBJECT;
    AkGameObjectID nextObject = 1;
    std::uint64_t epoch = 0, nextVoice = 1;
    std::map<AkGameObjectID, std::weak_ptr<Source>> sources;
    std::map<std::uint64_t, VoicePtr> voices;
    std::mutex completionsMutex;
    std::vector<std::pair<std::uint64_t, AkPlayingID>> completions;
    Manager() { camera.setIdentity(); }
};
Manager& manager() {
    // Explicit fini runs before resource/Python teardown; the inert C++ shell
    // remains alive while SDK callbacks finish during SoundEngine::Term.
    static Manager instance;
    return instance;
}
bool result(AKRESULT code, const char* operation, const char* name = "") {
    if (code == AK_Success) return true;
    ERROR_MSG("StockAudio: %s %s failed (AKRESULT=%d)\n", operation, name, int(code));
    return false;
}
AkTransform transform(const Matrix& matrix, const Vector3& offset = Vector3::zero()) {
    // #1513 0x01250e10 adds the supplied offset to translation. It does not
    // rotate the offset as a point by the provider matrix.
    const Vector3 position = matrix.applyToOrigin() + offset;
    const Vector3 front = matrix.applyToUnitAxisVector(2);
    const Vector3 up = matrix.applyToUnitAxisVector(1);
    AkTransform output;
    output.Set(position.x, position.y, position.z,
        front.x, front.y, front.z, up.x, up.y, up.z);
    return output;
}
bool loadBank(const BW::string& name) {
    Manager& state = manager();
    if (!state.sound) return false;
    if (state.banks.find(name) != state.banks.end()) return true;
    BW::wstring wide;
    if (!bw_utf8tow(name, wide)) return false;
    LoadedBank bank;
    bank.package = name.size() >= 4 && name.substr(name.size() - 4) == ".pck";
    AK::SoundEngine::RenderAudio(true); // original batch processor 0x01255480
    AKRESULT status;
    if (bank.package) {
        status = state.io.LoadFilePackage(wide.c_str(), bank.id);
        // SDK package parsing can succeed with an absent current language.
        // It returns a real package ID and retains the actual lookup table.
        if (status == AK_InvalidLanguage) status = AK_Success;
    } else {
        status = AK::SoundEngine::LoadBank(wide.c_str(), AK_DEFAULT_POOL_ID, bank.id);
    }
    if (!result(status, bank.package ? "LoadFilePackage" : "LoadBank", name.c_str())) return false;
    state.banks.emplace(name, bank);
    INFO_MSG("StockAudio: loaded %s id=%u\n", name.c_str(), unsigned(bank.id));
    return true;
}
bool unloadBank(const BW::string& name) {
    Manager& state = manager();
    const auto found = state.banks.find(name);
    if (found == state.banks.end()) return true;
    AK::SoundEngine::RenderAudio(true);
    const auto status = found->second.package ?
        state.io.UnloadFilePackage(found->second.id) :
        AK::SoundEngine::UnloadBank(found->second.id, NULL);
    if (!result(status, "UnloadBank", name.c_str())) return false;
    state.banks.erase(found);
    return true;
}
bool bankNames(const BW::string& section, std::vector<BW::string>& output) {
    DataSectionPtr node = manager().profile ? manager().profile->openSection(section) : NULL;
    if (!node) {
        ERROR_MSG("StockAudio: missing authored bank group %s\n", section.c_str());
        return false;
    }
    BW::vector<DataSectionPtr> projects;
    node->openSections("project", projects);
    for (const auto& project : projects) {
        const BW::string name = project->readString("name");
        if (name.empty()) return false;
        output.push_back(name);
    }
    return true;
}
}

bool stockAudioReady() { return StockAudioDetail::manager().ready; }

bool initialiseStockAudio(DataSectionPtr root, void* window) {
    using namespace StockAudioDetail;
    Manager& state = manager();
    if (state.ready) return true;
    finiStockAudio();
    state.configuration = root;
    DataSectionPtr sound = root ? root->openSection("soundMgr") : NULL;
    if (!sound || !sound->readBool("enabled", true)) return false;
    const BW::string active = sound->readString("WWISE_active_profile");
    state.profile = active.empty() ? NULL : sound->openSection(active);
    if (!state.profile) {
        ERROR_MSG("StockAudio: missing authored WWISE_active_profile\n");
        return false;
    }
    const HRESULT com = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    state.com = SUCCEEDED(com);
    AkMemSettings memory = {};
    memory.uMaxNumPools = 100; // #1513 0x01246e20
    if (!result(AK::MemoryMgr::Init(&memory), "MemoryMgr::Init")) goto failed;
    state.memory = true;
    {
        DataSectionPtr pools = state.profile->openSection("memoryManager");
        if (!pools) goto failed;
        AkStreamMgrSettings stream;
        AK::StreamMgr::GetDefaultSettings(stream);
        stream.uMemorySize = pools->readInt("streamingPool", 2) << 20;
        if (!AK::StreamMgr::Create(stream)) goto failed;
        AkDeviceSettings device;
        AK::StreamMgr::GetDefaultDeviceSettings(device);
        device.uIOMemorySize = pools->readInt("IOPoolSize", 4) << 20;
        if (!result(state.io.Init(device), "ResourceIO::Init")) goto failed;
        state.ioReady = true;
        if (!result(state.io.setResourcePath(sound->readString("wwmediaPath", "audioww")),
            "ResourceIO::setResourcePath")) goto failed;
        AkInitSettings settings;
        AkPlatformInitSettings platform;
        AK::SoundEngine::GetDefaultInitSettings(settings);
        AK::SoundEngine::GetDefaultPlatformInitSettings(platform);
        settings.uDefaultPoolSize = pools->readInt("defaultPool", 12) << 20;
        settings.fDefaultPoolRatioThreshold = 0.96f;
        settings.uCommandQueueSize *= 8;
        settings.uMonitorPoolSize *= 4;
        settings.uMonitorQueuePoolSize *= 4;
        settings.bEnableGameSyncPreparation = false;
        state.preparePool = AK::MemoryMgr::CreatePool(NULL,
            pools->readInt("preparedPool", 106) << 20, 0x400, AkMalloc, 0);
        if (state.preparePool == AK_INVALID_POOL_ID) goto failed;
        settings.uPrepareEventMemoryPoolID = state.preparePool;
        platform.hWnd = static_cast<HWND>(window);
        platform.uLEngineDefaultPoolSize = pools->readInt("lowEnginePool", 8) << 20;
        platform.fLEngineDefaultPoolRatioThreshold = 0.96f;
        platform.uSampleRate = 48000;
        platform.eAudioAPI = AkAPI_Default;
        if (sound->readBool("highPriorityBankThread", false))
            platform.threadBankManager.nPriority = THREAD_PRIORITY_ABOVE_NORMAL;
        if (!result(AK::SoundEngine::Init(&settings, &platform), "SoundEngine::Init")) goto failed;
        state.sound = true;
        AkMusicSettings music;
        AK::MusicEngine::GetDefaultInitSettings(music);
        if (!result(AK::MusicEngine::Init(&music), "MusicEngine::Init")) goto failed;
        state.music = true;
        // #1513 0x0092c270 reads the process-visible SYSTEM_INFO count.
        SYSTEM_INFO processor;
        GetSystemInfo(&processor);
        int voices = processor.dwNumberOfProcessors > 2 ? 110 : 50;
        int threshold = processor.dwNumberOfProcessors > 2 ? -50 : -30;
        DataSectionPtr render = state.profile->openSection("soundRender");
        if (render && render->readBool("use_config", false)) {
            voices = render->readInt("max_voices", voices);
            threshold = render->readInt("HDR_lower_threshold", threshold);
        }
        AK::SoundEngine::SetMaxNumVoicesLimit(static_cast<AkUInt16>(voices));
        AK::SoundEngine::SetVolumeThreshold(float(threshold));
    }
    if (!loadBank("Init.bnk")) goto failed;
    state.listener = state.nextObject++;
    state.global = state.nextObject++;
    if (!result(AK::SoundEngine::RegisterGameObj(state.listener, "Stock listener"), "RegisterGameObj") ||
        !result(AK::SoundEngine::RegisterGameObj(state.global, "Stock global"), "RegisterGameObj") ||
        !result(AK::SoundEngine::SetDefaultListeners(&state.listener, 1), "SetDefaultListeners")) goto failed;
    ++state.epoch;
    state.ready = true;
    {
        std::vector<BW::string> names;
        if (!bankNames("SFX_soundbanks_loadonce", names)) goto failed;
        for (const auto& name : names) {
            if (!loadBank(name)) goto failed;
            state.permanent.insert(name);
        }
    }
    INFO_MSG("StockAudio: Wwise 2017.1 application audio initialized\n");
    return true;
failed:
    finiStockAudio();
    return false;
}

bool selectStockAudioBankSet(const BW::string& section) {
    using namespace StockAudioDetail;
    Manager& state = manager();
    if (!state.ready) return false;
    std::vector<BW::string> names;
    if (!bankNames(section, names)) return false;
    std::set<BW::string> next(names.begin(), names.end());
    bool accepted = true;
    for (const auto& name : state.current)
        if (!next.count(name) && !state.permanent.count(name))
            accepted = unloadBank(name) && accepted;
    for (const auto& name : names) accepted = loadBank(name) && accepted;
    state.current = std::move(next);
    return accepted;
}

void tickStockAudio(const Matrix& cameraWorld, float elapsed) {
    using namespace StockAudioDetail;
    Manager& state = manager();
    if (!state.ready) return;
    const auto epoch = state.epoch;
    state.camera = cameraWorld;
    const AkTransform camera = transform(cameraWorld);
    AK::SoundEngine::SetPosition(state.listener, camera);
    AK::SoundEngine::SetPosition(state.global, camera);
    updateSources();
    if (!state.ready || state.epoch != epoch) return;
    // #1513 0x01248fd0 retries queued sync events in order and removes only
    // the first event successfully posted during this update.
    for (auto it = state.pendingEvents.begin(); it != state.pendingEvents.end(); ++it) {
        const auto id = AK::SoundEngine::PostEvent(*it, state.global);
        if (id != AK_INVALID_PLAYING_ID) {
            state.failedEvents.erase(*it);
            state.pendingEvents.erase(it);
            break;
        }
        if (state.failedEvents.insert(*it).second)
            ERROR_MSG("StockAudio: queued PostEvent unavailable id=%u\n", unsigned(*it));
    }
    deliverCallbacks();
    if (!state.ready || state.epoch != epoch) return;
    AK::SoundEngine::SetRTPCValue("RTPC_ext_menu_volume_master", state.masterVolume * 100.f);
    AK::SoundEngine::RenderAudio(true);
    // #1513 0x01248610 publishes camera RTPCs after RenderAudio. The first
    // sample and nonpositive intervals have zero velocity; there is no
    // smoothed position or invented terrain height in this contract.
    const Vector3 position = cameraWorld.applyToOrigin();
    const float speed = state.hasPreviousCamera && elapsed > 0.f ?
        (position - state.previousCamera).length() / elapsed : 0.f;
    state.previousCamera = position;
    state.hasPreviousCamera = true;
    ClientSpacePtr space = DeprecatedSpaceHelpers::cameraSpace();
    if (!space) return;
    const float terrain = space->findTerrainHeight(position);
    if (!std::isfinite(terrain) || terrain == FLT_MIN ||
        terrain == Terrain::BaseTerrainBlock::NO_TERRAIN) return;
    // The native helper 0x01249170 accumulates only actual space samples.
    state.cameraRTPCElapsed += elapsed;
    if (state.cameraRTPCElapsed < .2f) return;
    state.cameraRTPCElapsed = 0.f;
    AK::SoundEngine::SetRTPCValue("RTPC_ext_camera_speed", speed, state.global);
    AK::SoundEngine::SetRTPCValue("RTPC_ext_camera_height", position.y, state.global);
    AK::SoundEngine::SetRTPCValue("RTPC_ext_camera_height_rel", position.y - terrain, state.global);
    if (const Weather* weather = space->enviro().weather()) {
        const Vector2& wind = weather->wind();
        AK::SoundEngine::SetRTPCValue("RTPC_ext_wind_intensity", wind.length(), state.global);
    }
}

void finiStockAudio() {
    using namespace StockAudioDetail;
    Manager& state = manager();
    state.ready = false;
    ++state.epoch;
    releaseSources();
    if (state.sound) {
        AK::SoundEngine::StopAll();
        AK::SoundEngine::UnregisterAllGameObj();
    }
    if (state.music) { AK::MusicEngine::Term(); state.music = false; }
    if (state.sound) { AK::SoundEngine::Term(); state.sound = false; }
    if (state.ioReady) { state.io.Term(); state.ioReady = false; }
    if (AK::IAkStreamMgr::Get()) AK::IAkStreamMgr::Get()->Destroy();
    state.banks.clear(); state.permanent.clear(); state.current.clear();
    state.pendingEvents.clear(); state.persistentEvents.clear(); state.failedEvents.clear();
    state.hasPreviousCamera = false;
    state.cameraRTPCElapsed = 0.f;
    if (state.preparePool != AK_INVALID_POOL_ID) {
        AK::MemoryMgr::DestroyPool(state.preparePool);
        state.preparePool = AK_INVALID_POOL_ID;
    }
    if (state.memory) { AK::MemoryMgr::Term(); state.memory = false; }
    if (state.com) { CoUninitialize(); state.com = false; }
    // Cookies remain allocated until SoundEngine::Term has joined its workers.
    state.voices.clear();
    { std::lock_guard<std::mutex> lock(state.completionsMutex); state.completions.clear(); }
    state.configuration = NULL; state.profile = NULL; state.cameraShift = NULL;
}

namespace StockAudioDetail {
struct Source {
    AkGameObjectID id = AK_INVALID_GAME_OBJECT;
    std::uint64_t epoch = 0;
    bool global = false;
    bool auxSend = false;
    MatrixProviderPtr matrix;
    Vector3 offset = Vector3::zero();
    Matrix pose;
    BW::string name;
    ~Source() {
        Manager& state = manager();
        if (!global && state.sound && epoch == state.epoch && id != AK_INVALID_GAME_OBJECT)
            AK::SoundEngine::UnregisterGameObj(id);
    }
};
struct Voice {
    std::uint64_t token = 0, epoch = 0;
    SourcePtr source;
    BW::string event;
    AkPlayingID playingID = AK_INVALID_PLAYING_ID;
    bool playing = false, notify = true;
    PyObjectPtr weakOwner;
};
struct SoundObjectWrapper { PyObject_HEAD SourcePtr* source; };
struct SoundWrapper {
    PyObject_HEAD
    VoicePtr* voice;
    PyObject* callback;
    PyObject* weakrefs;
};
PyTypeObject soundType = { PyObject_HEAD_INIT(NULL) 0 };
PyTypeObject objectType = { PyObject_HEAD_INIT(NULL) 0 };

bool sourceValid(const SourcePtr& source) {
    const auto& state = manager();
    return source && state.ready && source->epoch == state.epoch &&
        source->id != AK_INVALID_GAME_OBJECT;
}
SourcePtr createSource(PyObject* name, MatrixProviderPtr matrix, const Vector3& position,
    bool auxSend) {
    Manager& state = manager();
    SourcePtr source(new Source);
    source->epoch = state.epoch;
    source->matrix = matrix;
    source->offset = position;
    source->auxSend = auxSend;
    source->pose.setIdentity();
    if (name && PyString_Check(name)) source->name = PyString_AsString(name);
    // Native factories distinguish the Python string type, including an
    // empty string, from the non-string sentinel selecting the global object.
    source->global = !(name && PyString_Check(name));
    if (!state.ready) return source;
    source->id = source->global ? state.global : state.nextObject++;
    if (!source->global) {
        if (!result(AK::SoundEngine::RegisterGameObj(source->id, source->name.c_str()),
            "RegisterGameObj", source->name.c_str())) {
            source->id = AK_INVALID_GAME_OBJECT;
            return source;
        }
        if (matrix) matrix->matrix(source->pose);
        if (!sourceValid(source)) return source;
        AK::SoundEngine::SetPosition(source->id, transform(source->pose, position));
        AkAuxSendValue send;
        send.listenerID = state.listener;
        send.auxBusID = state.auxiliaryBus;
        send.fControlValue = state.auxiliaryGain;
        // Original creation writes even a zero bus/gain; dynamic updates below
        // use an empty send list when the real environment clears its bus.
        const auto sent = AK::SoundEngine::SetGameObjectAuxSendValues(source->id, &send, 1);
        if (state.auxiliaryBus) result(sent, "SetGameObjectAuxSendValues");
        state.sources[source->id] = source;
    }
    return source;
}

void AK_CALLBACK ended(AkCallbackType type, AkCallbackInfo* info) {
    if (type != AK_EndOfEvent) return;
    const auto* cookie = static_cast<const Voice*>(info->pCookie);
    const auto* event = static_cast<AkEventCallbackInfo*>(info);
    Manager& state = manager();
    // No Python, MatrixProvider, or shared-owner mutation on the audio thread.
    std::lock_guard<std::mutex> lock(state.completionsMutex);
    state.completions.emplace_back(cookie->token, event->playingID);
}
bool play(const VoicePtr& voice, PyObject* owner = NULL) {
    if (!voice || voice->playing || voice->event.empty() || !sourceValid(voice->source)) return false;
    Manager& state = manager();
    voice->epoch = state.epoch;
    voice->token = state.nextVoice++;
    voice->notify = true;
    if (owner) {
        voice->weakOwner = PyObjectPtr(PyWeakref_NewRef(owner, NULL), PyObjectPtr::STEAL_REFERENCE);
        if (!voice->weakOwner) return false;
    }
    // Publish the cookie owner before PostEvent: a hot event can finish on an
    // audio thread before PostEvent has returned its playing ID.
    state.voices[voice->token] = voice;
    voice->playingID = AK::SoundEngine::PostEvent(voice->event.c_str(), voice->source->id,
        AK_EndOfEvent, ended, voice.get());
    voice->playing = voice->playingID != AK_INVALID_PLAYING_ID;
    if (!voice->playing) {
        state.voices.erase(voice->token);
        ERROR_MSG("StockAudio: PostEvent failed event=%s object=%llu\n",
            voice->event.c_str(), static_cast<unsigned long long>(voice->source->id));
    }
    return voice->playing;
}
void stop(const VoicePtr& voice) {
    if (!voice || !voice->playing) return;
    voice->notify = false; // original explicit stop does not deliver natural-end callback
    if (manager().sound && voice->epoch == manager().epoch)
        AK::SoundEngine::StopPlayingID(voice->playingID, 0, AkCurveInterpolation_Linear);
    voice->playing = false;
}
void updateSources() {
    Manager& state = manager();
    const auto epoch = state.epoch;
    std::vector<SourcePtr> snapshot;
    for (auto it = state.sources.begin(); it != state.sources.end();) {
        SourcePtr source = it->second.lock();
        if (!source) it = state.sources.erase(it);
        else { snapshot.push_back(source); ++it; }
    }
    for (const auto& source : snapshot) {
        MatrixProviderPtr provider = source->matrix;
        if (!provider || !sourceValid(source)) continue;
        Matrix pose;
        provider->matrix(pose);
        if (!state.ready || state.epoch != epoch) return;
        source->pose = pose;
        AK::SoundEngine::SetPosition(source->id, transform(pose, source->offset));
    }
}
void deliverCallbacks() {
    Manager& state = manager();
    std::vector<std::pair<std::uint64_t, AkPlayingID>> completed;
    { std::lock_guard<std::mutex> lock(state.completionsMutex); completed.swap(state.completions); }
    const auto epoch = state.epoch;
    for (const auto& event : completed) {
        const auto found = state.voices.find(event.first);
        if (found == state.voices.end()) continue;
        VoicePtr voice = found->second;
        if (voice->epoch != epoch || voice->playingID != event.second) continue;
        voice->playing = false;
        state.voices.erase(found);
        if (!voice->notify || !voice->weakOwner) continue;
        PyObject* owner = PyWeakref_GetObject(voice->weakOwner.get());
        if (owner == Py_None || !PyObject_TypeCheck(owner, &soundType)) continue;
        PyObjectPtr keep(owner);
        auto* sound = reinterpret_cast<SoundWrapper*>(owner);
        PyObjectPtr callback(sound->callback);
        if (!callback || callback.get() == Py_None) continue;
        PyObjectPtr returned(PyObject_CallFunctionObjArgs(callback.get(), owner, NULL),
            PyObjectPtr::STEAL_REFERENCE);
        if (!returned) {
            ERROR_MSG("StockAudio: PySound::doCallback failed\n");
            PyErr_Print();
        }
        if (!state.ready || state.epoch != epoch) return;
    }
}
void releaseSources() {
    Manager& state = manager();
    for (const auto& entry : state.voices) {
        entry.second->playing = false;
        entry.second->notify = false;
        if (state.sound) AK::SoundEngine::CancelEventCallbackCookie(entry.second.get());
    }
    for (const auto& entry : state.sources) {
        if (SourcePtr source = entry.second.lock()) {
            source->matrix = MatrixProviderPtr();
            source->id = AK_INVALID_GAME_OBJECT;
        }
    }
    state.sources.clear();
}

bool arity(PyObject* args, Py_ssize_t count, const char* name) {
    if (PyTuple_Size(args) == count) return true;
    PyErr_Format(PyExc_TypeError, "%s expects %d arguments", name, int(count));
    return false;
}
void objectDelete(PyObject* self) {
    delete reinterpret_cast<SoundObjectWrapper*>(self)->source;
    PyObject_Del(self);
}
void soundDelete(PyObject* self) {
    auto* wrapper = reinterpret_cast<SoundWrapper*>(self);
    if (wrapper->weakrefs) PyObject_ClearWeakRefs(self);
    Py_XDECREF(wrapper->callback);
    delete wrapper->voice;
    PyObject_Del(self);
}
PyObject* soundPlay(PyObject* self, PyObject* args) {
    if (!arity(args, 0, "play")) return NULL;
    auto* wrapper = reinterpret_cast<SoundWrapper*>(self);
    if ((*wrapper->voice)->playing) Py_RETURN_FALSE;
    if ((*wrapper->voice)->token) {
        VoicePtr fresh(new Voice);
        fresh->source = (*wrapper->voice)->source;
        fresh->event = (*wrapper->voice)->event;
        *wrapper->voice = fresh;
    }
    return PyBool_FromLong(play(*wrapper->voice, self));
}
PyObject* soundStop(PyObject* self, PyObject* args) {
    if (!arity(args, 0, "stop")) return NULL;
    stop(*reinterpret_cast<SoundWrapper*>(self)->voice);
    Py_RETURN_NONE;
}
PyObject* soundRestart(PyObject* self, PyObject* args) {
    if (!arity(args, 0, "restart")) return NULL;
    auto* wrapper = reinterpret_cast<SoundWrapper*>(self);
    stop(*wrapper->voice);
    VoicePtr fresh(new Voice);
    fresh->source = (*wrapper->voice)->source;
    fresh->event = (*wrapper->voice)->event;
    *wrapper->voice = fresh;
    play(fresh, self);
    Py_RETURN_NONE;
}
PyObject* soundCallback(PyObject* self, PyObject* args) {
    if (!arity(args, 1, "setCallback")) return NULL;
    auto* wrapper = reinterpret_cast<SoundWrapper*>(self);
    PyObject* next = PyTuple_GET_ITEM(args, 0);
    Py_INCREF(next); Py_XDECREF(wrapper->callback); wrapper->callback = next;
    Py_RETURN_NONE;
}
PyObject* soundIsPlaying(PyObject* self, void*) {
    const auto& voice = *reinterpret_cast<SoundWrapper*>(self)->voice;
    return PyBool_FromLong(voice && voice->playing && sourceValid(voice->source));
}
PyObject* soundReleaseMatrix(PyObject* self, PyObject* args) {
    if (!arity(args, 0, "releaseMatrix")) return NULL;
    const auto& voice = *reinterpret_cast<SoundWrapper*>(self)->voice;
    if (voice && voice->source) voice->source->matrix = MatrixProviderPtr();
    Py_RETURN_NONE;
}
SourcePtr sourceOf(PyObject* self, bool sound) {
    if (!sound) return *reinterpret_cast<SoundObjectWrapper*>(self)->source;
    const auto& voice = *reinterpret_cast<SoundWrapper*>(self)->voice;
    return voice ? voice->source : SourcePtr();
}
PyObject* setRTPC(PyObject* self, PyObject* args, bool sound) {
    BW::string name; float value;
    if (!arity(args, 2, "setRTPC") ||
        Script::setData(PyTuple_GET_ITEM(args, 0), name) ||
        Script::setData(PyTuple_GET_ITEM(args, 1), value)) return NULL;
    SourcePtr source = sourceOf(self, sound);
    if (sourceValid(source))
        result(AK::SoundEngine::SetRTPCValue(name.c_str(), value, source->id), "SetRTPCValue", name.c_str());
    Py_RETURN_NONE;
}
PyObject* setSwitch(PyObject* self, PyObject* args, bool sound) {
    BW::string group, value;
    if (!arity(args, 2, "setSwitch") ||
        Script::setData(PyTuple_GET_ITEM(args, 0), group) ||
        Script::setData(PyTuple_GET_ITEM(args, 1), value)) return NULL;
    SourcePtr source = sourceOf(self, sound);
    if (sourceValid(source))
        result(AK::SoundEngine::SetSwitch(group.c_str(), value.c_str(), source->id), "SetSwitch", group.c_str());
    Py_RETURN_NONE;
}
PyObject* soundRTPC(PyObject* self, PyObject* args) { return setRTPC(self, args, true); }
PyObject* soundSwitch(PyObject* self, PyObject* args) { return setSwitch(self, args, true); }
PyObject* objectRTPC(PyObject* self, PyObject* args) { return setRTPC(self, args, false); }
PyObject* objectSwitch(PyObject* self, PyObject* args) { return setSwitch(self, args, false); }
PyObject* objectPlay(PyObject* self, PyObject* args) {
    BW::string event;
    if (!arity(args, 1, "play") || Script::setData(PyTuple_GET_ITEM(args, 0), event)) return NULL;
    VoicePtr voice(new Voice);
    voice->event = event; voice->source = sourceOf(self, false);
    play(voice);
    // The original wrapper's bool denotes accepted string argument shape.
    Py_RETURN_TRUE;
}
PyObject* objectStopAll(PyObject* self, PyObject* args) {
    if (!arity(args, 0, "stopAll")) return NULL;
    SourcePtr source = sourceOf(self, false);
    if (sourceValid(source)) {
        for (const auto& entry : manager().voices)
            if (entry.second->source == source) stop(entry.second);
        AK::SoundEngine::StopAll(source->id);
    }
    Py_RETURN_NONE;
}
PyObject* objectMatrix(PyObject* self, void*) {
    SourcePtr source = sourceOf(self, false);
    return source ? Script::getData(source->matrix) : Script::getData(MatrixProviderPtr());
}
int objectSetMatrix(PyObject* self, PyObject* value, void*) {
    MatrixProviderPtr matrix;
    if (!value || Script::setData(value, matrix)) return -1;
    sourceOf(self, false)->matrix = matrix;
    return 0;
}

PyMethodDef soundMethods[] = {
    {"play", soundPlay, METH_VARARGS, NULL}, {"stop", soundStop, METH_VARARGS, NULL},
    {"restart", soundRestart, METH_VARARGS, NULL}, {"setCallback", soundCallback, METH_VARARGS, NULL},
    {"setRTPC", soundRTPC, METH_VARARGS, NULL}, {"setSwitch", soundSwitch, METH_VARARGS, NULL},
    {"releaseMatrix", soundReleaseMatrix, METH_VARARGS, NULL}, {NULL, NULL, 0, NULL}};
PyMethodDef objectMethods[] = {
    {"play", objectPlay, METH_VARARGS, NULL}, {"stopAll", objectStopAll, METH_VARARGS, NULL},
    {"setRTPC", objectRTPC, METH_VARARGS, NULL}, {"setSwitch", objectSwitch, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}};
PyGetSetDef soundAttributes[] = {
    {const_cast<char*>("isPlaying"), soundIsPlaying, NULL, NULL, NULL},
    {NULL, NULL, NULL, NULL, NULL}};
PyGetSetDef objectAttributes[] = {
    {const_cast<char*>("matrixProvider"), objectMatrix, objectSetMatrix, NULL, NULL},
    {NULL, NULL, NULL, NULL, NULL}};
bool typesReady() {
    if (!soundType.tp_name) {
        soundType.tp_name = const_cast<char*>("PySound");
        soundType.tp_basicsize = sizeof(SoundWrapper);
        soundType.tp_flags = Py_TPFLAGS_DEFAULT;
        soundType.tp_dealloc = soundDelete;
        soundType.tp_methods = soundMethods;
        soundType.tp_getset = soundAttributes;
        soundType.tp_weaklistoffset = offsetof(SoundWrapper, weakrefs);
        objectType.tp_name = const_cast<char*>("PySoundObject");
        objectType.tp_basicsize = sizeof(SoundObjectWrapper);
        objectType.tp_flags = Py_TPFLAGS_DEFAULT;
        objectType.tp_dealloc = objectDelete;
        objectType.tp_methods = objectMethods;
        objectType.tp_getset = objectAttributes;
    }
    return PyType_Ready(&soundType) >= 0 && PyType_Ready(&objectType) >= 0;
}
PyObject* soundObject(const VoicePtr& voice, PyObject* callback = NULL) {
    if (!typesReady()) return NULL;
    auto* object = PyObject_New(SoundWrapper, &soundType);
    if (!object) return NULL;
    object->voice = new VoicePtr(voice);
    object->weakrefs = NULL;
    object->callback = callback;
    Py_XINCREF(callback);
    return reinterpret_cast<PyObject*>(object);
}
VoicePtr voiceFor(PyObject* event, PyObject* name, MatrixProviderPtr matrix,
    const Vector3& position) {
    VoicePtr voice(new Voice);
    if (event && PyString_Check(event)) {
        voice->event = PyString_AsString(event);
        voice->source = createSource(name, matrix, position, false);
    }
    return voice;
}
VoicePtr cameraVoice(PyObject* event, const Vector3& target) {
    Manager& state = manager();
    Vector3 direction = target - state.camera.applyToOrigin();
    direction.y = 0.f;
    D3DXVec3Normalize(reinterpret_cast<D3DXVECTOR3*>(&direction),
        reinterpret_cast<const D3DXVECTOR3*>(&direction));
    const Vector3 position = state.camera.applyToOrigin() + direction * 40.f;
    VoicePtr voice = voiceFor(event, event, MatrixProviderPtr(), position);
    if (voice->source && sourceValid(voice->source)) {
        Source& source = *voice->source;
        source.pose(0, 0) = -direction.z;
        source.pose(0, 2) = direction.x;
        source.pose(2, 0) = -direction.x;
        source.pose(2, 2) = -direction.z;
        result(AK::SoundEngine::SetPosition(source.id, transform(source.pose, position)),
            "SetPosition", "camera-oriented source");
    }
    return voice;
}
PyObject* exportedObjectType() {
    if (!typesReady()) return NULL;
    Py_INCREF(&objectType);
    return reinterpret_cast<PyObject*>(&objectType);
}
PyModuleResultLink objectTypeLink("_WWISE", "SoundObject", exportedObjectType);
}

PyObject* py_WW_isInitialised(PyObject* args) {
    if (!StockAudioDetail::arity(args, 0, "WW_isInitialised")) return NULL;
    return PyBool_FromLong(stockAudioReady());
}
PY_MODULE_FUNCTION(WW_isInitialised, _WWISE)

PyObject* py_WG_loadSoundBank(PyObject* args) {
    if (!StockAudioDetail::arity(args, 1, "WG_loadSoundBank")) return NULL;
    PyObject* name = PyTuple_GET_ITEM(args, 0);
    if (PyString_Check(name)) StockAudioDetail::loadBank(PyString_AsString(name));
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(WG_loadSoundBank, _WWISE)
PyObject* py_WG_unLoadSoundBank(PyObject* args) {
    if (!StockAudioDetail::arity(args, 1, "WG_unLoadSoundBank")) return NULL;
    PyObject* name = PyTuple_GET_ITEM(args, 0);
    if (PyString_Check(name)) StockAudioDetail::unloadBank(PyString_AsString(name));
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(WG_unLoadSoundBank, _WWISE)
PyObject* py_WG_loadLogin(PyObject* args) {
    if (!StockAudioDetail::arity(args, 0, "WG_loadLogin")) return NULL;
    selectStockAudioBankSet("SFX_soundbanks_login");
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(WG_loadLogin, _WWISE)

PyObject* py_WW_getSoundObject(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 4, "WW_getSoundObject") || !typesReady()) return NULL;
    MatrixProviderPtr matrix; Vector3 offset; bool aux;
    if (Script::setData(PyTuple_GET_ITEM(args, 1), matrix) ||
        Script::setData(PyTuple_GET_ITEM(args, 2), offset) ||
        Script::setData(PyTuple_GET_ITEM(args, 3), aux)) return NULL;
    auto* object = PyObject_New(SoundObjectWrapper, &objectType);
    if (!object) return NULL;
    PyObject* name = PyTuple_GET_ITEM(args, 0);
    // Unlike WW_getSound's non-string global-name sentinel, the original
    // WW_getSoundObject factory returns an invalid handle for this input.
    object->source = new SourcePtr(PyString_Check(name) ?
        createSource(name, matrix, offset, aux) : SourcePtr(new Source));
    return reinterpret_cast<PyObject*>(object);
}
PY_MODULE_FUNCTION(WW_getSoundObject, _WWISE)
PyObject* py_WW_getSound(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 4, "WW_getSound")) return NULL;
    MatrixProviderPtr matrix; Vector3 offset;
    if (Script::setData(PyTuple_GET_ITEM(args, 2), matrix) ||
        Script::setData(PyTuple_GET_ITEM(args, 3), offset)) return NULL;
    return soundObject(voiceFor(PyTuple_GET_ITEM(args, 0), PyTuple_GET_ITEM(args, 1), matrix, offset));
}
PY_MODULE_FUNCTION(WW_getSound, _WWISE)
PyObject* py_WW_getSoundCallback(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 4, "WW_getSoundCallback")) return NULL;
    MatrixProviderPtr matrix;
    if (Script::setData(PyTuple_GET_ITEM(args, 2), matrix)) return NULL;
    // #1513 0x0123c070 requires an actual MatrixProvider for the callback
    // variant. Its invalid-input sentinel is an inert PySound, not a global
    // sound which could unexpectedly deliver a completion callback.
    if (!matrix) return soundObject(VoicePtr(new Voice));
    return soundObject(voiceFor(PyTuple_GET_ITEM(args, 0), PyTuple_GET_ITEM(args, 1),
        matrix, Vector3::zero()), PyTuple_GET_ITEM(args, 3));
}
PY_MODULE_FUNCTION(WW_getSoundCallback, _WWISE)
PyObject* py_WW_getSoundPos(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 3, "WW_getSoundPos")) return NULL;
    Vector3 position;
    if (Script::setData(PyTuple_GET_ITEM(args, 2), position)) return NULL;
    return soundObject(voiceFor(PyTuple_GET_ITEM(args, 0), PyTuple_GET_ITEM(args, 1),
        MatrixProviderPtr(), position));
}
PY_MODULE_FUNCTION(WW_getSoundPos, _WWISE)
PyObject* py_WW_getCameraOriented(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 2, "WW_getCameraOriented")) return NULL;
    Vector3 position;
    if (Script::setData(PyTuple_GET_ITEM(args, 1), position)) return NULL;
    return soundObject(cameraVoice(PyTuple_GET_ITEM(args, 0), position));
}
PY_MODULE_FUNCTION(WW_getCameraOriented, _WWISE)
PyObject* py_WW_playCameraOriented(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 2, "WW_playCameraOriented")) return NULL;
    Vector3 position;
    if (Script::setData(PyTuple_GET_ITEM(args, 1), position)) return NULL;
    play(cameraVoice(PyTuple_GET_ITEM(args, 0), position));
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(WW_playCameraOriented, _WWISE)

PyObject* py_WW_eventGlobal(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 1, "WW_eventGlobal")) return NULL;
    PyObject* event = PyTuple_GET_ITEM(args, 0);
    if (!PyString_Check(event)) Py_RETURN_FALSE;
    play(voiceFor(event, Py_None, MatrixProviderPtr(), Vector3::zero()));
    Py_RETURN_TRUE;
}
PY_MODULE_FUNCTION(WW_eventGlobal, _WWISE)
PyObject* py_WW_eventGlobalSync(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 1, "WW_eventGlobalSync")) return NULL;
    PyObject* event = PyTuple_GET_ITEM(args, 0);
    if (!PyString_Check(event)) Py_RETURN_FALSE;
    if (stockAudioReady()) {
        const auto id = AK::SoundEngine::GetIDFromString(PyString_AsString(event));
        if (id) {
            Manager& state = manager();
            state.pendingEvents.push_back(id);
            state.persistentEvents.erase(std::remove(state.persistentEvents.begin(),
                state.persistentEvents.end(), id), state.persistentEvents.end());
            state.persistentEvents.push_back(id);
        }
    }
    Py_RETURN_TRUE;
}
PY_MODULE_FUNCTION(WW_eventGlobalSync, _WWISE)
PyObject* py_WW_eventGlobalPos(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 2, "WW_eventGlobalPos")) return NULL;
    PyObject* event = PyTuple_GET_ITEM(args, 0);
    Vector3 position;
    if (Script::setData(PyTuple_GET_ITEM(args, 1), position)) return NULL;
    if (PyString_Check(event)) play(voiceFor(event, event, MatrixProviderPtr(), position));
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(WW_eventGlobalPos, _WWISE)

PyObject* py_WW_setRTCPGlobal(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 2, "WW_setRTCPGlobal")) return NULL;
    PyObject* name = PyTuple_GET_ITEM(args, 0);
    PyObject* value = PyTuple_GET_ITEM(args, 1);
    if (!PyString_Check(name) || (!PyFloat_Check(value) && !PyInt_Check(value))) Py_RETURN_FALSE;
    const float number = static_cast<float>(PyFloat_AsDouble(value));
    if (PyErr_Occurred()) return NULL;
    if (stockAudioReady()) result(AK::SoundEngine::SetRTPCValue(PyString_AsString(name), number),
        "SetRTPCValue", PyString_AsString(name));
    Py_RETURN_TRUE;
}
PY_MODULE_FUNCTION(WW_setRTCPGlobal, _WWISE)
PyObject* py_WW_setRTPCBus(PyObject* args) {
    // Both exact wrappers forward to the same native global RTPC method.
    return py_WW_setRTCPGlobal(args);
}
PY_MODULE_FUNCTION(WW_setRTPCBus, _WWISE)
PyObject* py_WW_setMasterVolume(PyObject* args) {
    using namespace StockAudioDetail;
    float value;
    if (!arity(args, 1, "WW_setMasterVolume") || Script::setData(PyTuple_GET_ITEM(args, 0), value))
        return NULL;
    manager().masterVolume = value; // original setter writes current and target together
    if (stockAudioReady()) result(AK::SoundEngine::SetRTPCValue("RTPC_ext_menu_volume_master", value * 100.f),
        "SetRTPCValue", "RTPC_ext_menu_volume_master");
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(WW_setMasterVolume, _WWISE)
PyObject* py_WW_setState(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 2, "WW_setState")) return NULL;
    PyObject* group = PyTuple_GET_ITEM(args, 0);
    PyObject* value = PyTuple_GET_ITEM(args, 1);
    if (!PyString_Check(group) || !PyString_Check(value)) Py_RETURN_FALSE;
    if (stockAudioReady()) result(AK::SoundEngine::SetState(PyString_AsString(group), PyString_AsString(value)),
        "SetState", PyString_AsString(group));
    Py_RETURN_TRUE;
}
PY_MODULE_FUNCTION(WW_setState, _WWISE)
PyObject* py_WW_setSwitch(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 2, "WW_setSwitch")) return NULL;
    PyObject* group = PyTuple_GET_ITEM(args, 0);
    PyObject* value = PyTuple_GET_ITEM(args, 1);
    if (!PyString_Check(group) || !PyString_Check(value)) Py_RETURN_FALSE;
    if (stockAudioReady()) result(AK::SoundEngine::SetSwitch(PyString_AsString(group),
        PyString_AsString(value), manager().global), "SetSwitch", PyString_AsString(group));
    Py_RETURN_TRUE;
}
PY_MODULE_FUNCTION(WW_setSwitch, _WWISE)

PyObject* py_WWsetCameraShift(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 1, "WWsetCameraShift")) return NULL;
    MatrixProviderPtr provider;
    if (Script::setData(PyTuple_GET_ITEM(args, 0), provider)) return NULL;
    manager().cameraShift = provider;
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(WWsetCameraShift, _WWISE)
PyObject* py_setLanguage(PyObject* args) {
    using namespace StockAudioDetail;
    if (!arity(args, 1, "setLanguage")) return NULL;
    BW::string language;
    if (Script::setData(PyTuple_GET_ITEM(args, 0), language)) return NULL;
    if (stockAudioReady()) {
        std::vector<BW::string> names;
        if (!bankNames("voice_soundbanks", names)) {
            PyErr_SetString(PyExc_RuntimeError, "Missing authored voice_soundbanks group");
            return NULL;
        }
        for (const auto& name : names) unloadBank(name);
        BW::wstring wide;
        if (!bw_utf8tow(language, wide)) {
            PyErr_SetString(PyExc_ValueError, "Invalid UTF-8 audio language");
            return NULL;
        }
        if (result(AK::StreamMgr::SetCurrentLanguage(wide.c_str()), "SetCurrentLanguage", language.c_str()))
            for (const auto& name : names) loadBank(name);
    }
    Py_RETURN_TRUE;
}
PY_MODULE_FUNCTION(setLanguage, _WWISE)

bool setStockAudioAuxiliarySend(unsigned busID, float gain) {
    using namespace StockAudioDetail;
    Manager& state = manager();
    if (!state.ready) return false;
    state.auxiliaryBus = busID;
    state.auxiliaryGain = gain;
    AkAuxSendValue send;
    send.listenerID = state.listener;
    send.auxBusID = busID;
    send.fControlValue = gain;
    bool accepted = true;
    for (const auto& entry : state.sources) {
        SourcePtr source = entry.second.lock();
        if (source && source->auxSend && sourceValid(source))
            accepted = result(AK::SoundEngine::SetGameObjectAuxSendValues(source->id,
                &send, busID ? 1u : 0u), "SetGameObjectAuxSendValues") && accepted;
    }
    return accepted;
}

BW_END_NAMESPACE
