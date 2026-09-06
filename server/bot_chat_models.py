"""Name the optional local chat model and runtime, and where to fetch them.

This module is pure data and URL construction.  It downloads nothing and
imports nothing beyond the standard library, so the launcher, the server and
the tests can all agree on exactly one catalogue.

Two mirrors serve every model entry.  ModelScope is reachable from mainland
China, where Hugging Face generally is not; both were verified to serve the
byte-identical file, so one ``sha256`` validates a download from either.

Every listed model is Apache-2.0.  The runtime is the upstream llama.cpp
CPU-only Windows build: the game already owns the GPU, and a CPU build is a
fraction of the size of the CUDA one.  The build is pinned rather than
resolved from "latest" so a working install cannot change under a player.
"""

MODELSCOPE = "modelscope"
HUGGINGFACE = "huggingface"
SOURCES = (MODELSCOPE, HUGGINGFACE)

# ``FilePath`` serves the LFS object directly, which is what makes ModelScope
# usable without its SDK.
_MODELSCOPE_URL = (
    "https://www.modelscope.cn/api/v1/models/%s/repo"
    "?Revision=master&FilePath=%s")
_HUGGINGFACE_URL = "https://huggingface.co/%s/resolve/main/%s"

# Qwen3 thinking is switched off explicitly rather than avoided: the pinned
# runtime accepts ``--reasoning-budget 0`` and the request carries
# ``chat_template_kwargs {"enable_thinking": false}``.  Both are no-ops for a
# model whose template has no thinking mode, so the same call serves either
# generation.  Without them a reasoning model would spend the whole reply
# budget on a ``<think>`` block nobody sees.
#
# The Qwen organisation publishes only Q8_0 for the Qwen3 GGUF repositories,
# so those entries are larger than the Q4_K_M Qwen2.5 ones at similar
# parameter counts.  Third-party requantisations are deliberately not listed.
MODEL_TIERS = (
    {
        "key": "qwen3-0.6b",
        "parameters": "0.6B",
        "quantization": "Q8_0",
        "repo": "Qwen/Qwen3-0.6B-GGUF",
        "file": "Qwen3-0.6B-Q8_0.gguf",
        "size": 639446688,
        "sha256":
            "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031",
        "license": "Apache-2.0",
    },
    {
        "key": "qwen3-1.7b",
        "parameters": "1.7B",
        "quantization": "Q8_0",
        "repo": "Qwen/Qwen3-1.7B-GGUF",
        "file": "Qwen3-1.7B-Q8_0.gguf",
        "size": 1834426016,
        "sha256":
            "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a",
        "license": "Apache-2.0",
    },
    {
        "key": "qwen2.5-0.5b",
        "parameters": "0.5B",
        "quantization": "Q4_K_M",
        "repo": "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
        "file": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "size": 491400032,
        "sha256":
            "74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db",
        "license": "Apache-2.0",
    },
    {
        "key": "qwen2.5-1.5b",
        "parameters": "1.5B",
        "quantization": "Q4_K_M",
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "size": 1117320736,
        "sha256":
            "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
        "license": "Apache-2.0",
    },
)
# The smallest current-generation entry: newer than the Qwen2.5 pair at a
# comparable size, and small enough to be the one tier every machine tries
# first.  Which of these actually writes a better Chinese one-liner in time
# has not been measured on Windows.
DEFAULT_TIER_KEY = "qwen3-0.6b"

RUNTIME_BUILD = "b10819"
RUNTIME_LICENSE = "MIT"
_RUNTIME_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/download/%s/%s")
RUNTIME_ASSETS = {
    "x64": {
        "file": "llama-b10819-bin-win-cpu-x64.zip",
        "size": 18413010,
        "sha256":
            "4599e502b374196d24600ea9b03c842a448c853116a15b55e8ba502bdc727b3f",
    },
    "arm64": {
        "file": "llama-b10819-bin-win-cpu-arm64.zip",
        "size": 11980559,
        "sha256":
            "5802d55f633b68bf6dbe574d75f9f47387761fe3b6ddef4193ea9ea423642afb",
    },
}
RUNTIME_EXECUTABLE = "llama-server.exe"


def tier(key):
    """Return one catalogued model tier, or None."""
    for entry in MODEL_TIERS:
        if entry["key"] == str(key):
            return dict(entry)
    return None


def default_tier():
    """Return the tier a player gets without choosing one."""
    return tier(DEFAULT_TIER_KEY)


def model_url(tier_key, source):
    """Return the direct download URL for one tier from one mirror."""
    entry = tier(tier_key)
    if entry is None or source not in SOURCES:
        return None
    if source == MODELSCOPE:
        return _MODELSCOPE_URL % (entry["repo"], entry["file"])
    return _HUGGINGFACE_URL % (entry["repo"], entry["file"])


def runtime_arch(machine):
    """Map a ``platform.machine()`` string onto a published runtime build.

    A Windows-on-ARM host runs the x64 build under emulation, which is
    markedly slower than its native ARM64 build.  Naming the architecture
    honestly is the difference between a usable reply and a timeout.
    """
    name = str(machine or "").strip().lower()
    if name in ("arm64", "aarch64"):
        return "arm64"
    if name in ("amd64", "x86_64", "x64"):
        return "x64"
    # A 32-bit host has no published CPU build; the caller must report that
    # rather than download an executable that cannot run.
    return None


def runtime_asset(arch):
    """Return the pinned runtime archive for one architecture, or None."""
    entry = RUNTIME_ASSETS.get(str(arch))
    return dict(entry) if entry else None


def runtime_url(arch):
    """Return the pinned runtime download URL, or None."""
    entry = runtime_asset(arch)
    if entry is None:
        return None
    return _RUNTIME_URL % (RUNTIME_BUILD, entry["file"])
