#!/usr/bin/env python3
"""Connect public BigWorld to the pinned source-built Scaleform 4.2.22 SDK."""
import argparse
from pathlib import Path


def replace_once(source, before, after, label):
    if source.count(after) == 1:
        return source
    if source.count(before) != 1:
        raise ValueError('Unrecognized pinned Scaleform seam: ' + label)
    return source.replace(before, after)


def sdk_edits(sdk_root, edit):
    # Existing AMP collection implementations are retained. Give only the
    # actual BW manager access, rather than exporting SDK internals globally.
    amp = sdk_root / 'Src/GFx/AMP/Amp_Server.h'
    edit(amp, 'namespace Scaleform {\n',
         'namespace BW { namespace ScaleformBW { class Manager; } }\n\nnamespace Scaleform {\n',
         'BW profiler owner declaration')
    edit(amp, 'class Server : public RefCountBase<Server, StatAmp_Server>, public AmpServer\n{',
         'class Server : public RefCountBase<Server, StatAmp_Server>, public AmpServer\n{\n'
         '    friend class ::BW::ScaleformBW::Manager;', 'BW profiler private access')
    png = sdk_root / '3rdParty/libpng-1.5.13/png.h'
    for symbol in ('png_benign_error', 'png_chunk_benign_error'):
        edit(png, '#  undef ' + symbol,
             '// Preserve the private static-build prefix for ' + symbol + '.',
             'PNG static symbol isolation ' + symbol)

    simd = sdk_root / 'Src/Kernel/SF_SIMD_SSE.h'
    # MSVC's first __m128i union member is signed char. Explicit conversion
    # preserves every byte of the constant without list-initialization narrowing.
    for index in range(4):
        for mask, shift in (('000000FF', '0 '), ('0000FF00', '8'),
                            ('00FF0000', '16'), ('FF000000', '24')):
            value = '((i%d&0x%s) >> %s)' % (index, mask, shift)
            edit(simd, value, 'static_cast<char>(%s)' % value,
                 'MSVC SIMD constant byte %d %s' % (index, mask))

    byte_array = sdk_root / 'Src/GFx/AS3/Obj/Utils/AS3_Obj_Utils_ByteArray.cpp'
    edit(byte_array,
         '#endif // SF_ENABLE_ZLIB\n//##protect##"methods"',
         '#endif // SF_ENABLE_ZLIB\n'
         '// Z_PREFIX must not rename the ActionScript ByteArray methods.\n'
         '// The native ZStream helper above has already used the prefixed API.\n'
         '#undef compress\n#undef uncompress\n#undef deflate\n#undef inflate\n'
         '//##protect##"methods"', 'zlib and ActionScript method names')
    edit(sdk_root / 'Src/GFx/AS3/Obj/AS3_Obj_Date.cpp',
         'NumberUtil::IsNaNOrInfinity(time) || (abs(time) > 8.64e15)',
         'NumberUtil::IsNaNOrInfinity(time) || (fabs(time) > 8.64e15)',
         'Date floating-point absolute value')


def patch_engine(engine_root, sdk_root):
    engine_root, sdk_root = Path(engine_root), Path(sdk_root)
    changes = {}

    def edit(path, before, after, label):
        source = changes.get(path)
        if source is None:
            source = path.read_text(encoding='utf-8-sig')
        changes[path] = replace_once(source, before, after, label)

    edit(engine_root / 'build/cmake/BWConfiguration_client.cmake',
         'SET( BW_SCALEFORM_SUPPORT OFF )', 'SET( BW_SCALEFORM_SUPPORT ON )',
         'client feature flag')
    edit(engine_root / 'CMakeLists.txt', '# Add libraries\n',
         '# Build the pinned source SDK before its BigWorld consumers.\n'
         'IF( BW_SCALEFORM_SUPPORT )\n'
         '\tIF( NOT EXISTS "${WOT_SCALEFORM_SOURCE_CMAKE}" )\n'
         '\t\tMESSAGE( FATAL_ERROR "WOT_SCALEFORM_SOURCE_CMAKE is required" )\n'
         '\tENDIF()\n'
         '\tINCLUDE( "${WOT_SCALEFORM_SOURCE_CMAKE}" )\n'
         'ENDIF()\n\n# Add libraries\n', 'SDK target ownership')
    for path, before in (
            (engine_root / 'lib/scaleform/CMakeLists.txt',
             'FIND_PACKAGE( ScaleformSDK 4.1.19 EXACT )'),
            (engine_root / 'client/CMakeLists.txt',
             '\tFIND_PACKAGE( ScaleformSDK )')):
        edit(path, before,
             'IF( NOT TARGET ScaleformSDK::ScaleformSDK )\n'
             '\tMESSAGE( FATAL_ERROR "The source-built Scaleform SDK target is missing" )\n'
             'ENDIF()', 'source SDK consumer ' + str(path.relative_to(engine_root)))

    config = engine_root / 'lib/scaleform/config.hpp'
    edit(config, '#include <GFx_Sound_FMOD.h>',
         '#if FMOD_SUPPORT\n#include <GFx_Sound_FMOD.h>\n#endif', 'FMOD convenience header')
    edit(config, '#include <Sound/Sound_SoundRendererFMOD.h>',
         '#if FMOD_SUPPORT\n#include <Sound/Sound_SoundRendererFMOD.h>\n#endif', 'FMOD backend header')
    edit(config, '#include <Video/Video_VideoPC.h>',
         '// This SDK does not include the optional CRI video addon.', 'uninstalled video addon')
    edit(config,
         '#ifdef GFX_AMP_SERVER\n#include <Amp/GFx_AmpServer.h>\n#include <Amp/Amp_ProfileFrame.h>\n#endif',
         '#ifdef SF_AMP_SERVER\n#include <GFx/AMP/Amp_Server.h>\n#include <GFx/AMP/Amp_ProfileFrame.h>\n#endif',
         'SDK AMP header names')

    manager = engine_root / 'lib/scaleform/manager.cpp'
    edit(manager, '\t\tGFx::setRestrictFlashFilters(true);',
         '\t\t// SDK 4.2.22 uses its stock filter policy; the BW-specific\n'
         '\t\t// setRestrictFlashFilters extension is absent from this source SDK.',
         'unavailable BW filter extension')
    edit(manager,
         '\t\t/* GFx Sound System initialization */\n',
         '#if FMOD_SUPPORT\n\t\t/* GFx Sound System initialization */\n',
         'optional FMOD initialization start')
    edit(manager, '\t\t\tpLoader_->SetAudio(pAudioState);\n\n\t\t}\n',
         '\t\t\tpLoader_->SetAudio(pAudioState);\n\n\t\t}\n#endif // FMOD_SUPPORT\n',
         'optional FMOD initialization end')
    edit(manager, '\t\tpLoader_->SetVideo(NULL);',
         '#ifdef GFX_ENABLE_VIDEO\n\t\tpLoader_->SetVideo(NULL);\n#endif',
         'optional video teardown')
    edit(manager,
         '\t\tif (SoundManager::instance().isInitialised())\n\t\t{\n'
         '\t\t\tGFx::Audio *pAudio = (GFx::Audio*)pLoader_->GetAudio().GetPtr();\n'
         '\t\t\tpAudio->GetRenderer()->SetMasterVolume(mute ? 0.0f : 1.0f);\n\t\t}',
         '\t\tPtr<GFx::AudioBase> audio = pLoader_->GetAudio();\n'
         '\t\tif (audio && audio->GetRenderer())\n\t\t{\n'
         '\t\t\taudio->GetRenderer()->SetMasterVolume(mute ? 0.0f : 1.0f);\n\t\t}',
         'abstract audio owner mute')

    # The upstream raw-pointer return outlives its local Ptr only after a
    # successful copy added a reference. Failed texture locks must return NULL.
    loader = engine_root / 'lib/scaleform/loader.cpp'
    edit(loader, '\t\tif (tex)\n', '\t\tbool copied = false;\n\t\tif (tex && pimage)\n',
         'protocol image allocation guard')
    edit(loader, '\t\t\t\t\tpimage->AddRef();',
         '\t\t\t\t\tpimage->AddRef();\n\t\t\t\t\tcopied = true;',
         'protocol image retained ownership')
    edit(loader, '\t\treturn pimage;', '\t\treturn copied ? pimage.GetPtr() : NULL;',
         'failed protocol image ownership')

    sdk_edits(sdk_root, edit)
    for path, source in changes.items():
        path.write_text(source, encoding='utf-8')
    print('Public BigWorld connected to source Scaleform 4.2.22; optional FMOD/video remain uninstalled')


def patch_sdk(sdk_root):
    changes = {}

    def edit(path, before, after, label):
        source = changes.get(path)
        if source is None:
            source = path.read_text(encoding='utf-8-sig')
        changes[path] = replace_once(source, before, after, label)

    sdk_edits(Path(sdk_root), edit)
    for path, source in changes.items():
        path.write_text(source, encoding='utf-8')
    print('Scaleform SDK source seams prepared for private static linking')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-only', action='store_true')
    parser.add_argument('source_roots', type=Path, nargs='+')
    args = parser.parse_args()
    if args.sdk_only:
        if len(args.source_roots) != 1:
            parser.error('--sdk-only takes one SDK source root')
        patch_sdk(args.source_roots[0])
    else:
        if len(args.source_roots) != 2:
            parser.error('Pass the engine and SDK source roots')
        patch_engine(*args.source_roots)
