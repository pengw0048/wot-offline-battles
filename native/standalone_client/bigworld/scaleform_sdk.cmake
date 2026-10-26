# Build the available SDK, not the original client's different 4.6.34 runtime.
# The source manifests come from the SDK's Win32 projects, not platform globs.
include_guard(GLOBAL)

if(NOT WIN32 OR NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
    message(FATAL_ERROR "Scaleform SDK integration requires 32-bit Windows/MSVC")
endif()
if(NOT WOT_SCALEFORM_SDK_ROOT)
    message(FATAL_ERROR "Set WOT_SCALEFORM_SDK_ROOT to the source SDK")
endif()
file(TO_CMAKE_PATH "${WOT_SCALEFORM_SDK_ROOT}" WOT_SCALEFORM_SDK_ROOT)
option(WOT_SCALEFORM_FMOD_ADDON "Build the optional GFx FMOD renderer" OFF)
file(STRINGS "${WOT_SCALEFORM_SDK_ROOT}/Include/GFxVersion.h" _sf_version_line
    REGEX "^#define[ \t]+GFX_VERSION_STRING[ \t]+")
if(NOT _sf_version_line MATCHES "\"4\\.2\\.22\"")
    message(FATAL_ERROR "This source manifest was reviewed for SDK 4.2.22 only")
endif()

# These projects have no per-platform source substitutions. The sole excluded
# source is zlib's optional assembler wrapper, excluded in all four configs.
function(_wot_scaleform_sources output project)
    set(project "${WOT_SCALEFORM_SDK_ROOT}/${project}")
    get_filename_component(directory "${project}" DIRECTORY)
    file(READ "${project}" xml)
    file(STRINGS "${project}" items REGEX "<ClCompile Include=")
    set(sources)
    foreach(item IN LISTS items)
        if(NOT item MATCHES "<ClCompile Include=\"([^\"]+)\"")
            message(FATAL_ERROR "Unexpected source item in ${project}: ${item}")
        endif()
        set(relative "${CMAKE_MATCH_1}")
        if(NOT item MATCHES "/>")
            string(FIND "${xml}" "${item}" begin)
            string(SUBSTRING "${xml}" ${begin} -1 tail)
            string(FIND "${tail}" "</ClCompile>" end)
            if(end LESS 0)
                message(FATAL_ERROR "Unterminated source item: ${item}")
            endif()
            string(SUBSTRING "${tail}" 0 ${end} block)
            if(block MATCHES "<ExcludedFromBuild")
                string(REGEX MATCHALL "<ExcludedFromBuild[^>]*>true</ExcludedFromBuild>" exclusions "${block}")
                list(LENGTH exclusions count)
                if(NOT count EQUAL 4)
                    message(FATAL_ERROR "Unreviewed conditional source exclusion: ${item}")
                endif()
                continue()
            endif()
        endif()
        string(REPLACE "\\" "/" relative "${relative}")
        get_filename_component(source "${directory}/${relative}" ABSOLUTE)
        if(NOT EXISTS "${source}")
            message(FATAL_ERROR "SDK project source is missing: ${source}")
        endif()
        list(APPEND sources "${source}")
    endforeach()
    if(NOT sources)
        message(FATAL_ERROR "SDK project contains no compile items: ${project}")
    endif()
    list(LENGTH sources count)
    message(STATUS "Scaleform SDK project ${project}: ${count} sources")
    set(${output} "${sources}" PARENT_SCOPE)
endfunction()

# BigWorld and GFx carry different PNG/zlib versions. Keep their C symbols
# private instead of accidentally resolving one decoder against another's ABI.
file(READ "${WOT_SCALEFORM_SDK_ROOT}/3rdParty/libpng-1.5.13/scripts/symbols.def" _sf_png_exports)
string(REGEX MATCHALL "png_[A-Za-z0-9_]+" _sf_png_symbols "${_sf_png_exports}")
file(READ "${WOT_SCALEFORM_SDK_ROOT}/3rdParty/libpng-1.5.13/pngpriv.h" _sf_png_private)
string(REGEX REPLACE "/\\*([^*]|\\*+[^*/])*\\*+/" "" _sf_png_private "${_sf_png_private}")
string(REGEX REPLACE "#[^\n]*\n" "\n" _sf_png_private "${_sf_png_private}")
string(REGEX MATCHALL "PNG_EXTERN[^;]*;" _sf_png_declarations "${_sf_png_private}")
foreach(declaration IN LISTS _sf_png_declarations)
    string(REGEX MATCHALL "png_[A-Za-z0-9_]+[ \t\r\n]+PNGARG|png_[A-Za-z0-9_]+[ \t\r\n]*\\(" functions "${declaration}")
    foreach(function IN LISTS functions)
        string(REGEX MATCH "png_[A-Za-z0-9_]+" symbol "${function}")
        list(APPEND _sf_png_symbols "${symbol}")
    endforeach()
    if(declaration MATCHES "PNG_FUNCTION\\([^,]+,[ \t\r\n]*(png_[A-Za-z0-9_]+)")
        list(APPEND _sf_png_symbols "${CMAKE_MATCH_1}")
    endif()
endforeach()
list(REMOVE_DUPLICATES _sf_png_symbols)
list(SORT _sf_png_symbols)
set(_sf_png_prefix_header "${CMAKE_CURRENT_BINARY_DIR}/wot_scaleform_png_prefix.h")
file(WRITE "${_sf_png_prefix_header}" "/* Private names for the SDK's original PNG decoder. */\n#pragma once\n")
foreach(symbol IN LISTS _sf_png_symbols)
    file(APPEND "${_sf_png_prefix_header}" "#define ${symbol} wot_sf_${symbol}\n")
endforeach()

set(_sf_includes
    "${WOT_SCALEFORM_SDK_ROOT}/Include"
    "${WOT_SCALEFORM_SDK_ROOT}/Src"
    "${WOT_SCALEFORM_SDK_ROOT}/Src/GFx"
    "${WOT_SCALEFORM_SDK_ROOT}/3rdParty/zlib-1.2.7"
    "${WOT_SCALEFORM_SDK_ROOT}/3rdParty/jpeg-8d"
    "${WOT_SCALEFORM_SDK_ROOT}/3rdParty/libpng-1.5.13"
    "${WOT_SCALEFORM_SDK_ROOT}/3rdParty/expat-2.1.0/lib"
    "${WOT_SCALEFORM_SDK_ROOT}/3rdParty/pcre")

function(_wot_scaleform_library name project output_name)
    _wot_scaleform_sources(sources "${project}")
    if(name STREQUAL "wot_sf_sound" AND NOT WOT_SCALEFORM_FMOD_ADDON)
        # Preserve the actual SoundData/SoundRenderer abstractions and AS sound
        # readers. Only the unused backend requires FMOD headers or symbols.
        list(FILTER sources EXCLUDE REGEX "/Sound_SoundRendererFMOD\\.cpp$")
    endif()
    add_library(${name} STATIC ${sources})
    target_include_directories(${name} PUBLIC ${_sf_includes})
    target_compile_definitions(${name} PUBLIC SF_BUILD_STATICLIB
        PRIVATE WIN32 _WINDOWS _CRT_SECURE_NO_WARNINGS Z_PREFIX PNG_NO_USE_READ_MACROS
        $<$<CONFIG:Debug>:_DEBUG> $<$<NOT:$<CONFIG:Debug>>:NDEBUG>)
    # Rebuild with the application's DLL CRT; the supplied VS2010 binaries
    # use /MT and must not be mixed with BigWorld's /MD allocation ownership.
    set_target_properties(${name} PROPERTIES OUTPUT_NAME "${output_name}"
        CXX_STANDARD 14 CXX_STANDARD_REQUIRED YES
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")
    target_compile_options(${name} PRIVATE /W3 /WX- /MP /Zp8 "/FI${_sf_png_prefix_header}"
        $<$<CONFIG:Debug>:/MDd> $<$<NOT:$<CONFIG:Debug>>:/MD>)
endfunction()

_wot_scaleform_library(wot_sf_gfx Projects/Win32/Msvc10/SDK/GFx/GFx.vcxproj libgfx)
_wot_scaleform_library(wot_sf_as2 Projects/Win32/Msvc10/SDK/GFx/AS2.vcxproj libgfx_as2)
_wot_scaleform_library(wot_sf_as3 Projects/Win32/Msvc10/SDK/GFx/AS3.vcxproj libgfx_as3)
_wot_scaleform_library(wot_sf_d3d9 Projects/Win32/Msvc10/SDK/Render/Render_D3D9.vcxproj libgfxrender_d3d9)
_wot_scaleform_library(wot_sf_xml Projects/Win32/Msvc10/SDK/Expat/Expat.vcxproj libgfxexpat)
_wot_scaleform_library(wot_sf_sound Projects/Win32/Msvc10/SDK/Sound/Sound.vcxproj libgfxsound)
_wot_scaleform_library(wot_sf_jpeg 3rdParty/jpeg-8d/gfx_projects/Win32/Msvc10/libjpeg.vcxproj libjpeg)
_wot_scaleform_library(wot_sf_zlib 3rdParty/zlib-1.2.7/gfx_projects/win32/Msvc10/zlib.vcxproj zlib)
_wot_scaleform_library(wot_sf_png 3rdParty/libpng-1.5.13/gfx_projects/Win32/Msvc10/libpng.vcxproj libpng)
_wot_scaleform_library(wot_sf_pcre 3rdParty/pcre/gfx_projects/Win32/Msvc10/pcre.vcxproj pcre)
_wot_scaleform_library(wot_sf_expat 3rdParty/expat-2.1.0/gfx_projects/Win32/Msvc10/expat.vcxproj expat)
target_compile_definitions(wot_sf_pcre PRIVATE HAVE_CONFIG_H PUBLIC PCRE_STATIC)
target_compile_definitions(wot_sf_expat PRIVATE COMPILED_FROM_DSP PUBLIC XML_STATIC)
target_link_libraries(wot_sf_png PUBLIC wot_sf_zlib)
target_link_libraries(wot_sf_gfx PUBLIC wot_sf_jpeg wot_sf_png wot_sf_zlib wot_sf_pcre
    winmm ws2_32)
target_link_libraries(wot_sf_as2 PUBLIC wot_sf_gfx)
target_link_libraries(wot_sf_as3 PUBLIC wot_sf_gfx)
target_link_libraries(wot_sf_xml PUBLIC wot_sf_gfx wot_sf_expat)

if(NOT WOT_SCALEFORM_D3DX_ROOT)
    message(FATAL_ERROR "Set WOT_SCALEFORM_D3DX_ROOT to a legacy DirectX SDK layout (Include, Lib/x86)")
endif()
find_path(_sf_d3dx_include d3dx9.h PATHS "${WOT_SCALEFORM_D3DX_ROOT}/Include" NO_DEFAULT_PATH REQUIRED)
find_library(_sf_d3dx_library d3dx9 PATHS "${WOT_SCALEFORM_D3DX_ROOT}/Lib/x86" NO_DEFAULT_PATH REQUIRED)
target_include_directories(wot_sf_d3d9 PRIVATE "${_sf_d3dx_include}")
target_link_libraries(wot_sf_d3d9 PUBLIC wot_sf_gfx d3d9 "${_sf_d3dx_library}")

# GFx's core sound path talks to the abstract SoundRenderer. The Windows
# configuration enables that path, but only this optional backend includes FMOD.
# Keep the backend disabled when the application's audio owner is Wwise.
if(WOT_SCALEFORM_FMOD_ADDON)
    if(NOT EXISTS "${WOT_SCALEFORM_FMOD_INCLUDE_DIR}/fmod.hpp" OR
            NOT EXISTS "${WOT_SCALEFORM_FMOD_LIBRARY}")
        message(FATAL_ERROR "The FMOD addon requires the application's FMOD headers/import library")
    endif()
    target_include_directories(wot_sf_sound PUBLIC "${WOT_SCALEFORM_FMOD_INCLUDE_DIR}"
        PRIVATE "${WOT_SCALEFORM_SDK_ROOT}/Src/Kernel")
    target_link_libraries(wot_sf_sound PUBLIC "${WOT_SCALEFORM_FMOD_LIBRARY}")
endif()
target_link_libraries(wot_sf_sound PUBLIC wot_sf_gfx)

add_library(wot_scaleform_sdk INTERFACE)
add_library(ScaleformSDK::ScaleformSDK ALIAS wot_scaleform_sdk)
target_link_libraries(wot_scaleform_sdk INTERFACE wot_sf_as2 wot_sf_as3 wot_sf_d3d9
    wot_sf_xml wot_sf_sound)
set(SCALEFORMSDK_FOUND TRUE)
set(SCALEFORMSDK_VERSION_STRING "4.2.22")
set(SCALEFORMSDK_INCLUDE_DIRS "${_sf_includes}")
if(WOT_SCALEFORM_FMOD_ADDON)
    list(APPEND SCALEFORMSDK_INCLUDE_DIRS "${WOT_SCALEFORM_FMOD_INCLUDE_DIR}")
endif()
set(SCALEFORMSDK_LIBRARIES wot_scaleform_sdk)

option(WOT_SCALEFORM_LINK_PROBE "Link real AS2/AS3, XML, D3D9 and sound SDK entry points" OFF)
if(WOT_SCALEFORM_LINK_PROBE)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/scaleform_link_probe.cpp" [=[
#include <GFx.h>
#include <GFx_Kernel.h>
#include <GFx_Renderer_D3D9.h>
#ifdef WOT_SCALEFORM_FMOD_ADDON
#include <Sound/Sound_SoundRendererFMOD.h>
#endif
#include <Xml/XML_Expat.h>
int main() {
    Scaleform::GFx::System system;
    Scaleform::GFx::Loader loader;
    loader.SetAS2Support(Scaleform::Ptr<Scaleform::GFx::ASSupport>(*new Scaleform::GFx::AS2Support));
    loader.SetAS3Support(Scaleform::Ptr<Scaleform::GFx::ASSupport>(*new Scaleform::GFx::AS3Support));
    Scaleform::Ptr<Scaleform::GFx::XML::Parser> parser = *new Scaleform::GFx::XML::ParserExpat;
    loader.SetXMLSupport(Scaleform::Ptr<Scaleform::GFx::XML::SupportBase>(*new Scaleform::GFx::XML::Support(parser)));
    Scaleform::Ptr<Scaleform::Render::D3D9::HAL> hal = *new Scaleform::Render::D3D9::HAL;
#ifdef WOT_SCALEFORM_FMOD_ADDON
    Scaleform::Ptr<Scaleform::Sound::SoundRendererFMOD> sound = *Scaleform::Sound::SoundRendererFMOD::CreateSoundRenderer();
    return hal && sound ? 0 : 1;
#else
    return hal ? 0 : 1;
#endif
}
]=])
    add_executable(wot_scaleform_link_probe "${CMAKE_CURRENT_BINARY_DIR}/scaleform_link_probe.cpp")
    target_link_libraries(wot_scaleform_link_probe PRIVATE wot_scaleform_sdk)
    target_include_directories(wot_scaleform_link_probe PRIVATE "${_sf_d3dx_include}")
    target_compile_options(wot_scaleform_link_probe PRIVATE /MD)
    if(WOT_SCALEFORM_FMOD_ADDON)
        target_compile_definitions(wot_scaleform_link_probe PRIVATE WOT_SCALEFORM_FMOD_ADDON)
    endif()
endif()
