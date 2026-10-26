// Real Win32 BigWorld resource-owner probe. All inputs are generated fixtures.
#include "cstdmf/cstdmf_windows.hpp"
#include "cstdmf/allocator.hpp"
#include "cstdmf/log_msg.hpp"
#include "resmgr/bwresource.hpp"
#include "resmgr/multi_file_system.hpp"
#include "resmgr/zip_file_system.hpp"
#include "resmgr/zip_section.hpp"

#include <cstdio>
#include <cstring>
#include <exception>
#include <iostream>
#include <stdexcept>

namespace {
const char* originalName = "shaders/fixture/original.bin";
const char* directoryName = "directory/protected.bin";
const char* originalBytes = "ORIGINAL_ARCHIVE_RESOURCE\n";
const char* directoryBytes = "ORIGINAL_DIRECTORY_RESOURCE\n";
const char* parallelBytes = "PARALLEL_DIRECTORY_RESOURCE\n";

void require(bool accepted, const char* message) {
    if (!accepted) throw std::runtime_error(message);
}
BW::BinaryPtr bytes(const char* value) {
    return new BW::BinaryBlock(value, std::strlen(value), "BinaryBlock/ResourceOverlayProbe");
}
bool equal(BW::BinaryPtr first, BW::BinaryPtr second) {
    return first && second && first->len() == second->len() &&
        std::memcmp(first->cdata(), second->cdata(), static_cast<size_t>(first->len())) == 0;
}
BW::string childPath(const BW::string& root, const char* relative) {
    BW::string result = root;
    if (!result.empty() && result[result.size() - 1] != '/' && result[result.size() - 1] != '\\')
        result += '/';
    result += relative;
    return result;
}
void sameLocation(const BW::IFileSystem::FileDataLocation& before,
    const BW::IFileSystem::FileDataLocation& after) {
    require(before.hostResourceID == after.hostResourceID && before.offset == after.offset,
        "A rejected mutation changed the mounted resource location");
}
void checkOriginals(BW::MultiFileSystem* files, BW::ZipFileSystemPtr archive,
    const BW::string& package, const BW::string& source, BW::BinaryPtr packageBefore,
    const BW::IFileSystem::FileDataLocation& locationBefore) {
    BW::IFileSystem* native = BW::BWResource::instance().nativeFileSystem();
    require(equal(native->readFile(package), packageBefore), "Generated source package bytes changed");
    require(equal(archive->readFile(originalName), bytes(originalBytes)),
        "Direct mounted archive index or bytes changed");
    require(equal(files->readFile(originalName), bytes(originalBytes)),
        "Root mounted archive index or bytes changed");
    BW::IFileSystem::FileDataLocation after;
    require(files->locateFileData(originalName, &after), "Original resource can no longer be located");
    sameLocation(locationBefore, after);
    require(equal(native->readFile(childPath(source, originalName)), bytes(parallelBytes)),
        "A resource write fell through into the original directory root");
    require(equal(native->readFile(childPath(source, directoryName)), bytes(directoryBytes)),
        "A POSIX write fell through into the original directory root");
}

void rejectArchiveMutations(BW::ZipFileSystemPtr archive) {
    BW::IFileSystem::FileDataLocation before, after;
    require(archive->locateFileData(originalName, &before), "Cannot locate direct archive fixture");
    BW::IFileSystem::FileInfo infoBefore, infoAfter;
    require(archive->getFileType(originalName, &infoBefore) == BW::IFileSystem::FT_FILE,
        "Fixture member must be a file");
    BW::BinaryPtr changed = bytes("REJECTED RESOURCE CONTENT\n");
    require(!archive->writeFile(originalName, changed, true), "Mounted archive accepted writeFile");
    require(!archive->writeFileIfExists(originalName, changed, true),
        "Mounted archive accepted writeFileIfExists");
    require(!archive->writeToStructure(originalName, changed), "Mounted archive accepted an index write");
    require(!archive->eraseFileOrDirectory(originalName), "Mounted archive accepted erase");
    require(archive->posixFileOpen(originalName, "r+") == NULL, "Mounted archive accepted r+ open");
    require(!archive->createZip(), "Mounted archive accepted mutable ZIP serialization");
    archive->clear("shaders/fixture");
    // A public section borrowing the mounted ZIP must not bypass the guard.
    {
        BW::DataSectionPtr borrowed = new BW::ZipSection(archive, "shaders/fixture");
        require(borrowed->writeBinary("original.bin", changed),
            "Cannot stage a mutation through a borrowed public ZipSection");
        require(!borrowed->asBinary(), "Borrowed ZipSection serialized a mutable source archive");
    }
    require(equal(archive->readFile(originalName), bytes(originalBytes)),
        "Rejected direct archive mutations changed the read result");
    require(archive->getFileType(originalName, &infoAfter) == BW::IFileSystem::FT_FILE &&
        infoBefore.size == infoAfter.size, "Rejected archive mutations changed file metadata");
    require(archive->locateFileData(originalName, &after), "Rejected mutation removed a fixture entry");
    sameLocation(before, after);
    std::cout << "Real mounted ZIP mutation guards preserve reads, metadata and stored offsets" << std::endl;
}

void rejectWriteFallback(BW::MultiFileSystem* files) {
    BW::BinaryPtr changed = bytes("MUST STAY PRIVATE\n");
    BW::BWResource::ensurePathExists(originalName);
    BW::BWResource::ensurePathExists(directoryName);
    // A directory at a file destination is a real, deterministic Win32 write
    // failure, including under an administrator account. No ACL fake is needed.
    require(files->makeDirectory(originalName), "Cannot create private write blocker");
    require(files->makeDirectory(directoryName), "Cannot create private POSIX blocker");
    require(!files->writeFile(originalName, changed, true), "Failed private write tried a later root");
    require(!files->writeFileIfExists(originalName, changed, true),
        "Failed private conditional write tried a later root");
    const char* modes[] = {"wb", "a+b", "r+"};
    for (size_t index = 0; index < sizeof(modes) / sizeof(modes[0]); ++index)
        require(files->posixFileOpen(directoryName, modes[index]) == NULL,
            "Failed private POSIX open tried a later root");
    require(files->eraseFileOrDirectory(originalName), "Cannot remove private write blocker");
    require(files->eraseFileOrDirectory(directoryName), "Cannot remove private POSIX blocker");
    require(!files->eraseFileOrDirectory(originalName), "Erase fell through into a source root");
    require(!files->copyFileOrDirectory(directoryName, "directory/copied.bin"),
        "Copy fell through into a source root");
    require(!files->moveFileOrDirectory(directoryName, "directory/moved.bin"),
        "Move fell through into a source root");
    require(files->writeFile("directory-blocker", changed, true), "Cannot create private mkdir blocker");
    require(!files->makeDirectory("directory-blocker"), "mkdir fell through into a source root");
    require(files->eraseFileOrDirectory("directory-blocker"), "Cannot remove private mkdir blocker");
    // Pure reads must retain the full, authored search order.
    FILE* input = files->posixFileOpen(directoryName, "rb");
    require(input != NULL, "Read-only POSIX open did not search the source root");
    char buffer[128] = {};
    const size_t count = std::fread(buffer, 1, sizeof(buffer), input);
    const int closed = std::fclose(input);
    require(count == std::strlen(directoryBytes) && closed == 0 &&
        std::memcmp(buffer, directoryBytes, count) == 0, "Read-only POSIX open returned different bytes");
    std::cout << "Real MultiFileSystem write failures never fall through; read-only lookup order is preserved" << std::endl;
}

void roundTripCache(BW::MultiFileSystem* files, const char* name,
    const char* effect, const char* hash) {
    BW::vector<BW::DataSectionPtr> emptyChildren;
    BW::DataSectionPtr cache = new BW::ZipSection(name, files, &emptyChildren);
    BW::DataSectionPtr effectNode = cache->openSection("effect", true);
    BW::DataSectionPtr hashNode = cache->openSection("hash", true);
    require(effectNode && hashNode, "Cannot create detached cache sections");
    effectNode->setParent(cache);
    hashNode->setParent(cache);
    require(cache->writeBinary("effect", bytes(effect)) && cache->writeBinary("hash", bytes(hash)),
        "Cannot populate detached cache sections");
    BW::BinaryPtr encoded = cache->asBinary();
    effectNode->setParent(NULL);
    hashNode->setParent(NULL);
    cache = NULL;
    require(encoded && BW::ZipFileSystem::zipTest(encoded), "Detached cache did not serialize as ZIP");
    require(files->writeFile(name, encoded, true), "Cannot save the detached cache into the private overlay");
    require(equal(files->readFile(name), encoded), "Private cache bytes differ from detached serialization");
    BW::IFileSystem::FileDataLocation saved;
    require(files->locateFileData(name, &saved) && saved.offset == 0,
        "Private cache must resolve to a standalone native file");
    BW::BWResource::instance().purge(name, true);
    BW::DataSectionPtr reopened = BW::BWResource::openSection(name);
    require(reopened && equal(reopened->readBinary("effect"), bytes(effect)) &&
        equal(reopened->readBinary("hash"), bytes(hash)), "Saved cache cannot be reopened through BWResource");
    // Also exercise the exact public ZipSection loading constructor directly.
    BW::DataSectionPtr direct = new BW::ZipSection(name, files);
    require(equal(direct->readBinary("effect"), bytes(effect)) &&
        equal(direct->readBinary("hash"), bytes(hash)), "Saved cache cannot be reopened as a native ZipSection");
    std::cout << "Detached ZipSection effect/hash serialization and private cache reopen passed: " << name << std::endl;
}

void exercise(const BW::string& overlay, const BW::string& package, const BW::string& source) {
    BW::vector<BW::string> paths;
    paths.push_back(overlay);
    paths.push_back(package);
    paths.push_back(source);
    require(BW::BWResource::init(paths), "BWResource::init failed");
    BW::MultiFileSystem* files = BW::BWResource::instance().fileSystem();
    BW::BinaryPtr packageBefore = BW::BWResource::instance().nativeFileSystem()->readFile(package);
    require(packageBefore && packageBefore->len() > 0, "Cannot snapshot generated source package");
    BW::IFileSystem::FileDataLocation before;
    require(files->locateFileData(originalName, &before) && before.offset > 0,
        "Fixture member must be ZIP_STORED at a nonzero package offset");
    BW::ZipFileSystemPtr archive = new BW::ZipFileSystem(package);
    rejectArchiveMutations(archive);
    rejectWriteFallback(files);
    checkOriginals(files, archive, package, source, packageBefore, before);
    roundTripCache(files, "shaders/fixture/runtime.1.fxo", "GENERATED EFFECT ONE", "GENERATED HASH ONE");
    roundTripCache(files, "terrain/shaders/runtime.7.fxo", "GENERATED EFFECT TWO", "GENERATED HASH TWO");
    checkOriginals(files, archive, package, source, packageBefore, before);
    std::cout << "Real resource overlay ownership probe passed; generated source package remains unchanged" << std::endl;
}
}

int main(int argc, char** argv) {
    BW_SYSTEMSTAGE_MAIN();
    ::SetEnvironmentVariableA("BW_UNATTENDED_MACHINE", "TRUE");
    ::SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    BW::LogMsg::automatedTest(true);
    if (argc < 4) {
        std::cerr << "Usage: probe_resource_write_overlay <private-overlay-dir> <generated.pkg> <source-dir> [-unattended]" << std::endl;
        return 2;
    }
    int outcome = 1;
    try {
        exercise(argv[1], argv[2], argv[3]);
        outcome = 0;
    } catch (const std::exception& error) {
        std::cerr << "Resource overlay probe failed: " << error.what() << std::endl;
    }
    BW::BWResource::fini();
    BW::LogMsg::fini();
    return outcome;
}
