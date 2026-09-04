from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[1]
PORT_ROOT = ROOT
BRIDGE_PATH = PORT_ROOT / 'native' / 'offline_instance_guard_native.pyd'
SOURCE_PATH = PORT_ROOT / 'native' / 'offline_instance_guard_native.c'
BUILD_PATH = PORT_ROOT / 'tools' / 'build_instance_guard_native.sh'


def _cstring(data, offset):
    end = data.index(b'\0', offset)
    return data[offset:end].decode('ascii')


class _PeImage(object):
    def __init__(self, data):
        self.data = data
        self.pe_offset = struct.unpack_from('<I', data, 0x3c)[0]
        if data[self.pe_offset:self.pe_offset + 4] != b'PE\0\0':
            raise ValueError('missing PE signature')
        file_header = self.pe_offset + 4
        (self.machine, self.section_count, self.timestamp, _, _,
         self.optional_size, _) = struct.unpack_from(
            '<HHIIIHH', data, file_header)
        self.optional_offset = file_header + 20
        self.optional_magic = struct.unpack_from(
            '<H', data, self.optional_offset)[0]
        section_offset = self.optional_offset + self.optional_size
        self.sections = []
        for index in range(self.section_count):
            offset = section_offset + index * 40
            virtual_size, virtual_address, raw_size, raw_offset = \
                struct.unpack_from('<IIII', data, offset + 8)
            self.sections.append(
                (virtual_address, max(virtual_size, raw_size), raw_offset))

    def rva_offset(self, rva):
        for virtual_address, extent, raw_offset in self.sections:
            if virtual_address <= rva < virtual_address + extent:
                return raw_offset + rva - virtual_address
        raise ValueError('RVA is outside all sections: 0x%x' % rva)

    def directory_rva(self, index):
        return struct.unpack_from(
            '<I', self.data, self.optional_offset + 96 + index * 8)[0]

    def exports(self):
        offset = self.rva_offset(self.directory_rva(0))
        name_count = struct.unpack_from('<I', self.data, offset + 24)[0]
        names_rva = struct.unpack_from('<I', self.data, offset + 32)[0]
        names_offset = self.rva_offset(names_rva)
        result = []
        for index in range(name_count):
            name_rva = struct.unpack_from(
                '<I', self.data, names_offset + index * 4)[0]
            result.append(_cstring(self.data, self.rva_offset(name_rva)))
        return result

    def imports(self):
        offset = self.rva_offset(self.directory_rva(1))
        result = []
        while self.data[offset:offset + 20] != b'\0' * 20:
            name_rva = struct.unpack_from('<I', self.data, offset + 12)[0]
            result.append(_cstring(self.data, self.rva_offset(name_rva)))
            offset += 20
        return result


class NativeInstanceGuardArtifactTests(unittest.TestCase):
    def test_bridge_is_deterministic_x86_pe_with_one_python_initializer(self):
        image = _PeImage(BRIDGE_PATH.read_bytes())

        self.assertEqual(0x014c, image.machine)
        self.assertEqual(0x010b, image.optional_magic)
        self.assertEqual(0, image.timestamp)
        self.assertEqual(
            ['initoffline_instance_guard_native'], image.exports())

    def test_bridge_has_only_win32_dependencies_and_expected_surface(self):
        payload = BRIDGE_PATH.read_bytes()
        imports = [name.lower() for name in _PeImage(payload).imports()]

        self.assertIn('kernel32.dll', imports)
        self.assertIn('user32.dll', imports)
        self.assertFalse(any(name.startswith('python') for name in imports))
        for method_name in (
                b'release_client_guard\0',
                b'apply_standard_gameplay_mask\0',
                b'restore_standard_gameplay_mask\0',
                b'hide_process_windows\0',
                b'show_process_windows\0'):
            self.assertIn(method_name, payload)
        self.assertIn('wgc_api.dll'.encode('utf-16le'), payload)
        self.assertIn('wot_client_mutex'.encode('utf-16le'), payload)

    def test_source_and_build_are_exact_build_and_fail_closed(self):
        source = SOURCE_PATH.read_text(encoding='utf-8')
        build = BUILD_PATH.read_text(encoding='utf-8')

        self.assertIn('#define EXPECTED_PE_TIMESTAMP 0x5a6edca4U', source)
        self.assertIn('#define EXPECTED_IMAGE_BASE 0x00400000U', source)
        self.assertIn('#define EXPECTED_IMAGE_SIZE 0x0206a000U', source)
        self.assertIn('#define RVA_PY_INIT_MODULE4 0x00be1940U', source)
        self.assertIn('#define RVA_PY_INT_FROM_LONG 0x00be1180U', source)
        self.assertIn('#define RVA_WGC_CLEANUP_THUNK 0x004b7180U', source)
        self.assertIn('#define RVA_WGC_HOLDER 0x019351ecU', source)
        self.assertIn('#define RVA_WGC_WRAPPER_VTABLE 0x010ef788U', source)
        self.assertIn('#define RVA_MAPPING_SIGNATURE 0x00254fb9U', source)
        self.assertIn(
            '#define RVA_MAPPING_MASK_IMMEDIATE 0x00254fc2U', source)
        self.assertIn('MAPPING_ORIGINAL_SIGNATURE', source)
        self.assertIn('MAPPING_PATCHED_SIGNATURE', source)
        self.assertIn('VirtualProtect(mask, 1U, PAGE_EXECUTE_READWRITE', source)
        self.assertIn('FlushInstructionCache(', source)
        self.assertIn('restore_standard_gameplay_mask_internal()', source)
        self.assertIn('validate_host(base)', source)
        self.assertIn(
            'OpenMutexW(SYNCHRONIZE, FALSE, CLIENT_MUTEX_NAME)', source)
        self.assertNotIn('ReleaseMutex', source)
        self.assertIn('CloseHandle(probe)', source)
        self.assertIn('i686-w64-mingw32-gcc', build)
        self.assertIn('--no-insert-timestamp', build)
        self.assertIn('--kill-at', build)


if __name__ == '__main__':
    unittest.main()
