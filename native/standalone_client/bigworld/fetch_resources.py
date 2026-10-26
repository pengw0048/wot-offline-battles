#!/usr/bin/env python3
"""Fetch the public BigWorld engine runtime resources from pinned SVN r10."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
import time
from urllib.parse import unquote
from urllib.request import urlopen
import xml.etree.ElementTree as ET


SOURCE = (
    "https://svn.code.sf.net/p/bigworld/code/!svn/rvr/10/"
    "trunk/game/res/bigworld/"
)
RUNTIME_DIRECTORIES = (
    "helpers/", "materials/", "particles/", "scripts/", "shaders/", "system/",
)


class DirectoryLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        for key, value in attrs:
            if key != "href" or value is None:
                continue
            name = unquote(value).rstrip("/")
            if (not name or name in (".", "..") or
                    any(char in name for char in "/\\:")):
                continue
            self.names.append(value)


def read_resource(path):
    for attempt in range(3):
        try:
            with urlopen(SOURCE + path, timeout=60) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1)


def list_directory(path):
    parser = DirectoryLinks()
    parser.feed(read_resource(path).decode("utf-8"))
    return [path + name for name in parser.names]


def fetch_resources(output):
    directories = list(RUNTIME_DIRECTORIES)
    files = ["resources.xml", "texture_details.xml"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        while directories:
            pending = [pool.submit(list_directory, path) for path in directories]
            directories = []
            for future in as_completed(pending):
                for path in future.result():
                    (directories if path.endswith("/") else files).append(path)

        def download(path):
            destination = output / unquote(path)
            if not destination.is_file():
                contents = read_resource(path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".download")
                temporary.write_bytes(contents)
                temporary.replace(destination)
            return destination.stat().st_size

        total_bytes = 0
        pending = [pool.submit(download, path) for path in sorted(files)]
        for count, future in enumerate(as_completed(pending), 1):
            total_bytes += future.result()
            if count % 100 == 0:
                print("Runtime resources: %d/%d files" % (count, len(files)),
                      flush=True)
    print("Public BigWorld SVN r10: %d files, %d bytes in %s" %
          (len(files), total_bytes, output), flush=True)


def write_overlay_resources(public_resources, destination):
    # Resource lookup may select only the first resources.xml. Preserve the
    # complete public table in that file, then change our startup paths.
    tree = ET.parse(public_resources)
    root = tree.getroot()
    overlay = ET.parse(Path(__file__).parent / "res" / "resources.xml")
    for section in overlay.getroot():
        target_section = root.find(section.tag)
        if target_section is None:
            target_section = ET.SubElement(root, section.tag)
        for setting in section:
            target = target_section.find(setting.tag)
            if target is None:
                target = ET.SubElement(target_section, setting.tag)
            target.text = setting.text
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    print("Merged public resource mappings and standalone overrides: %s" %
          destination, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--overlay-resources", type=Path,
                        help="Write the complete merged resources.xml here")
    args = parser.parse_args()
    output = args.output.resolve()
    fetch_resources(output)
    from patch_stock_deferred_buffers import patch_resources
    patch_resources(output)
    if args.overlay_resources is not None:
        write_overlay_resources(output / "resources.xml",
                                args.overlay_resources.resolve())


if __name__ == "__main__":
    main()
