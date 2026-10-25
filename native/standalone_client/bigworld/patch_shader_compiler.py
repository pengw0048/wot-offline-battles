#!/usr/bin/env python3
"""Enable the pinned engine's local FX compiler for the standalone client."""

import argparse
from pathlib import Path


def replace_once(text, before, after, label):
    if text.count(before) != 1:
        raise ValueError("Pinned shader compiler contract changed: " + label)
    return text.replace(before, after)


def patch_engine(engine):
    moo = engine / "lib" / "moo"
    header = (moo / "effect_compiler.hpp").read_text()
    header = replace_once(header,
        "\tstatic void setMacroDefinition(",
        "\tstatic BinaryPtr loadOrCompile( const BW::string& resourceID );\n\n"
        "\tstatic void setMacroDefinition(", "runtime entry point")
    header = replace_once(header,
        "\tstatic SimpleMutex s_hashCacheMutex_;",
        "\tstatic SimpleMutex s_hashCacheMutex_;\n"
        "\tstatic SimpleMutex s_runtimeCompileMutex_;", "compiler context mutex")

    compiler = (moo / "effect_compiler.cpp").read_text()
    compiler = replace_once(compiler, "namespace {\n", "namespace {\n" + r'''
    // Resource IDs can live in any search root or package. Includes keep using
    // EffectIncludes; the main source must also use BWResource instead of cwd.
    HRESULT createEffectCompilerFromResource( const BW::string& resourceID,
        const D3DXMACRO* macros, ID3DXInclude* includes, DWORD flags,
        ID3DXEffectCompiler** compiler, ID3DXBuffer** errors )
    {
        DataSectionPtr section = BWResource::openSection( resourceID );
        BinaryPtr source = section ? section->asBinary() : BinaryPtr();
        if (!source)
        {
            ERROR_MSG( "EffectCompiler: unable to read source '%s'\n",
                resourceID.c_str() );
            return E_FAIL;
        }
        return D3DXCreateEffectCompiler( source->cdata(),
            static_cast<UINT>( source->len() ), macros, includes, flags,
            compiler, errors );
    }

''', "resource-backed compiler input")
    if compiler.count("D3DXCreateEffectCompilerFromFileW( wResName.c_str(),") != 4:
        raise ValueError("Pinned shader compiler contract changed: file compiler calls")
    compiler = compiler.replace(
        "D3DXCreateEffectCompilerFromFileW( wResName.c_str(),",
        "createEffectCompilerFromResource( resourceID,")
    if compiler.count("BW::wstring wResName = bw_utf8tow( resourceID );") != 2:
        raise ValueError("Pinned shader compiler contract changed: file compiler names")
    compiler = compiler.replace("BW::wstring wResName = bw_utf8tow( resourceID );", "")
    compiler = replace_once(compiler, "SimpleMutex EffectCompiler::s_hashCacheMutex_;",
        "SimpleMutex EffectCompiler::s_hashCacheMutex_;\n"
        "SimpleMutex EffectCompiler::s_runtimeCompileMutex_;", "context mutex storage")
    compiler = replace_once(compiler,
        "EffectCompiler::StringStringMap EffectCompiler::s_macros_;",
        "EffectCompiler::StringStringMap EffectCompiler::s_macros_;\n" + r'''

// Serialize the freshness check, include state, compilation and cache write.
// Existing macro settings and fxoInfix retain the upstream cache names.
BinaryPtr EffectCompiler::loadOrCompile( const BW::string& resourceID )
{
    SimpleMutexHolder guard( s_runtimeCompileMutex_ );
    EffectCompiler compiler( true );
    if (!compiler.checkModified( resourceID ))
    {
        return EffectHelpers::loadEffectBinary( resourceID );
    }

    BW::string result;
    BinaryPtr binary = compiler.compile( resourceID, &result );
    if (!binary)
    {
        ERROR_MSG( "EffectCompiler: local compilation failed for '%s': %s\n",
            resourceID.c_str(), result.c_str() );
    }
    else
    {
        INFO_MSG( "EffectCompiler: locally compiled '%s'\n", resourceID.c_str() );
    }
    return binary;
}
''', "local compile and cache")
    compiler = replace_once(compiler, "\ts_macros_[macro] = value;",
        "\tSimpleMutexHolder guard( s_runtimeCompileMutex_ );\n"
        "\ts_macros_[macro] = value;", "macro synchronization")
    compiler = replace_once(compiler, "\ts_fxoInfix_ = infix;",
        "\tSimpleMutexHolder guard( s_runtimeCompileMutex_ );\n"
        "\ts_fxoInfix_ = infix;", "cache infix synchronization")

    manager = (moo / "effect_manager.cpp").read_text()
    manager = replace_once(manager, '''#if ENABLE_ASSET_PIPE
\tif (pAssetClient_ != NULL)
    {
\t    BW::string fxoName = EffectHelpers::fxoName( resourceID );
\t    pAssetClient_->requestAsset( fxoName, true );
    }
#endif''',
        "\tEffectCompiler::loadOrCompile( resourceID );", "standalone ensureCompiled")
    effect = (moo / "managed_effect.cpp").read_text()
    effect = replace_once(effect,
        "BinaryPtr bin = EffectHelpers::loadEffectBinary( resourceID_ );",
        "BinaryPtr bin = EffectCompiler::loadOrCompile( resourceID_ );",
        "reload local compilation")
    effect = replace_once(effect, '''\tID3DXEffect* pEffect = NULL;
\tHRESULT hr = D3DXCreateEffect( rc().device(),
\t\tbin->cdata(), bin->len(),
\t\tNULL, NULL, USE_LEGACY_D3DX9_DLL,\x20
\t\trc().effectVisualContext().pEffectPool(),\x20
\t\t&pEffect, NULL );''', '''\tID3DXEffect* pEffect = NULL;
\tComObjectWrap<ID3DXBuffer> creationErrors;
\t// The input is CompileEffect bytecode, not FX source. No compiler flags
\t// belong here; keep the runtime's normal shader validation enabled.
\tHRESULT hr = D3DXCreateEffect( rc().device(),
\t\tbin->cdata(), bin->len(),
\t\tNULL, NULL, 0,
\t\trc().effectVisualContext().pEffectPool(),
\t\t&pEffect, &creationErrors );''', "compiled effect creation flags and diagnostics")
    effect = replace_once(effect, '''\t\tif (E_FAIL == hr)
\t\t\tERROR_MSG( "Check if DirectX 9 installed on your system!\\n" );''',
        '''\t\tERROR_MSG( "ManagedEffect::reload: D3DXCreateEffect failed for '%s' "
\t\t\t"(%d byte compiled effect): %s\\n", resourceID_.c_str(), bin->len(),
\t\t\tcreationErrors ? static_cast<const char*>(
\t\t\t\tcreationErrors->GetBufferPointer() ) : "<no D3DX diagnostics>" );''',
        "compiled effect failure diagnostics")

    for name, contents in (("effect_compiler.hpp", header),
                           ("effect_compiler.cpp", compiler),
                           ("effect_manager.cpp", manager),
                           ("managed_effect.cpp", effect)):
        (moo / name).write_text(contents, encoding="utf-8")
    print("Enabled resource-backed local FX compilation and native FXO caching")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", type=Path)
    patch_engine(parser.parse_args().engine)


if __name__ == "__main__":
    main()
