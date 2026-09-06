import importlib.util
import os
from pathlib import Path
import re
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (ROOT / 'src/res/scripts/client/gui/mods/offline_lan_0922/'
               'ui_i18n.py')
spec = importlib.util.spec_from_file_location('mod_ui_i18n', MODULE_PATH)
i18n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i18n)


class ModLanguageTests(unittest.TestCase):
    def test_missing_or_unknown_language_retains_english(self):
        for value in ('', 'invalid', 'auto', 'en'):
            with mock.patch.dict(os.environ, {i18n.LANGUAGE_ENV: value}):
                self.assertEqual('START BATTLE', i18n.tr('START BATTLE'))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual('START BATTLE', i18n.tr('START BATTLE'))

    def test_chinese_catalog_preserves_all_interpolation_arguments(self):
        pattern = re.compile(r'%(?:\([^)]+\))?[#0 +\-]*\d*(?:\.\d+)?[sdrf]')
        with mock.patch.dict(os.environ, {i18n.LANGUAGE_ENV: 'zh'}):
            for source, translated in i18n._ZH.items():
                self.assertEqual(pattern.findall(source),
                                 pattern.findall(translated), source)
                self.assertEqual(translated, i18n.tr(source))
                args = tuple(2 if token[-1] in 'drf' else '小鹏'
                             for token in pattern.findall(source))
                if args:
                    self.assertIsInstance(i18n.tr(source) % args, str)
            self.assertEqual('untranslated detail',
                             i18n.tr('untranslated detail'))

    def test_utf8_names_are_decoded_before_formatting(self):
        name = '小鹏'
        self.assertEqual(name, i18n.as_text(name.encode('utf-8')))
        self.assertEqual(name, i18n.as_text(name))
        self.assertEqual('bad\ufffd', i18n.as_text(b'bad\xff'))

    def test_mod_font_uses_dynamic_glyphs_and_a_cjk_family(self):
        path = ROOT / 'src/res/system/fonts' / i18n.CHINESE_FONT
        font = ET.parse(path).getroot()
        self.assertEqual('Microsoft YaHei',
                         font.findtext('creation/sourceFont'))
        self.assertIsNone(font.find('generated'))
        self.assertIsNone(font.find('creation/startChar'))
        self.assertIsNone(font.find('creation/endChar'))
