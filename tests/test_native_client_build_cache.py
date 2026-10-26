"""Protect the public SDK cache boundary and the final link's debug symbols."""
from pathlib import Path
import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "configure_bigworld_build_cache", ROOT / "tools/configure_bigworld_build_cache.py")
CACHE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CACHE)

PROJECT = '''<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup Label="ProjectConfigurations">
    <ProjectConfiguration Include="Hybrid|Win32">
      <Configuration>Hybrid</Configuration><Platform>Win32</Platform>
    </ProjectConfiguration>
  </ItemGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.Default.props" />
  <ItemDefinitionGroup Condition="'$(Configuration)|$(Platform)'=='Hybrid|Win32'">
    <ClCompile>
      <AdditionalOptions>/d2Zi+ /Zp8 %(AdditionalOptions)</AdditionalOptions>
      <DebugInformationFormat>ProgramDatabase</DebugInformationFormat>
      <ProgramDataBaseFileName>Hybrid/sdk.pdb</ProgramDataBaseFileName>
      <PrecompiledHeader>NotUsing</PrecompiledHeader>
    </ClCompile>
    <Link><GenerateDebugInformation>true</GenerateDebugInformation></Link>
  </ItemDefinitionGroup>
  <ItemGroup><ClCompile Include="SDK/Example.cpp" /></ItemGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets" />
</Project>
'''


class NativeBuildCacheTests(unittest.TestCase):
    def test_only_public_sdk_projects_use_per_object_debug_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            sdk = build / "wot_sf_gfx.vcxproj"
            sdk.write_text(PROJECT)
            for name in ("bwclient", "moo", "probe_wwise_sdk"):
                (build / (name + ".vcxproj")).write_text(PROJECT)
            wrapper = Path("D:/build/cache-compiler/cl.exe")
            self.assertEqual(CACHE.configure_projects(build, wrapper), 1)
            root = ET.parse(sdk).getroot()
            self.assertEqual(root.findtext(".//" + CACHE.tag("CLToolExe")), str(wrapper))
            self.assertEqual(root.findtext(".//" + CACHE.tag("UseMultiToolTask")), "true")
            self.assertEqual(root.findtext(".//" + CACHE.tag("TrackFileAccess")), "false")
            self.assertEqual(root.findtext(".//" + CACHE.tag("DebugInformationFormat")), "OldStyle")
            self.assertEqual(root.findtext(".//" + CACHE.tag("GenerateDebugInformation")), "true")
            # All compile inputs, ABI options, and link options survive intact.
            before = ET.fromstring(PROJECT)
            group_path = CACHE.tag("ItemDefinitionGroup")
            before.find(group_path).find(CACHE.tag("ClCompile")).find(
                CACHE.tag("DebugInformationFormat")).text = "OldStyle"
            self.assertEqual(ET.tostring(before.find(group_path)),
                             ET.tostring(root.find(group_path)))
            self.assertEqual([item.attrib for item in before.iter(CACHE.tag("ClCompile"))],
                             [item.attrib for item in root.iter(CACHE.tag("ClCompile"))])
            for name in ("bwclient", "moo", "probe_wwise_sdk"):
                self.assertEqual((build / (name + ".vcxproj")).read_text(), PROJECT)

    def test_unreviewed_compiler_contract_does_not_partially_enable_cache(self):
        changes = [
            PROJECT.replace("NotUsing", "Use"),
            PROJECT.replace("/Zp8", '/Zp8 /Yu"pch.hpp"'),
            PROJECT.replace("/Zp8", '/Zp8 /Fp"sdk.pch"'),
            PROJECT.replace("/Zp8", "/Zp8 /Zi"),
            PROJECT.replace("ProgramDatabase", "EditAndContinue"),
            PROJECT.replace("Hybrid|Win32", "Hybrid|x64"),
            PROJECT.replace('<ClCompile Include="SDK/Example.cpp" />',
                            '<ClCompile Include="SDK/Example.cpp"><PrecompiledHeader>Use</PrecompiledHeader></ClCompile>'),
        ]
        for changed in changes:
            with self.subTest(project=changed), tempfile.TemporaryDirectory() as directory:
                build = Path(directory)
                good = build / "wot_sf_as2.vcxproj"
                good.write_text(PROJECT)
                (build / "wot_sf_gfx.vcxproj").write_text(changed)
                with self.assertRaises(ValueError):
                    CACHE.configure_projects(build, Path("compiler/cl.exe"))
                self.assertEqual(good.read_text(), PROJECT)

    def test_missing_public_sdk_projects_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "bwclient.vcxproj").write_text(PROJECT)
            with self.assertRaises(ValueError):
                CACHE.configure_projects(Path(directory), Path("compiler/cl.exe"))


if __name__ == "__main__":
    unittest.main()
