"""Display names stay separate from saved route/enum identities and main language."""
import copy
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog
import unittest
from unittest import mock

import bot_tactics_labels as labels
import bot_tactics_store as storage
import bot_tactics_ui as ui_module
import i18n


class CatalogTests(unittest.TestCase):
    def test_all_41_maps_have_full_names_in_both_languages(self):
        self.assertEqual(set(storage.contract.MAPS), set(labels.MAP_NAMES))
        for language in ('zh', 'en'):
            captions = [labels.map_label(key, language) for key in storage.contract.MAPS]
            self.assertEqual(41, len(set(captions)))
            for key, caption in zip(storage.contract.MAPS, captions):
                self.assertNotEqual(key, caption)
                self.assertNotIn('_', caption)
                self.assertNotIn(' / ', caption)
                self.assertNotIn('锡城', caption)

    def test_requested_full_himmelsdorf_name_and_winter_variant(self):
        self.assertEqual('锡莫尔斯多夫', labels.map_label('04_himmelsdorf', 'zh'))
        self.assertEqual('锡莫尔斯多夫（冬季）', labels.map_label('86_himmelsdorf_winter', 'zh'))
        self.assertEqual('Himmelsdorf', labels.map_label('04_himmelsdorf', 'en'))
        self.assertEqual('Winter Himmelsdorf', labels.map_label('86_himmelsdorf_winter', 'en'))

    def test_old_map_identity_does_not_borrow_current_replacement_name(self):
        self.assertEqual('Arctic Region', labels.map_label('38_mannerheim_line', 'en'))
        self.assertEqual('喀秋莎', labels.map_label('63_tundra', 'zh'))
        self.assertEqual('哈尔科夫', labels.map_label('83_kharkiv', 'zh'))
        self.assertEqual('神圣之谷', labels.map_label('73_asia_korea', 'zh'))
        self.assertEqual('巴黎', labels.map_label('112_eiffel_tower_ctf', 'zh'))

    def test_every_pinned_builtin_route_has_translated_caption(self):
        seen = set()
        for name in storage.contract.MAPS:
            graph = storage.graph_data(name)
            for routes in graph['routes'].values():
                seen.update(route['id'] for route in routes)
        self.assertEqual(seen, set(labels.ROUTE_NAMES))
        self.assertEqual(96, len(seen))
        for key in seen:
            self.assertNotEqual(key, labels.route_label(key, 'zh'))
            self.assertNotIn('_', labels.route_label(key, 'en'))

    def test_behavior_and_policy_catalogs_cover_runtime_identifiers(self):
        self.assertEqual({'all'} | set(storage.contract.CLASSES), set(labels.ENUM_NAMES['class_tag']))
        self.assertEqual({''} | set(storage.contract.SKILLS), set(labels.ENUM_NAMES['skill']))
        self.assertEqual(set(storage.contract.PARAMETERS) | {'skill', 'crew_level'}, set(labels.PARAM_NAMES))
        self.assertEqual({'preferred', 'fixed'}, set(labels.ENUM_NAMES['policy']))


@unittest.skipUnless(os.name == 'nt' or os.environ.get('DISPLAY'), 'requires Tk display')
class LanguageUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk(); self.root.withdraw(); self.addCleanup(self.root.destroy)
        patch = mock.patch.object(ui_module.messagebox, 'showerror')
        self.errors = patch.start(); self.addCleanup(patch.stop)
        self.ui = ui_module.BotTacticsEditor(self.root, store=storage.Store(self.temp.name), language='zh')
        self.root.update()

    def choose(self, box, index):
        box.current(index); box.event_generate('<<ComboboxSelected>>'); self.root.update()

    def test_enum_selection_saves_only_canonical_values(self):
        box = self.ui.rule_boxes['skill']
        self.assertEqual(('继承上级设置','新手','普通','老兵','精英'), box.cget('values'))
        self.choose(box, 3)
        self.choose(self.ui.rule_boxes['class_tag'], 5)
        self.choose(self.ui.rule_boxes['team'], 1)
        self.ui.save_rule(); self.ui.save(True)
        rule = self.ui.store.active()['behavior'][0]
        self.assertEqual('veteran', rule['values']['skill'])
        self.assertEqual('SPG', rule['class_tag']); self.assertEqual(1, rule['team'])
        self.assertFalse(self.errors.called)
        self.ui.set_language('en'); self.root.update()
        self.assertEqual('Veteran', box.get())
        self.assertEqual('veteran', self.ui.rule_vars['skill'].get())
        self.assertEqual(rule, self.ui.store.active()['behavior'][0])

    def test_clear_inheritance_updates_model_and_displays_label(self):
        self.choose(self.ui.rule_boxes['skill'], 4)
        self.choose(self.ui.rule_boxes['skill'], 0)
        self.assertEqual('', self.ui.rule_vars['skill'].get())
        self.assertEqual('继承上级设置', self.ui.rule_boxes['skill'].get())

    def test_programmatic_canonical_values_remain_supported(self):
        self.ui.rule_vars['class_tag'].set('AT-SPG')
        self.assertEqual('坦克歼击车', self.ui.rule_boxes['class_tag'].get())
        self.ui.set_language('en'); self.root.update()
        self.assertEqual('Tank destroyer', self.ui.rule_boxes['class_tag'].get())
        self.assertEqual('AT-SPG', self.ui.rule_vars['class_tag'].get())

    def test_map_switch_does_not_change_base_or_id_after_language_toggle(self):
        for name in storage.contract.MAPS:
            self.ui.map_var.set(labels.map_label(name,'zh')); self.ui.change_map()
            meta = copy.deepcopy(storage.contract.MAPS[name])
            self.ui.set_language('en'); self.root.update()
            self.assertEqual(name, self.ui.map_name)
            self.assertEqual(labels.map_label(name,'en'), self.ui.map_var.get())
            self.assertEqual(meta, storage.contract.MAPS[name])
            self.ui.set_language('zh'); self.root.update()
        self.assertEqual({}, self.ui.document['maps'])
        self.assertFalse(self.ui.dirty())

    def test_builtin_routes_are_translated_without_changing_ids(self):
        self.ui.map_var.set('喀秋莎'); self.ui.change_map(); self.root.update()
        for key, caption in [('waterfall','瀑布'),('plateau','高原'),('village','村庄')]:
            self.assertEqual('[内置] '+caption, self.ui.items.item('builtin:'+key,'text'))
        self.ui.items.selection_set('builtin:plateau'); self.root.update()
        before=copy.deepcopy(self.ui.document)
        self.ui.set_language('en'); self.root.update()
        self.assertEqual('[Built-in] Plateau',self.ui.items.item('builtin:plateau','text'))
        self.assertEqual(('builtin','plateau'),self.ui.selection)
        self.assertEqual(before,self.ui.document)

    def test_switch_preserves_unsaved_fields_selection_view_and_history(self):
        self.ui.book.select(1); self.ui.new_position(); self.root.update()
        self.ui.profile_name.set('My 自定义方案')
        self.ui.item_vars['label'].set('waterfall 是我的名称')
        self.ui.item_vars['radius'].set('not-yet-a-number')
        self.ui.rule_vars['reaction_seconds'].set('1.')
        self.ui.view.zoom=2.5; self.ui.view.pan_x=25; self.ui.view.pan_y=-33
        snap=(copy.deepcopy(self.ui.document),copy.deepcopy(self.ui.original),
              copy.deepcopy(self.ui.undo_stack),copy.deepcopy(self.ui.redo_stack),self.ui.selection)
        before_active=self.ui.store.active_path.read_bytes()
        for language in ('en','zh','en'):
            self.ui.set_language(language);self.root.update()
            self.assertEqual('My 自定义方案',self.ui.profile_name.get())
            self.assertEqual('waterfall 是我的名称',self.ui.item_vars['label'].get())
            self.assertEqual('not-yet-a-number',self.ui.item_vars['radius'].get())
            self.assertEqual('1.',self.ui.rule_vars['reaction_seconds'].get())
            self.assertEqual((2.5,25,-33),(self.ui.view.zoom,self.ui.view.pan_x,self.ui.view.pan_y))
            self.assertEqual(1,self.ui.book.index('current'))
        self.assertEqual(snap,(self.ui.document,self.ui.original,self.ui.undo_stack,self.ui.redo_stack,self.ui.selection))
        self.assertEqual(before_active,self.ui.store.active_path.read_bytes())

    def test_rule_table_and_preview_do_not_leak_field_keys(self):
        self.ui.rule_vars['class_tag'].set('heavyTank');self.ui.rule_vars['skill'].set('regular')
        self.ui.rule_vars['reaction_seconds'].set('1.2');self.ui.save_rule()
        row=' '.join(self.ui.rules.item('0','values'))
        self.assertIn('重型坦克',row);self.assertIn('反应时间',row);self.assertIn('普通',row)
        self.assertNotIn('reaction_seconds',row);self.assertNotIn('heavyTank',row)
        with mock.patch.object(ui_module.messagebox,'showinfo') as show:
            self.ui.preview_rule()
            self.assertIn('反应时间',show.call_args.args[1])
            self.assertNotIn('regular',show.call_args.args[1])

    def test_language_switch_preserves_pending_selected_rule(self):
        self.ui.rule_vars['skill'].set('elite');self.ui.save_rule()
        self.ui.rules.selection_set('0');self.root.update()
        self.ui.rule_vars['reaction_seconds'].set('2.99')
        self.ui.set_language('en');self.root.update()
        self.assertEqual('2.99',self.ui.rule_vars['reaction_seconds'].get())
        self.assertEqual(('0',),self.ui.rules.selection())

    def test_copy_uses_translated_default_name_but_custom_names_are_not_rewritten(self):
        self.ui.map_var.set('喀秋莎');self.ui.change_map();self.root.update()
        self.ui.items.selection_set('builtin:waterfall');self.root.update();self.ui.duplicate_item();self.root.update()
        self.assertEqual('瀑布 副本',self.ui._selected()['label'])
        self.ui.set_language('en');self.root.update()
        self.assertEqual('瀑布 副本',self.ui._selected()['label'])

    def test_policy_captions_round_trip_without_localized_json(self):
        self.ui.new_route();self.root.update()
        self.ui._selected()['points']=[[-66.,306.,0],[-126.,246.,0]]
        self.choose(self.ui.item_fields['policy'][1],1)
        self.ui.update_properties();self.ui.save(True)
        self.assertEqual('fixed',self.ui.store.active()['maps']['08_ruinberg']['routes'][0]['policy'])
        self.ui.set_language('en');self.root.update()
        self.assertEqual('Fixed route',self.ui.item_fields['policy'][1].get())

    def test_closed_editor_does_not_leave_variable_traces(self):
        var=self.ui.rule_vars['skill'];self.assertTrue(var.trace_info())
        self.ui.root.destroy();self.assertFalse(var.trace_info())


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'requires Tk display')
class MainLanguageTests(unittest.TestCase):
    def test_main_selector_updates_open_and_new_windows_including_auto(self):
        import core,wot_launcher
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ,{'LOCALAPPDATA':tmp}),\
             mock.patch.object(core,'load_settings',return_value={'language':'zh','free_notice_seen':True}),\
             mock.patch.object(core,'discover_game_folders',return_value=[]):
            app=wot_launcher.LauncherWindow(tk,ttk,filedialog)
            try:
                app.bot_tactics_button.invoke();app.root.update()
                editor=app._bot_tactics_editors[0]
                self.assertEqual('zh',editor.language)
                editor.profile_name.set('未保存测试')
                app.language_choice.set('English');app._language_selected();app.root.update()
                self.assertEqual('en',editor.language)
                self.assertEqual('Ruinberg',editor.map_var.get())
                self.assertEqual('未保存测试',editor.profile_name.get())
                with mock.patch.object(i18n,'detect_system_language',return_value='zh'):
                    app.language_choice.set('Auto / 自动');app._language_selected();app.root.update()
                self.assertEqual('zh',editor.language);self.assertEqual('鲁别克',editor.map_var.get())
                editor.root.destroy()
                app.language_choice.set('English');app._language_selected();app.root.update()
                app.bot_tactics_button.invoke();app.root.update()
                self.assertEqual(1,len(app._bot_tactics_editors))
                self.assertEqual('en',app._bot_tactics_editors[0].language)
            finally:app.root.destroy()
