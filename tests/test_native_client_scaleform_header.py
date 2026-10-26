"""Exercise generated-header invalidation through the actual CMake module.

The tiny SDK metadata fixture permits configuration only. These tests do not
compile SDK code or establish a native Windows rendering contract.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "native/standalone_client/bigworld/scaleform_sdk.cmake"
CMAKE = shutil.which("cmake")
PROJECTS = (
    "Projects/Win32/Msvc10/SDK/GFx/GFx.vcxproj",
    "Projects/Win32/Msvc10/SDK/GFx/AS2.vcxproj",
    "Projects/Win32/Msvc10/SDK/GFx/AS3.vcxproj",
    "Projects/Win32/Msvc10/SDK/Render/Render_D3D9.vcxproj",
    "Projects/Win32/Msvc10/SDK/Expat/Expat.vcxproj",
    "Projects/Win32/Msvc10/SDK/Sound/Sound.vcxproj",
    "3rdParty/jpeg-8d/gfx_projects/Win32/Msvc10/libjpeg.vcxproj",
    "3rdParty/zlib-1.2.7/gfx_projects/win32/Msvc10/zlib.vcxproj",
    "3rdParty/libpng-1.5.13/gfx_projects/Win32/Msvc10/libpng.vcxproj",
    "3rdParty/pcre/gfx_projects/Win32/Msvc10/pcre.vcxproj",
    "3rdParty/expat-2.1.0/gfx_projects/Win32/Msvc10/expat.vcxproj",
)


@unittest.skipUnless(CMAKE, "CMake is required for the generated-header contract")
class ScaleformHeaderGeneration(unittest.TestCase):
    def test_unchanged_configuration_preserves_mtime_but_symbols_invalidate(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            sdk, directx = folder / "sdk", folder / "directx"

            def write(relative, contents="// Configure-only fixture.\n"):
                path = sdk / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents, encoding="utf-8")
                return path

            write("Include/GFxVersion.h", '#define GFX_VERSION_STRING "4.2.22"\n')
            symbols = write("3rdParty/libpng-1.5.13/scripts/symbols.def", "png_fixture_public\n")
            write("3rdParty/libpng-1.5.13/pngpriv.h",
                  "PNG_EXTERN int png_fixture_private PNGARG((void));\n")
            for project in PROJECTS:
                write(project, '<Project><ItemGroup><ClCompile Include="stub.cpp" /></ItemGroup></Project>\n')
                write(str(Path(project).parent / "stub.cpp"))
            for source in ("Src/GFx/AS3/AS3_SocketBuffer.cpp", "Src/GFx/AS3/AS3_SocketThreadMgr.cpp",
                           "Src/GFx/AS3/Obj/System/AS3_Obj_System_Domain.cpp"):
                write(source)
            for name in ("Include/d3dx9.h", "Lib/x86/d3dx9.lib", "Lib/x86/libd3dx9.a"):
                path = directx / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            source, build = folder / "source", folder / "build"
            source.mkdir()
            (source / "CMakeLists.txt").write_text('''cmake_minimum_required(VERSION 3.20)
project(generated_header_contract LANGUAGES C CXX)
# Only the configure-time platform guard is satisfied; no compiler target runs.
set(WIN32 TRUE)
set(MSVC TRUE)
set(CMAKE_SIZEOF_VOID_P 4)
set(WOT_SCALEFORM_SDK_ROOT "{sdk}")
set(WOT_SCALEFORM_D3DX_ROOT "{directx}")
include("{module}")
'''.format(sdk=sdk.as_posix(), directx=directx.as_posix(), module=MODULE.as_posix()), encoding="utf-8")

            def configure():
                result = subprocess.run([CMAKE, "-S", str(source), "-B", str(build)],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                self.assertEqual(result.returncode, 0, result.stdout)

            configure()
            header = build / "wot_scaleform_png_prefix.h"
            initial = header.read_bytes()
            self.assertNotIn(b"\r", initial)
            self.assertIn(b"#define png_fixture_public wot_sf_png_fixture_public", initial.splitlines())
            self.assertIn(b"#define png_fixture_private wot_sf_png_fixture_private", initial.splitlines())
            # A known old mtime catches a rewrite without relying on a sleep or
            # the timestamp resolution of the filesystem running this test.
            sentinel = 946684800000000000
            os.utime(header, ns=(sentinel, sentinel))
            configure()
            self.assertEqual(header.read_bytes(), initial)
            self.assertEqual(header.stat().st_mtime_ns, sentinel)
            symbols.write_text("png_fixture_public\npng_new_export\n", encoding="utf-8")
            configure()
            self.assertIn(b"#define png_new_export wot_sf_png_new_export", header.read_bytes().splitlines())
            self.assertNotEqual(header.stat().st_mtime_ns, sentinel)


if __name__ == "__main__":
    unittest.main()
