"""Install the optional Bot chat model and inference runtime.

The model is the largest download this project has ever asked for, over a
route that is slow for many of its players, so every part of this is built
around being interrupted: progress is reported continuously, a cancel is
honoured within one chunk, a partial file is resumed rather than restarted,
and nothing is accepted without matching the digest pinned beside it.

The catalogue itself lives with the server, in ``bot_chat_models``. This
module never restates a URL, size or digest; it is handed the entry.

Nothing here imports a GUI. The launcher window drives it from a worker
thread, and the tests drive it with an injected opener.
"""

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile


CHAT_DIR = "bot-chat"
MODEL_DIR = "models"
RUNTIME_DIR = "runtime"
PART_SUFFIX = ".part"

CHUNK_BYTES = 1 << 20
DOWNLOAD_TIMEOUT_SECONDS = 60.0
# A pinned entry states its own size, so anything materially larger is a
# wrong or hostile response rather than a big model.
SIZE_SLACK_BYTES = 1 << 20

RUNTIME_EXECUTABLE = "llama-server.exe"


class InstallError(Exception):
    """One download or extraction could not be completed."""


class InstallCancelled(Exception):
    """The player stopped the install; a partial file is kept for resume."""


def _default_base_dir():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "WoTOfflineBattles")


def install_root(base_dir=None):
    """Return the directory holding everything this feature downloads."""
    return os.path.join(base_dir or _default_base_dir(), CHAT_DIR)


def model_path(entry, base_dir=None):
    """Return where one catalogued model is stored once installed."""
    return os.path.join(install_root(base_dir), MODEL_DIR, entry["file"])


def runtime_root(base_dir=None):
    """Return the directory the runtime archive is unpacked into."""
    return os.path.join(install_root(base_dir), RUNTIME_DIR)


def runtime_executable(base_dir=None):
    """Return the generator executable this feature runs."""
    return os.path.join(runtime_root(base_dir), RUNTIME_EXECUTABLE)


def _check_cancel(cancel):
    if cancel is not None and cancel():
        raise InstallCancelled("the install was stopped")


def file_digest(path, cancel=None, progress=None):
    """Return one file's SHA-256, staying interruptible on a large file."""
    digest = hashlib.sha256()
    total = os.path.getsize(path)
    done = 0
    with open(path, "rb") as stream:
        while True:
            _check_cancel(cancel)
            block = stream.read(CHUNK_BYTES)
            if not block:
                break
            digest.update(block)
            done += len(block)
            if progress is not None:
                progress(done, total)
    return digest.hexdigest()


def is_installed(path, entry, cancel=None, progress=None):
    """Return whether a complete, verified copy is already on disk.

    Size is checked first because it rejects almost every bad file without
    reading a gigabyte.
    """
    try:
        if not os.path.isfile(path) or os.path.getsize(path) != entry["size"]:
            return False
    except OSError:
        return False
    try:
        return file_digest(path, cancel, progress) == entry["sha256"]
    except OSError:
        return False


def _open(opener, url, offset):
    request = urllib.request.Request(url)
    if offset:
        request.add_header("Range", "bytes=%d-" % offset)
    return opener(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)


def _response_status(response):
    status = getattr(response, "status", None)
    if status is None:
        getcode = getattr(response, "getcode", None)
        status = getcode() if callable(getcode) else None
    return status


def download_file(url, destination, entry, progress=None, cancel=None,
                  opener=None):
    """Fetch one catalogued file, resuming a partial copy when possible."""
    opener = opener or urllib.request.urlopen
    directory = os.path.dirname(destination)
    if directory:
        os.makedirs(directory, exist_ok=True)
    part = destination + PART_SUFFIX
    total = int(entry["size"])
    offset = 0
    if os.path.isfile(part):
        offset = os.path.getsize(part)
        if offset > total:
            # A partial file longer than the finished one is not this file.
            os.remove(part)
            offset = 0
    if offset == total:
        # The bytes are all there; only the digest is still unproven.
        os.replace(part, destination)
        return _finish(destination, entry, cancel)

    _check_cancel(cancel)
    try:
        response = _open(opener, url, offset)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise InstallError("%s could not be reached: %s" % (url, error))
    try:
        status = _response_status(response)
        if offset and status != 206:
            # The host ignored the range request, so the partial copy is
            # worthless and the whole file arrives from the start.
            offset = 0
        mode = "r+b" if offset else "wb"
        with open(part, mode) as stream:
            stream.seek(offset)
            stream.truncate(offset)
            done = offset
            if progress is not None:
                progress(done, total)
            while True:
                _check_cancel(cancel)
                block = response.read(CHUNK_BYTES)
                if not block:
                    break
                done += len(block)
                if done > total + SIZE_SLACK_BYTES:
                    raise InstallError(
                        "%s returned more data than the catalogue expects"
                        % url)
                stream.write(block)
                if progress is not None:
                    progress(done, total)
    except InstallCancelled:
        raise
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise InstallError("%s could not be downloaded: %s" % (url, error))
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    if os.path.getsize(part) != total:
        raise InstallError(
            "%s ended early; run the install again to resume" % url)
    os.replace(part, destination)
    return _finish(destination, entry, cancel)


def _finish(destination, entry, cancel):
    """Accept a complete file only when its digest matches the catalogue."""
    digest = file_digest(destination, cancel)
    if digest != entry["sha256"]:
        os.remove(destination)
        raise InstallError(
            "the download did not match its published checksum; "
            "nothing was installed")
    return destination


def _sources_for(catalogue, kind, arch=None):
    if kind == "runtime":
        return catalogue.runtime_sources(arch)
    return catalogue.SOURCES


def _download_from_sources(urls, destination, entry, progress, cancel,
                           opener):
    """Try every published host in turn, keeping the first real failure."""
    first_error = None
    for url in urls:
        if url is None:
            continue
        try:
            return download_file(url, destination, entry, progress, cancel,
                                 opener)
        except InstallCancelled:
            raise
        except InstallError as error:
            if first_error is None:
                first_error = error
    raise first_error or InstallError("no download host is published")


def install_model(catalogue, tier_key, base_dir=None, progress=None,
                  cancel=None, opener=None, sources=None):
    """Install one catalogued model, returning its path."""
    entry = catalogue.tier(tier_key)
    if entry is None:
        raise InstallError("unknown model: %s" % tier_key)
    destination = model_path(entry, base_dir)
    if is_installed(destination, entry, cancel):
        return destination
    urls = [catalogue.model_url(tier_key, source)
            for source in (sources or catalogue.SOURCES)]
    return _download_from_sources(urls, destination, entry, progress, cancel,
                                  opener)


def install_runtime(catalogue, arch, base_dir=None, progress=None,
                    cancel=None, opener=None, sources=None):
    """Install the inference runtime, returning the generator executable."""
    entry = catalogue.runtime_asset(arch)
    if entry is None:
        raise InstallError(
            "no inference runtime is published for this machine")
    executable = runtime_executable(base_dir)
    if os.path.isfile(executable):
        return executable
    root = runtime_root(base_dir)
    os.makedirs(root, exist_ok=True)
    archive = os.path.join(root, entry["file"])
    if not is_installed(archive, entry, cancel):
        urls = [catalogue.runtime_url(arch, source)
                for source in (sources or catalogue.runtime_sources(arch))]
        _download_from_sources(urls, archive, entry, progress, cancel, opener)
    try:
        extract_runtime(archive, root, cancel)
    finally:
        # The unpacked files are the installation; the archive is 17 MB of
        # nothing once they exist, and it can always be fetched again.
        try:
            os.remove(archive)
        except OSError:
            pass
    if not os.path.isfile(executable):
        raise InstallError(
            "the runtime archive did not contain %s" % RUNTIME_EXECUTABLE)
    return executable


def extract_runtime(archive, root, cancel=None):
    """Unpack the generator and its libraries, and nothing else.

    The published archive also carries twenty other command line tools. Only
    the server and the shared libraries it loads are installed, and any
    member naming a directory is refused rather than written.
    """
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            _check_cancel(cancel)
            name = info.filename
            if info.is_dir() or not _is_wanted_member(name):
                continue
            if (os.path.basename(name) != name or
                    os.path.isabs(name) or "\\" in name):
                raise InstallError(
                    "the runtime archive contains an unexpected path: %s"
                    % name)
            target = os.path.join(root, name)
            with bundle.open(info) as source:
                with open(target, "wb") as stream:
                    shutil.copyfileobj(source, stream, CHUNK_BYTES)


def _is_wanted_member(name):
    lowered = name.lower()
    return lowered.endswith(".dll") or lowered == RUNTIME_EXECUTABLE.lower()


def installation_state(catalogue, tier_key, arch, base_dir=None):
    """Describe what is installed, without hashing a gigabyte to find out."""
    entry = catalogue.tier(tier_key)
    model = model_path(entry, base_dir) if entry else None
    executable = runtime_executable(base_dir)
    model_present = bool(
        entry and model and os.path.isfile(model) and
        os.path.getsize(model) == entry["size"])
    runtime_present = os.path.isfile(executable)
    return {
        "model": model,
        "model_present": model_present,
        "runtime": executable,
        "runtime_present": runtime_present,
        "runtime_available": catalogue.runtime_asset(arch) is not None,
        "ready": bool(model_present and runtime_present),
    }


def remove_installation(base_dir=None):
    """Delete everything this feature downloaded."""
    root = install_root(base_dir)
    if not os.path.isdir(root):
        return False
    # Rename first so a locked file cannot leave a half-deleted install
    # looking installed.
    holding = tempfile.mkdtemp(prefix=".bot-chat-removed-",
                               dir=os.path.dirname(root))
    target = os.path.join(holding, CHAT_DIR)
    os.replace(root, target)
    shutil.rmtree(holding, ignore_errors=True)
    return True


def machine_architecture(catalogue, machine=None):
    """Return the catalogue's name for this machine, or None."""
    if machine is None:
        import platform

        machine = platform.machine()
    return catalogue.runtime_arch(machine)


def format_bytes(count):
    """Return a short human-readable size for a progress line."""
    value = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            if unit == "B":
                return "%d B" % int(value)
            return "%.1f %s" % (value, unit)
        value /= 1024.0
    return "%.1f GB" % value
