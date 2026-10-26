"""Enable sccache only for the generated, public Scaleform SDK projects.

The engine's /Yu and /Yc compilations cannot be cached by sccache. Keep those
projects and the client's licensed Wwise source outside this cache entirely.
"""
from pathlib import Path
import argparse
import re
import xml.etree.ElementTree as ET


NAMESPACE = "http://schemas.microsoft.com/developer/msbuild/2003"
ET.register_namespace("", NAMESPACE)
HYBRID = "'$(Configuration)|$(Platform)'=='Hybrid|Win32'"


def tag(name):
    return "{%s}%s" % (NAMESPACE, name)


def normalize_option_prefixes(options):
    # MSVC accepts both prefixes. sccache passes unknown dash switches through,
    # but treats unknown slash switches (for example /d2Zi+) as input filenames.
    # Rewrite only the beginning of each option; quoted macro values and paths
    # must retain their original text and escaping.
    return re.sub(r'(?:[^\s"]|"(?:\\.|[^"\\])*")+',
                  lambda match: re.sub(r'^("?)/(?=[A-Za-z])', r'\1-', match[0]),
                  options)


def configure_projects(build, compiler):
    """Validate every project before writing any changed project files."""
    projects = sorted(Path(build).glob("wot_sf_*.vcxproj"))
    if not projects:
        raise ValueError("No generated Scaleform SDK projects were found")
    pending = []
    for path in projects:
        tree = ET.parse(path)
        root = tree.getroot()
        groups = [group for group in root.findall(tag("ItemDefinitionGroup"))
                  if re.sub(r"\s", "", group.get("Condition", "")) == HYBRID]
        if len(groups) != 1:
            raise ValueError("Expected one Hybrid|Win32 definition in %s" % path.name)
        compile_options = groups[0].find(tag("ClCompile"))
        if compile_options is None:
            raise ValueError("Missing compiler options in %s" % path.name)
        # Inspect per-file metadata as well as target defaults: one PCH option
        # would bypass the cache, and shared /Zi PDBs are unsafe to reuse.
        for item in root.iter(tag("ClCompile")):
            for option in item:
                name = option.tag.rsplit("}", 1)[-1]
                value = option.text or ""
                if name.startswith("PrecompiledHeader") and value not in ("", "NotUsing"):
                    raise ValueError("Unexpected precompiled header in %s" % path.name)
                if name == "AdditionalOptions" and re.search(
                        r"(?:^|\s)[/-](?:Y[cu]|Fp|Z[iI](?=\s|$))", value):
                    raise ValueError("Unexpected PCH/debug compiler flag in %s" % path.name)
        debug = compile_options.find(tag("DebugInformationFormat"))
        if debug is None or debug.text != "ProgramDatabase":
            raise ValueError("Expected the reviewed /Zi compiler format in %s" % path.name)
        # /Z7 embeds compile debug information in each cached object. The
        # unchanged final /DEBUG link still produces the matching client PDB.
        debug.text = "OldStyle"
        for options in compile_options.findall(tag("AdditionalOptions")):
            options.text = normalize_option_prefixes(options.text or "")
        properties = ET.Element(tag("PropertyGroup"), {"Condition": HYBRID})
        for name, value in (("TrackFileAccess", "false"),
                            ("UseMultiToolTask", "true"),
                            ("CLToolExe", str(compiler))):
            ET.SubElement(properties, tag(name)).text = value
        root.insert(0, properties)
        pending.append((path, tree))
    for path, tree in pending:
        tree.write(path, encoding="utf-8", xml_declaration=True)
    return len(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("compiler", type=Path)
    args = parser.parse_args()
    count = configure_projects(args.build, args.compiler)
    print("Enabled compilation cache for %d public Scaleform projects; engine PCH and licensed SDK builds are unchanged" % count)


if __name__ == "__main__":
    main()
