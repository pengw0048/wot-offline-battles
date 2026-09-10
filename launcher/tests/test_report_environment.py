"""What the eight 2026-09-09/10 reports could not answer about a machine.

Each test here names the report that needed the fact.  The module is pure
diagnostics, so the behaviour that matters most is that nothing it does can
stop a report from being written.
"""

import os
import shutil
import struct
import tempfile
import unittest
from unittest import mock

import core
import report_environment


def _pe_image(characteristics):
    """Build the smallest PE header the readers here actually parse."""
    head = bytearray(0x400)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", head, 0x80 + 4, 0x014C)          # i386
    struct.pack_into("<H", head, 0x80 + 22, characteristics)
    return bytes(head)


class AddressSpaceTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _image(self, characteristics):
        path = os.path.join(self.root, "WorldOfTanks.exe")
        with open(path, "wb") as stream:
            stream.write(_pe_image(characteristics))
        return path

    def test_the_shipped_client_is_large_address_aware(self):
        # #1513's COFF characteristics are 0x0123.  This is the fact that
        # decides whether 3.4 GiB of private commit is possible at all, and
        # reading it wrong halved every figure in the 2026-09-10 write-up.
        text = report_environment._address_space(self._image(0x0123))
        self.assertIn("4 GB", text)
        self.assertIn("LARGE_ADDRESS_AWARE", text)
        self.assertIn("0x0123", text)

    def test_an_image_without_the_flag_is_named_as_two_gigabytes(self):
        text = report_environment._address_space(self._image(0x0103))
        self.assertIn("2 GB", text)
        self.assertIn("NOT large-address-aware", text)

    def test_an_unreadable_image_says_so_instead_of_guessing(self):
        self.assertIn("unreadable", report_environment._address_space(
            os.path.join(self.root, "absent.exe")))
        truncated = os.path.join(self.root, "short.exe")
        with open(truncated, "wb") as stream:
            stream.write(b"MZ")
        self.assertIn("unreadable",
                      report_environment._address_space(truncated))


class CrashTextTest(unittest.TestCase):
    BANNER = (b"Application E:/wot/WorldOfTanks.exe crashed 09.09.2026 at "
              b"22:33:31\r\nMessage:\r\nThe BigWorld Client has encountered "
              b"an unhandled exception and must close "
              b"(EXCEPTION_ACCESS_VIOLATION : 0xC0000005)\r\n"
              b"Memory status:\r\nSystem: 3633459200/3221225472 "
              b"[100.00% used]")

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _dump(self, payload):
        path = os.path.join(self.root, "worker.dmp")
        with open(path, "wb") as stream:
            stream.write(payload)
        return path

    def test_the_banner_is_read_out_of_the_dump(self):
        path = self._dump(b"\0" * 8192 + self.BANNER + b"\0" * 8192)
        text = report_environment.crash_text(path)
        self.assertIn("EXCEPTION_ACCESS_VIOLATION", text)
        self.assertIn("System: 3633459200/3221225472 [100.00% used]", text)

    def test_the_scan_is_the_same_at_any_chunk_size(self):
        # create_report feeds the scanner the 64 KiB chunks it is already
        # copying into the ZIP, not this module's own 4 MiB reads.
        payload = b"\0" * 100000 + self.BANNER + b"\0" * 100000
        expected = report_environment.crash_text(self._dump(payload))
        for size in (1024, 4096, 65536):
            scanner = report_environment.CrashTextScanner()
            for start in range(0, len(payload), size):
                scanner.feed(payload[start:start + size])
            self.assertEqual(expected, scanner.result(),
                             "chunk size %d disagreed" % size)

    def test_a_banner_split_across_two_chunks_still_reads(self):
        payload = b"\0" * 1000 + self.BANNER + b"\0" * 1000
        scanner = report_environment.CrashTextScanner()
        cut = 1000 + 40
        scanner.feed(payload[:cut])
        scanner.feed(payload[cut:])
        self.assertIn("EXCEPTION_ACCESS_VIOLATION", scanner.result())

    def test_the_crash_is_reported_once_at_every_chunk_boundary(self):
        # A cut inside the banner renders a truncated copy from one window
        # and the whole one from the next.  The report must carry the crash
        # once, whichever 64 KiB boundary the ZIP copy happens to land on.
        payload = b"\0" * 3000 + self.BANNER + b"\0" * 3000
        for cut in range(1, len(payload), 13):
            scanner = report_environment.CrashTextScanner()
            scanner.feed(payload[:cut])
            scanner.feed(payload[cut:])
            result = scanner.result()
            if not result:
                continue
            self.assertEqual(
                1, len(result.split("\n\n")),
                "cut at %d reported the crash more than once" % cut)
            self.assertIn("System: 3633459200/3221225472", result)

    def test_a_format_template_is_not_mistaken_for_the_event(self):
        # The same words live in the module image as printf templates. An
        # earlier version of this reader returned those instead of the crash.
        template = (b"Application %s crashed %s at %s\r\nMessage:\r\n%s\r\n"
                    b"Memory status:\r\nSystem: %lld/%lld [%3.2f%% used]")
        self.assertIsNone(
            report_environment.crash_text(self._dump(b"\0" + template + b"\0")))

    def test_a_dump_with_no_banner_reports_nothing(self):
        self.assertIsNone(
            report_environment.crash_text(self._dump(b"\xff" * 200000)))

    def test_an_unreadable_dump_reports_nothing(self):
        self.assertIsNone(report_environment.crash_text(
            os.path.join(self.root, "absent.dmp")))

    def test_the_scanner_never_raises_on_rubbish(self):
        scanner = report_environment.CrashTextScanner()
        for chunk in (b"", None, b"\0" * 10, self.BANNER):
            scanner.feed(chunk)
        self.assertIn("EXCEPTION_ACCESS_VIOLATION", scanner.result())


class SectionTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.game = os.path.join(self.root, "game")
        os.makedirs(self.game)

    def test_every_section_produces_text_for_a_bare_game_folder(self):
        # A section that raises would cost the player their whole report.
        for builder in (report_environment.installed_mods_report,
                        report_environment.missing_dependencies_report):
            text = builder(self.game)
            self.assertTrue(text.endswith("\n"))
            self.assertTrue(text.strip())
        text = report_environment.environment_report(self.game, None)
        self.assertIn("== machine", text)
        self.assertIn("== game", text)
        self.assertIn("client address space:", text)

    def _dependency_report(self, names):
        with mock.patch.object(report_environment, "_import_table",
                               return_value=names), mock.patch.object(
                report_environment, "_search_directories",
                return_value=[self.game]):
            return report_environment.missing_dependencies_report(self.game)

    def test_api_set_names_are_virtual_contracts_in_any_case(self):
        names = [
            "api-ms-win-crt-runtime-l1-1-0.dll",
            "API-MS-WIN-CRT-STDIO-L1-1-0.DLL",
            "ext-ms-win-ntuser-window-l1-1-0.dll",
            "EXT-MS-WIN-NTUSER-WINDOW-L1-1-0.DLL",
        ]

        text = self._dependency_report(names)

        contracts = [line for line in text.splitlines()
                     if line.startswith("API-SET")]
        self.assertEqual(len(names), len(contracts))
        for name in names:
            matching = [line for line in contracts if name in line]
            self.assertEqual(1, len(matching), name)
            self.assertIn("virtual contract", matching[0])
            self.assertIn("loader resolution not checked", matching[0])
        self.assertFalse(any(line.startswith("MISSING")
                             for line in text.splitlines()))
        self.assertIn("physical import files: found=0 not_found=0", text)
        self.assertIn("cannot identify the cause of 0xC0000135", text)

    def test_missing_ordinary_imports_remain_a_physical_file_observation(self):
        present = "shipped-helper.dll"
        missing = "missing-helper.dll"
        with open(os.path.join(self.game, present), "wb") as stream:
            stream.write(b"physical file only\n")

        text = self._dependency_report([
            present, missing, "api-ms-win-crt-runtime-l1-1-0.dll"])

        missing_names = [line.split()[1] for line in text.splitlines()
                         if line.startswith("MISSING")]
        self.assertEqual([missing], missing_names)
        self.assertTrue(any(line.startswith("found") and present in line
                            for line in text.splitlines()))
        self.assertIn("scanned directories", text)
        self.assertIn("physical import files: found=1 not_found=1", text)
        self.assertIn("cannot identify the cause of 0xC0000135", text)
        self.assertNotIn("this is what 0xC0000135 means", text)

    def test_existing_import_files_do_not_prove_loader_resolution(self):
        name = "shipped-helper.dll"
        # The scanner observes existence; this is not a loadable DLL.
        with open(os.path.join(self.game, name), "wb") as stream:
            stream.write(b"physical file only\n")

        text = self._dependency_report([name])

        self.assertIn("physical import files: found=1 not_found=0", text)
        self.assertIn("cannot identify the cause of 0xC0000135", text)
        self.assertNotIn("every load-time import resolved", text)
        self.assertNotIn("would come from a dependency", text)

    def test_loose_mod_files_are_listed_not_just_packaged_ones(self):
        # Blame for the address-space growth turned on whether a worker was
        # really mod-free, and a loose res_mods script announces nothing.
        loose = os.path.join(self.game, "res_mods", "0.9.22.0.1", "scripts")
        os.makedirs(loose)
        with open(os.path.join(loose, "mod_quiet.py"), "wb") as stream:
            stream.write(b"# silent\n")
        text = report_environment.installed_mods_report(self.game)
        self.assertIn("mod_quiet.py", text)

    def test_the_listing_is_capped_instead_of_unbounded(self):
        directory = os.path.join(self.game, "mods", "0.9.22.0.1")
        os.makedirs(directory)
        for index in range(report_environment.MODS_LISTING_LIMIT + 25):
            with open(os.path.join(directory, "m%04d.wotmod" % index),
                      "wb") as stream:
                stream.write(b"x")
        text = report_environment.installed_mods_report(self.game)
        self.assertIn("truncated", text.lower())

    def test_the_environment_names_wine_when_wine_is_present(self):
        # `C:\users\xuser` in report 20260909-234646 was a hint, not a
        # diagnosis. Wine exports this from ntdll and Windows does not.
        with mock.patch.object(report_environment, "_wine_version",
                               return_value="9.0"):
            text = report_environment.environment_report(self.game, None)
        self.assertIn("wine: 9.0", text)

    def test_the_environment_reports_the_machine_memory_totals(self):
        with mock.patch.object(report_environment, "_memory_status",
                               return_value={
                                   "load": 71,
                                   "totalPhys": 8 * 1024 ** 3,
                                   "availPhys": 2 * 1024 ** 3,
                                   "totalPageFile": 16 * 1024 ** 3,
                                   "availPageFile": 4 * 1024 ** 3,
                                   "totalVirtual": 0xFFFE0000,
                                   "availVirtual": 0x80000000,
                               }):
            text = report_environment.environment_report(self.game, None)
        self.assertIn("load=71%", text)
        self.assertIn("phys=2048 MB/8192 MB", text)
        self.assertIn("virtual (this launcher process): 2048 MB/4096 MB", text)

    def test_a_machine_without_the_windows_api_still_produces_a_report(self):
        with mock.patch.object(report_environment, "_memory_status",
                               return_value=None):
            text = report_environment.environment_report(self.game, None)
        self.assertIn("memory: unavailable", text)


if __name__ == "__main__":
    unittest.main()


class OwnPayloadIsNotListedTest(unittest.TestCase):
    """The listing must not spend its budget on this launcher's own files.

    `prepare_worker_resource_root` unpacks the whole port `res` tree into
    `mods/configs/offline_lan_0922/worker_res`.  On a machine that ran the
    isolated worker that is hundreds of files, and walking them would
    truncate the listing before it reached the player's third-party mods -
    which is the only thing this section exists to show.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.game = os.path.join(self.root, "game")
        own = os.path.join(self.game, core.WORKER_RESOURCE_ROOT_0922)
        os.makedirs(own)
        for index in range(500):
            with open(os.path.join(own, "payload%03d.py" % index),
                      "wb") as stream:
                stream.write(b"# ours\n")
        self.third_party = os.path.join(self.game, "mods", "0.9.22.0.1")
        os.makedirs(self.third_party)
        with open(os.path.join(self.third_party, "zz_aslain.wotmod"),
                  "wb") as stream:
            stream.write(b"x")
        loose = os.path.join(self.game, "res_mods", "0.9.22.0.1", "scripts")
        os.makedirs(loose)
        with open(os.path.join(loose, "mod_quiet.py"), "wb") as stream:
            stream.write(b"# silent\n")

    def test_a_third_party_mod_survives_our_own_five_hundred_files(self):
        text = report_environment.installed_mods_report(self.game)
        self.assertIn("zz_aslain.wotmod", text)
        self.assertIn("mod_quiet.py", text)
        self.assertNotIn("payload000.py", text)
        self.assertNotIn("truncated", text.lower())

    def test_our_own_tree_is_counted_rather_than_hidden(self):
        # Summarised, not silently dropped: how many files this launcher put
        # there is still worth knowing.
        text = report_environment.installed_mods_report(self.game)
        self.assertIn("configs/", text)
        self.assertIn("500 files, installed by this launcher", text)

    def test_other_mod_configs_remain_visible_beside_our_own_tree(self):
        directory = os.path.join(self.game, "mods", "configs", "third_party")
        os.makedirs(directory)
        with open(os.path.join(directory, "settings.json"), "wb") as stream:
            stream.write(b"{}")
        text = report_environment.installed_mods_report(self.game)
        self.assertIn("configs/third_party/settings.json", text)
        self.assertIn("configs/offline_lan_0922/", text)
        self.assertIn("500 files, installed by this launcher", text)
        self.assertNotIn("truncated", text.lower())
