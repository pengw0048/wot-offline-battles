"""Real Tk event/storage tests (Windows desktop or Linux DISPLAY required)."""
import copy
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog
import unittest
from unittest import mock

import bot_tactics_ui as ui_module
import bot_tactics_store as storage


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'requires actual Tk display')
class EditorUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=tk.Tk();self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.errors=mock.patch.object(ui_module.messagebox,'showerror');self.error_mock=self.errors.start();self.addCleanup(self.errors.stop)
        self.ui=ui_module.BotTacticsEditor(self.root,store=storage.Store(self.temp.name))
        self.ui.book.select(1);self.root.update()

    def click(self,point,shift=False):
        x,y=self.ui.view.screen(point)
        self.ui.canvas.event_generate('<ButtonPress-1>',x=int(x),y=int(y),state=1 if shift else 0)
        self.ui.canvas.event_generate('<ButtonRelease-1>',x=int(x),y=int(y));self.root.update()

    def test_opening_does_not_create_dirty_or_active_map_entries(self):
        self.assertFalse(self.ui.dirty());self.assertEqual({},self.ui.store.active()['maps'])
        self.assertEqual(41,len(self.ui.map_labels));self.assertTrue(self.ui.graph_cache['08_ruinberg'])

    def test_draw_drag_insert_undo_save_reopen_and_apply_route(self):
        self.ui.new_route();self.root.update()
        self.click((-66,306));self.click((-126,246));self.click((-186,186))
        route=self.ui._selected();self.assertEqual(3,len(route['points']))
        first=copy.deepcopy(route['points'][0]);x,y=self.ui.view.screen(first)
        end=self.ui.view.screen((-80,290))
        self.ui.canvas.event_generate('<ButtonPress-1>',x=int(x),y=int(y))
        self.ui.canvas.event_generate('<B1-Motion>',x=int(end[0]),y=int(end[1]))
        self.ui.canvas.event_generate('<ButtonRelease-1>',x=int(end[0]),y=int(end[1]));self.root.update()
        self.assertNotEqual(first,route['points'][0]);self.ui.undo();self.root.update()
        self.assertEqual(first,self.ui._selected()['points'][0]);self.ui.redo();self.root.update()
        self.ui.selected_point=0;self.click((-90,270),shift=True)
        self.assertEqual(4,len(self.ui._selected()['points']))
        self.ui.item_vars['label'].set('Test road');self.ui.item_vars['policy'].set('fixed')
        self.ui.profile_name.set('UI test');self.ui.save(False);self.root.update()
        self.assertFalse(self.error_mock.called,self.error_mock.call_args)
        self.assertEqual({},self.ui.store.active()['maps'])
        draft=self.ui.store.read('UI test');self.assertEqual('fixed',draft['maps']['08_ruinberg']['routes'][0]['policy'])
        self.ui.adopt(draft);self.ui.save(True)
        self.assertEqual(draft,self.ui.store.active());self.assertFalse(self.ui.dirty())

    def test_manual_spg_position_fields_persist_to_active_document(self):
        self.ui.new_position();self.root.update();self.click((-106,346))
        self.ui.item_vars['radius'].set('34');self.ui.item_vars['heading'].set('-180');self.ui.item_vars['priority'].set('9')
        self.ui.save(True);self.root.update();self.assertFalse(self.error_mock.called,self.error_mock.call_args)
        point=self.ui.store.active()['maps']['08_ruinberg']['positions'][0]
        self.assertEqual(34,point['radius']);self.assertEqual(-180,point['heading']);self.assertEqual(9,point['priority'])
        self.assertLess(abs(point['point'][0]+106),2)
        self.assertLess(abs(point['point'][1]-346),2)

    def test_switching_class_keeps_saved_routes_and_limits_visible_items(self):
        self.ui.new_route();self.root.update()
        self.click((-66,306));self.click((-126,246))
        heavy=copy.deepcopy(self.ui._selected())
        self.assertEqual(['heavyTank'],heavy['classes'])
        self.ui.route_class_var.set('mediumTank');self.ui.change_route_class()
        self.assertNotIn('routes:'+heavy['id'],self.ui.items.get_children())
        self.ui.new_route();self.root.update()
        self.click((-76,306));self.click((-136,246))
        medium=copy.deepcopy(self.ui._selected())
        self.assertEqual(['mediumTank'],medium['classes'])
        self.ui.profile_name.set('Separate classes');self.ui.save(False)
        saved=self.ui.store.read('Separate classes')
        self.assertEqual(2,len(saved['maps']['08_ruinberg']['routes']))
        self.ui.route_class_var.set('heavyTank');self.ui.change_route_class()
        self.assertIn('routes:'+heavy['id'],self.ui.items.get_children())
        self.assertNotIn('routes:'+medium['id'],self.ui.items.get_children())

    def test_double_click_condition_survives_save_and_reopen(self):
        self.ui.new_route();self.root.update()
        self.click((-66,306));self.click((-126,246))
        point=self.ui._selected()['points'][0]
        x,y=self.ui.view.screen(point)
        event=type('Event',(),dict(x=x,y=y))()
        with mock.patch.object(ui_module.simpledialog,'askfloat',return_value=25):
            self.ui.edit_point_condition(event)
        self.ui.profile_name.set('Timed route');self.ui.save(False)
        saved=self.ui.store.read('Timed route')
        self.assertEqual(25,saved['maps']['08_ruinberg']['routes'][0]['points'][0][3])
        self.ui.adopt(saved)
        self.assertEqual(saved,self.ui.document)

    def test_invalid_form_does_not_replace_prior_active_config(self):
        before=self.ui.store.active();self.ui.new_position();self.root.update()
        self.ui.item_vars['radius'].set('nan');self.ui.save(True)
        self.assertTrue(self.error_mock.called);self.assertEqual(before,self.ui.store.active())

    def test_behavior_form_changes_actual_schema_and_can_be_removed(self):
        self.ui.book.select(0);self.root.update()
        self.ui.rule_vars['team'].set('1');self.ui.rule_vars['class_tag'].set('SPG')
        self.ui.rule_vars['crew_level'].set('100');self.ui.rule_vars['reaction_seconds'].set('2.7')
        self.ui.save_rule();self.ui.save(True)
        raw=self.ui.store.active();actual=storage.contract.effective(raw,1,'SPG',2)
        self.assertEqual({'crew_level':100,'reaction_seconds':2.7},actual)
        self.assertEqual({},storage.contract.effective(raw,2,'SPG',2))
        self.ui.rules.selection_set('0');self.root.update();self.ui.delete_rule();self.ui.save(True)
        self.assertEqual([],self.ui.store.active()['behavior'])

    def test_copy_builtin_route_does_not_change_baked_source(self):
        graph=copy.deepcopy(self.ui.graph_cache['08_ruinberg'])
        self.ui.items.selection_set('builtin:'+graph['routes']['1'][0]['id']);self.root.update()
        self.ui.duplicate_item();self.root.update()
        self.assertEqual('routes',self.ui.selection[0]);self.assertTrue(self.ui._selected()['points'])
        self.assertEqual(graph,self.ui.graph_cache['08_ruinberg']);self.ui.save(False)
        self.assertFalse(self.error_mock.called,self.error_mock.call_args)

    def test_map_switch_keeps_world_points_and_shows_different_base(self):
        self.ui.new_position();self.root.update();self.click((-106,346));saved=copy.deepcopy(self.ui.entry()['positions'])
        self.ui.map_var.set(storage.MAP_LABELS['35_steppes']);self.ui.change_map();self.root.update()
        self.assertEqual('35_steppes',self.ui.map_name);self.assertIn('-342',self.ui.base_label.cget('text'))
        self.ui.map_var.set(storage.MAP_LABELS['08_ruinberg']);self.ui.change_map();self.root.update()
        self.assertEqual(saved,self.ui.entry()['positions'])

    def test_apply_controls_remain_visible_at_minimum_window_size(self):
        self.ui.root.geometry('1000x700');self.root.update()
        self.assertLess(self.ui.book.winfo_y(),self.ui.root.winfo_height())
        for label in self.ui.root.winfo_children():
            if isinstance(label,ttk.Frame):
                for child in label.winfo_children():
                    if isinstance(child,ttk.Button) and '下一局' in str(child.cget('text')):
                        self.assertTrue(child.winfo_ismapped())
                        self.assertLess(child.winfo_rooty(),self.ui.root.winfo_rooty()+self.ui.root.winfo_height())


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'requires actual Tk display')
class LauncherEntryTests(unittest.TestCase):
    def test_real_launcher_button_opens_real_editor(self):
        import core,wot_launcher
        with tempfile.TemporaryDirectory() as tmp,mock.patch.dict(os.environ,{'LOCALAPPDATA':tmp}),\
             mock.patch.object(core,'load_settings',return_value={'language':'zh','free_notice_seen':True}),\
             mock.patch.object(core,'discover_game_folders',return_value=[]),\
             mock.patch.object(ui_module.messagebox,'showerror') as errors:
            app=wot_launcher.LauncherWindow(tk,ttk,filedialog)
            try:
                app.root.update()
                app.bot_tactics_button.invoke();app.root.update()
                self.assertFalse(errors.called,errors.call_args)
                self.assertTrue(any(isinstance(w,tk.Toplevel) and w.title()=='Bot 配置与地图战术' for w in app.root.winfo_children()))
            finally:app.root.destroy()


if __name__=='__main__':unittest.main()
