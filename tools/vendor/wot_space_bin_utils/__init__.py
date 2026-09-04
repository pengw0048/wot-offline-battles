unpacker_version = '0.5.4'


import json
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom
from .xml_utils.XmlUnpacker import XmlUnpacker
from .versioning import WoTVersion
from .space_assembler import space_assembly
from .sections.bwtb_section import BWTB_Section



class CompiledSpace:
    __bwtb = None
    __sections = None
    __wot_version: WoTVersion | None = None

    @property
    def sections(self):
        return self.__sections

    def __init__(self, stream=None, wot_version=None, wot_realm='RU', sections_to_load=None):
        if stream is not None:
            if wot_version is not None:
                self.__wot_version = WoTVersion(wot_version, wot_realm)
            self.from_bin_stream(stream, sections_to_load)

    def from_bin_stream(self, stream, sections_to_load=None):
        self.__bwtb = BWTB_Section(stream)
        self.__sections = {}
        secs = self.__wot_version.get_sections()
        for sec_cls in secs.sections:
            if sections_to_load and not sec_cls.header in sections_to_load:
                continue
            row = self.__bwtb.get_row_by_name(sec_cls.header)
            if row is None:
                print(f'Warning: {sec_cls.header} is None')
                continue
            self.__sections[sec_cls.header] = sec_cls(stream, row)

    def from_dir(self, unp_dir: Path):
        with (unp_dir / 'info.json').open('r') as fr:
            info = json.load(fr)
        assert info['unpacker_version'] == unpacker_version, (info['unpacker_version'], unpacker_version)
        self.__wot_version = WoTVersion(info['wot_version'])

        self.__sections = {}
        secs = self.__wot_version.get_sections()
        for sec_cls in secs.sections:
            try:
                sec = sec_cls(unp_dir)
                if sec._exist:
                    self.__sections[sec_cls.header] = sec
            except Exception:
                import traceback
                traceback.print_exc()
                print(sec_cls)

    def unp_to_dir(self, path: Path):
        out_dict = {
            'wot_version': self.__wot_version.ver_str,
            'unpacker_version': unpacker_version
        }
        with (path / 'info.json').open('w') as fw:
            json.dump(out_dict, fw, sort_keys=True, indent=4)
        for header, sec in self.__sections.items():
            sec.unp_to_dir(path)

    def unp_for_world_editor(self, path: Path):
        out_dir = path / 'unpacked_for_world_editor'
        out_dir.mkdir(exist_ok=True)

        settings_path = path / 'space.settings'
        if settings_path.is_file():
            with settings_path.open('rb') as f:
                settings = XmlUnpacker().read(f)
        else:
            settings = ET.Element('root')

        gchunk = ET.Element('root')

        bwt2 = self.__sections['BWT2']

        if not hasattr(bwt2, 'prepare_unp_xml'):
            return

        chunks = bwt2.prepare_unp_xml(gchunk, settings, path, out_dir, self.__sections)

        for header, sec in self.__sections.items():
            if not hasattr(sec, 'to_xml'):
                continue
            sec.to_xml(chunks)

        bwt2.flush_unp_xml(chunks)

        with (out_dir / 'global.chunk').open('w') as f:
            reparsed = minidom.parseString(ET.tostring(gchunk))
            f.write(reparsed.toprettyxml())

        with (out_dir / 'space.settings').open('w') as f:
            reparsed = minidom.parseString(ET.tostring(settings))
            f.write(reparsed.toprettyxml())

    def save_to_bin(self, bin_path: Path):
        space_assembly(bin_path, self.__sections, self.__wot_version, unpacker_version)
