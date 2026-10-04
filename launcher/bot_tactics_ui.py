# -*- coding: utf-8 -*-
"""Tk Bot behavior / route / artillery editor, backed by the runtime contract."""
from __future__ import annotations
import copy
import math
from pathlib import Path
import uuid
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

try:
    from . import bot_tactics_store as storage
    from . import bot_tactics_labels as labels, i18n
except ImportError:
    import bot_tactics_store as storage
    import bot_tactics_labels as labels
    import i18n

contract = storage.contract
PARAM_LABELS = labels.PARAM_NAMES


class LocalizedCombobox(ttk.Combobox):
    """Translated captions with a separate canonical model variable.

    Changing the UI language never rewrites a profile, enum or selection. The
    existing form variables remain the API used by validation and runtime code.
    """
    def __init__(self, parent, *, variable, kind, values, language, **options):
        self.canonical = variable
        self.kind = kind
        self.keys = tuple(str(value) for value in values)
        self.language = language
        self.display = tk.StringVar(master=parent)
        super().__init__(parent, textvariable=self.display, state='readonly', **options)
        self._trace = variable.trace_add('write', self._from_model)
        self.bind('<<ComboboxSelected>>', self._from_view)
        self.set_language(language)

    def _from_model(self, *unused):
        self.display.set(labels.enum_label(self.kind, self.canonical.get(), self.language))

    def _from_view(self, unused=None):
        index = self.current()
        if 0 <= index < len(self.keys):
            self.canonical.set(self.keys[index])

    def set_language(self, language):
        self.language = language
        self.configure(values=tuple(labels.enum_label(self.kind, key, language) for key in self.keys))
        self._from_model()

    def destroy(self):
        if self._trace is not None:
            self.canonical.trace_remove('write', self._trace)
            self._trace = None
        super().destroy()



class BotTacticsEditor:
    def __init__(self, parent, game_root='', store=None, language='zh', log=None):
        self.language = i18n.resolve_language(language)
        self.zh = self.language == 'zh'; self.game_root = game_root
        self._translations = {}
        self.store = store or storage.Store(); self.log = log or (lambda message: None)
        self.document = self.store.active()
        self.original = copy.deepcopy(self.document)
        self.undo_stack = []; self.redo_stack = []
        self.selection = None; self.selected_point = None; self.drag = None
        self.graph_cache = {}; self.image_cache = {}; self.background = None; self.photo = None
        self.root = tk.Toplevel(parent)
        self.root.title(self.tr('Bot 配置与地图战术', 'Bot configuration and map tactics'))
        self.root.geometry('1200x820'); self.root.minsize(1000, 700)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self._build()
        self.map_name = '08_ruinberg'; self.team = 1
        self._load_map(); self._refresh_rules(); self._refresh_profiles()
        self.root.bind('<Control-z>', lambda e: self.undo())
        self.root.bind('<Control-y>', lambda e: self.redo())
        self.root.bind('<Control-s>', lambda e: self.save(False))

    def tr(self, zh, en):
        self._translations[zh] = self._translations[en] = (zh, en)
        return zh if self.zh else en

    def _translated_text(self, text):
        pair = self._translations.get(text)
        if pair:
            return pair[0 if self.zh else 1]
        # Numeric limits, coordinates and a profile hash may follow a caption.
        for old in sorted(self._translations, key=len, reverse=True):
            if old and text.startswith(old):
                pair = self._translations[old]
                return pair[0 if self.zh else 1] + text[len(old):]
        return text

    def set_language(self, language):
        """Refresh an open window without discarding pending edits or view state."""
        language = i18n.resolve_language(language)
        if language == self.language:
            return
        self.language = language; self.zh = language == 'zh'
        def refresh(widget):
            if isinstance(widget, LocalizedCombobox):
                widget.set_language(language)
            if isinstance(widget, (ttk.Label, ttk.Button, ttk.Checkbutton, ttk.LabelFrame)):
                text = str(widget.cget('text'))
                widget.configure(text=self._translated_text(text))
            if isinstance(widget, ttk.Notebook):
                for tab in widget.tabs():
                    widget.tab(tab, text=self._translated_text(widget.tab(tab, 'text')))
            for child in widget.winfo_children():
                refresh(child)
        refresh(self.root)
        self.root.title(self.tr('Bot 配置与地图战术', 'Bot configuration and map tactics'))
        for key in ('team', 'class', 'slot', 'values'):
            self.rules.heading(key, text=self._translated_text(self.rules.heading(key, 'text')))
        self.map_labels = {labels.map_label(name, language): name for name in sorted(contract.MAPS)}
        self.map_box.configure(values=sorted(self.map_labels))
        self.map_var.set(labels.map_label(self.map_name, language))
        self.status.set(self._translated_text(self.status.get()))
        self._refresh_rules()
        # Update tree captions in-place: no selection callbacks, draft rewrites
        # or changes to waypoint selection, zoom, pan or partially edited forms.
        for iid in self.items.get_children():
            kind, identity = iid.split(':', 1)
            if kind == 'builtin':
                text = self.tr('[内置] ', '[Built-in] ') + labels.route_label(identity, language)
            else:
                item = next(v for v in self.entry()[kind] if v['id'] == identity)
                text = self.tr('路线 ', 'Route ') if kind == 'routes' else self.tr('炮位 ', 'SPG ')
                text += item['label']  # user-authored names are never translated
            self.items.item(iid, text=text)
        self.redraw()

    def _build(self):
        head = ttk.Frame(self.root, padding=10); head.pack(fill='x')
        ttk.Label(head, text=self.tr('方案名称', 'Profile')).pack(side='left')
        self.profile_name = tk.StringVar(value=self.document['name'])
        self.profile_box = ttk.Combobox(head, textvariable=self.profile_name, width=28)
        self.profile_box.pack(side='left', padx=6)
        for zh, en, command in (
            ('打开', 'Open', self.open_profile), ('新建', 'New', self.new_profile),
            ('导入', 'Import', self.import_profile), ('导出', 'Export', self.export_profile),
            ('恢复默认草稿', 'Default draft', self.default_profile)):
            ttk.Button(head, text=self.tr(zh,en), command=command).pack(side='left', padx=2)
        self.book = ttk.Notebook(self.root)
        behavior = ttk.Frame(self.book, padding=12); maps = ttk.Frame(self.book, padding=6)
        self.book.add(behavior, text=self.tr('行为参数', 'Behavior'))
        self.book.add(maps, text=self.tr('进攻路线 / 火炮炮位', 'Routes / SPG positions'))
        self._build_behavior(behavior); self._build_maps(maps)
        foot = ttk.Frame(self.root, padding=10); foot.pack(fill='x')
        self.status = tk.StringVar(value=self.tr('已载入当前配置。修改先作为草稿，不影响进行中的战斗。',
                                               'Loaded active profile. Editing does not change an ongoing battle.'))
        ttk.Label(foot, textvariable=self.status, wraplength=670).pack(side='left', fill='x', expand=True)
        ttk.Button(foot, text=self.tr('保存草稿', 'Save draft'), command=lambda:self.save(False)).pack(side='left', padx=5)
        ttk.Button(foot, text=self.tr('保存并应用（下一局）', 'Apply next battle'), command=lambda:self.save(True)).pack(side='left')
        ttk.Button(foot, text=self.tr('关闭', 'Close'), command=self.close).pack(side='left', padx=5)
        self.book.pack(fill='both', expand=True, padx=10)

    def _build_behavior(self, parent):
        ttk.Label(parent, text=self.tr(
            '留空表示继承。覆盖顺序：全局 → 队伍 → 车型 → 指定槽位。只调整 Bot 决策；乘员等级另列。\n'
            '射速、装甲、穿深、炮弹散布规律和碰撞规则不在此处修改。明确设置难度时优先于阵容难度。',
            'Blank values inherit. Global → team → class → slot. Crew level is independent.\n'
            'No armour, penetration, reload-law, dispersion-law or collision cheats. Explicit skill overrides lineup skill.'),
                  justify='left').pack(anchor='w', pady=(0,12))
        main = ttk.Frame(parent); main.pack(fill='both', expand=True)
        self.rules = ttk.Treeview(main, columns=('team','class','slot','values'), show='headings', height=14)
        for key,label,width in [('team',self.tr('队伍','Team'),60),('class',self.tr('车型','Class'),90),
                                ('slot',self.tr('槽位','Slot'),55),('values',self.tr('覆盖参数','Overrides'),310)]:
            self.rules.heading(key,text=label); self.rules.column(key,width=width)
        self.rules.pack(side='left',fill='both',expand=True)
        self.rules.bind('<<TreeviewSelect>>',self._select_rule)
        form=ttk.Frame(main,padding=(14,0));form.pack(side='left',fill='y')
        self.rule_vars={}; self.rule_boxes={}
        for row,(name,label,values) in enumerate([
            ('team',self.tr('队伍','Team'),('0','1','2')),
            ('class_tag',self.tr('车型','Class'),('all',)+contract.CLASSES),
            ('slot',self.tr('槽位','Slot'),('',)+tuple(str(i) for i in range(1,16))),
            ('skill',self.tr('难度','Difficulty'),('',)+contract.SKILLS),
            ('crew_level',self.tr('乘员等级','Crew level'),('','75','90','100'))]):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',pady=5)
            var=tk.StringVar(value='0' if name=='team' else 'all' if name=='class_tag' else '')
            self.rule_vars[name]=var
            box=LocalizedCombobox(form,variable=var,kind=name,values=values,language=self.language,width=21)
            box.grid(row=row,column=1,sticky='ew');self.rule_boxes[name]=box
        for row,key in enumerate(contract.PARAMETERS,5):
            lo,hi=contract.PARAMETERS[key]
            label=self.tr(*PARAM_LABELS[key])
            ttk.Label(form,text='%s [%g–%g]'%(label,lo,hi)).grid(row=row,column=0,sticky='w',pady=5)
            var=tk.StringVar();self.rule_vars[key]=var
            ttk.Entry(form,textvariable=var,width=15).grid(row=row,column=1,sticky='ew')
        row=11
        ttk.Button(form,text=self.tr('新增 / 更新规则','Add / update rule'),command=self.save_rule).grid(row=row,column=0,columnspan=2,sticky='ew',pady=12)
        ttk.Button(form,text=self.tr('删除选中规则','Delete selected rule'),command=self.delete_rule).grid(row=row+1,column=0,columnspan=2,sticky='ew')
        ttk.Button(form,text=self.tr('显示所选范围生效参数','Preview effective values'),command=self.preview_rule).grid(row=row+2,column=0,columnspan=2,sticky='ew',pady=12)

    def _build_maps(self,parent):
        bar=ttk.Frame(parent);bar.pack(fill='x',pady=4)
        names=sorted(contract.MAPS)
        self.map_labels={labels.map_label(n,self.language):n for n in names}
        self.map_var=tk.StringVar(value=labels.map_label('08_ruinberg',self.language))
        box=ttk.Combobox(bar,textvariable=self.map_var,values=sorted(self.map_labels),state='readonly',width=35)
        self.map_box=box
        box.pack(side='left');box.bind('<<ComboboxSelected>>',lambda e:self.change_map())
        self.team_var=tk.StringVar(value='1')
        t=LocalizedCombobox(bar,variable=self.team_var,kind='team',values=('1','2'),language=self.language,width=9)
        ttk.Label(bar,text=self.tr('出生队伍','Spawn team')).pack(side='left',padx=6);t.pack(side='left')
        t.bind('<<ComboboxSelected>>',lambda e:self.change_map(),add='+')
        self.route_class_var = tk.StringVar(value='heavyTank')
        ttk.Label(bar,text=self.tr('显示车型','Show class')).pack(side='left',padx=6)
        scope = LocalizedCombobox(bar,variable=self.route_class_var,
            kind='class_tag',values=('all',)+contract.CLASSES,
            language=self.language,width=13)
        scope.pack(side='left')
        scope.bind('<<ComboboxSelected>>',lambda e:self.change_route_class(),add='+')
        self.base_label=ttk.Label(bar,text='');self.base_label.pack(side='left',padx=8)
        ttk.Label(bar,text=self.tr('模式：标准战','Mode: standard')).pack(side='right')
        tools=ttk.Frame(parent);tools.pack(fill='x')
        for zh,en,command in [('新建路线','New route',self.new_route),('新建炮位','New SPG',self.new_position),
                              ('复制','Duplicate',self.duplicate_item),('删除','Delete',self.delete_item),
                              ('撤销','Undo',self.undo),('重做','Redo',self.redo),
                              ('检查本图','Check map',self.check_map),('适应窗口','Fit view',self.fit_view),
                              ('导入底图','Load image',self.load_background)]:
            ttk.Button(tools,text=self.tr(zh,en),command=command).pack(side='left',padx=1,pady=5)
        body=ttk.Panedwindow(parent,orient='horizontal');body.pack(fill='both',expand=True)
        left=ttk.Frame(body,width=185);centre=ttk.Frame(body);right=ttk.Frame(body,width=260)
        body.add(left,weight=0);body.add(centre,weight=1);body.add(right,weight=0)
        self.items=ttk.Treeview(left,show='tree',height=16,selectmode='browse');self.items.column('#0',width=175)
        self.items.pack(fill='both',expand=True);self.items.bind('<<TreeviewSelect>>',self.select_item)
        self.canvas=tk.Canvas(centre,background='#202529',highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.redraw())
        self.canvas.bind('<ButtonPress-1>',self.press)
        self.canvas.bind('<B1-Motion>',self.motion)
        self.canvas.bind('<ButtonRelease-1>',self.release)
        self.canvas.bind('<Double-Button-1>',self.edit_point_condition)
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<Button-4>',lambda e:self.wheel(e,1))
        self.canvas.bind('<Button-5>',lambda e:self.wheel(e,-1))
        self.canvas.bind('<ButtonPress-2>',self.pan_start)
        self.canvas.bind('<B2-Motion>',self.pan_move)
        self.canvas.bind('<Delete>',lambda e:self.delete_point())
        self.canvas.bind('<Motion>',self.cursor)
        self.coords=tk.StringVar(value='');ttk.Label(centre,textvariable=self.coords).pack(anchor='w')
        self.map_status=ttk.Label(centre,text='',wraplength=520);self.map_status.pack(anchor='w')
        self.item_vars={};self.item_fields={}
        for row,(key,zh,en) in enumerate([
            ('label','名称','Label'),('policy','路线模式','Route policy'),('capacity','路线容量','Route capacity'),
            ('weight','路线分配权重','Route weight'),('slots','指定槽位，逗号分隔','Slots, comma-separated'),
            ('radius','炮位区域半径（米）','SPG zone radius (m)'),
            ('heading','炮位朝向（度，0=北）','SPG heading (deg, 0=N)'),('priority','炮位优先级（0–9）','SPG priority (0–9)')]):
            label=ttk.Label(right,text=self.tr(zh,en));label.grid(row=row*2,column=0,sticky='w')
            var=tk.StringVar();self.item_vars[key]=var
            if key=='policy':
                widget=LocalizedCombobox(right,variable=var,kind='policy',values=('preferred','fixed'),language=self.language,width=26)
            else: widget=ttk.Entry(right,textvariable=var,width=28)
            widget.grid(row=row*2+1,column=0,sticky='ew',pady=(0,4));self.item_fields[key]=(label,widget)
        types=ttk.Frame(right);types.grid(row=16,column=0,sticky='ew');self.classes_frame=types
        self.class_vars={}
        for i,c in enumerate(contract.CLASSES):
            var=tk.BooleanVar(value=True);self.class_vars[c]=var
            ttk.Checkbutton(types,text=self.tr(*labels.ENUM_NAMES['class_tag'][c]),variable=var).grid(row=i,column=0,sticky='w')
        ttk.Button(right,text=self.tr('应用属性到草稿','Update draft properties'),command=self.update_properties).grid(row=17,column=0,sticky='ew',pady=5)
        self.points=ttk.Combobox(right,state='readonly',width=28);self.points.grid(row=18,column=0,sticky='ew')
        self.points.bind('<<ComboboxSelected>>',lambda e:self.choose_point())
        actions=ttk.Frame(right);actions.grid(row=19,column=0,sticky='ew');self.point_actions=actions
        ttk.Button(actions,text=self.tr('删点','Delete point'),command=self.delete_point).pack(side='left')
        ttk.Button(actions,text=self.tr('切换驻留点','Toggle hold'),command=self.toggle_hold).pack(side='left')
        ttk.Button(actions,text=self.tr('停留条件','Wait condition'),command=self.edit_point_condition).pack(side='left')
        ttk.Label(right,text=self.tr(
            '路线：点击空白处追加点；拖动节点。\nShift+点击：插在选中节点后。\n双击节点设置停留时间，-1 为一直停留。\n带停留条件的路线按点位移动，仍可瞄准射击。\n切换车型只隐藏其他路线。\n滚轮缩放；中键拖动。',
            'Click empty space to add a waypoint; drag nodes.\nShift-click inserts after selected node.\nDouble-click a node to set wait seconds; -1 holds indefinitely.\nTimed routes control movement while retaining aim/fire.\nSwitching class only hides other routes.\nWheel zoom; middle-drag pan.'),justify='left',wraplength=250).grid(row=20,column=0,sticky='w',pady=8)

    def checkpoint(self):
        self.undo_stack.append(copy.deepcopy(self.document));self.undo_stack=self.undo_stack[-50:];self.redo_stack=[]

    def dirty(self):
        return self.document!=self.original or self.profile_name.get()!=self.document['name']

    def mark(self):
        self.status.set(self.tr('草稿已修改，尚未应用。','Draft changed; not applied.'));self.redraw()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(copy.deepcopy(self.document));self.document=self.undo_stack.pop();self._refresh_items();self._refresh_rules();self.mark()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(copy.deepcopy(self.document));self.document=self.redo_stack.pop();self._refresh_items();self._refresh_rules();self.mark()

    def _refresh_profiles(self):
        self.profile_box.configure(values=self.store.names())

    def _refresh_rules(self):
        keep = {str(i) for i in range(len(self.document['behavior']))}
        for iid in self.rules.get_children():
            if iid not in keep:self.rules.delete(iid)
        for i,r in enumerate(self.document['behavior']):
            values=(labels.enum_label('team',r['team'],self.language),
                    labels.enum_label('class_tag',r['class_tag'],self.language),
                    r['slot']+1 if r['slot']>=0 else self.tr('全部','All'),
                    labels.parameter_summary(r['values'],self.language))
            if self.rules.exists(str(i)):self.rules.item(str(i),values=values)
            else:self.rules.insert('', 'end',iid=str(i),values=values)

    def _select_rule(self,event=None):
        selected=self.rules.selection()
        if not selected:return
        r=self.document['behavior'][int(selected[0])]
        for key,var in self.rule_vars.items():
            value=r.get(key,r['values'].get(key,''))
            if key=='slot':value='' if value<0 else value+1
            var.set(str(value))

    def save_rule(self):
        try:
            values={}
            for key in ('skill','crew_level')+tuple(contract.PARAMETERS):
                value=self.rule_vars[key].get().strip()
                if value:values[key]=value if key=='skill' else int(value) if key=='crew_level' else float(value)
            slot=self.rule_vars['slot'].get()
            r=dict(team=int(self.rule_vars['team'].get()),class_tag=self.rule_vars['class_tag'].get(),
                   slot=int(slot)-1 if slot else -1,values=values)
            doc=copy.deepcopy(self.document)
            doc['behavior']=[a for a in doc['behavior'] if (a['team'],a['class_tag'],a['slot'])!=(r['team'],r['class_tag'],r['slot'])]
            doc['behavior'].append(r)
            # A new map draft may not yet have points; validate this rule alone.
            test=contract.empty();test['behavior']=doc['behavior'];clean=contract.canonical(test)
            self.checkpoint();self.document['behavior']=clean['behavior'];self._refresh_rules();self.mark()
        except (ValueError,contract.TacticsError) as e:self.error(e)

    def delete_rule(self):
        selected=self.rules.selection()
        if selected:self.checkpoint();del self.document['behavior'][int(selected[0])];self._refresh_rules();self.mark()

    def preview_rule(self):
        from gui.mods.offline_lan_0922 import bot_gunnery
        team=int(self.rule_vars['team'].get()) or 1;tag=self.rule_vars['class_tag'].get();slot=self.rule_vars['slot'].get()
        tag=tag if tag!='all' else 'heavyTank';slot=int(slot)-1 if slot else 0
        values=contract.effective(self.document,team,tag,slot)
        rating=bot_gunnery.rating_for_skill(values.get('skill','regular'))
        params=bot_gunnery.rating_parameters(rating,values)
        messagebox.showinfo(self.tr('生效值预览','Effective values'),
            self.tr('示例：队伍%d / %s / 槽位%d；未指定难度按普通演示。\n','Example: Team %d / %s / slot %d. Unpinned skill uses Regular for this preview.\n')%(team,labels.enum_label('class_tag',tag,self.language),slot+1)+
            '\n'.join('%s = %s'%(labels.parameter_label(k,self.language),labels.parameter_value(k,v,self.language)) for k,v in params.items()),parent=self.root)

    def entry(self):
        return self.document['maps'].get(self.map_name,dict(mode='regular',resource_sha256=contract.MAPS[self.map_name]['resource_sha256'],routes=[],positions=[]))

    def _ensure_entry(self):
        return self.document['maps'].setdefault(self.map_name, self.entry())

    def _selected(self):
        if self.selection:
            kind,identity=self.selection
            if kind=='builtin':return None
            return next((v for v in self.entry()[kind] if v['id']==identity),None)
        return None

    def _load_map(self):
        meta=contract.MAPS[self.map_name]
        self.view=storage.ViewTransform(meta['bounds'],700,600)
        self.base_label.config(text=self.tr('本侧基地坐标：','Own base: ')+str(meta['bases'][self.team-1]))
        if self.map_name not in self.graph_cache:
            try:self.graph_cache[self.map_name]=storage.graph_data(self.map_name)
            except Exception as e:self.error(e);self.graph_cache[self.map_name]=None
        try:
            if self.map_name not in self.image_cache:
                try:
                    image=storage.minimap_image(self.game_root,self.map_name);label=self.tr('原版客户端小地图','Original client minimap')
                except (OSError,ValueError,KeyError,ImportError):
                    graph=self.graph_cache[self.map_name]
                    image=storage.navigation_image(graph) if graph else None
                    label=self.tr('导航栅格预览（非原版底图）；可导入完整小地图。','Navigation raster, NOT original map artwork; a full-map image can be loaded.')
                self.image_cache[self.map_name]=(image,label)
            self.background,label=self.image_cache[self.map_name]
        except (OSError,ValueError,ImportError,TypeError) as e:
            self.background=None;label=str(e)
        self.map_status.config(text=self._translated_text(label))
        self.selection=None;self.selected_point=None;self._refresh_items();self.redraw()

    def change_map(self):
        self.map_name=self.map_labels[self.map_var.get()];self.team=int(self.team_var.get());self._load_map()

    def change_route_class(self):
        self.selection=None;self.selected_point=None;self.drag=None
        self._refresh_items()

    def _visible_for_class(self, kind, item):
        tag = self.route_class_var.get()
        if tag == 'all':
            return True
        if kind == 'positions':
            return tag == 'SPG'
        if kind == 'builtin':
            weights = item.get('class_weights') or {}
            return weights.get(tag, 0.0) > 0.0 if weights else tag != 'SPG'
        return tag in item['classes']

    def _refresh_items(self):
        self.items.delete(*self.items.get_children())
        for kind in ('routes','positions'):
            for item in self.entry()[kind]:
                if item['team']==self.team and self._visible_for_class(kind,item):
                    self.items.insert('','end',iid=kind+':'+item['id'],text=(self.tr('路线 ','Route ') if kind=='routes' else self.tr('炮位 ','SPG '))+item['label'])
        graph=self.graph_cache.get(self.map_name)
        if graph:
            for route in graph.get('routes',{}).get(str(self.team),()):
                if not self._visible_for_class('builtin',route):continue
                self.items.insert('','end',iid='builtin:'+route['id'],text=self.tr('[内置] ','[Built-in] ')+labels.route_label(route['id'],self.language))
        if self.selection and self.items.exists(':'.join(self.selection)):
            self.items.selection_set(':'.join(self.selection))
        else:self.selection=None
        self._refresh_properties();self.redraw()

    def select_item(self,event=None):
        values=self.items.selection()
        self.selection=tuple(values[0].split(':',1)) if values else None
        self.selected_point=None;self._refresh_properties();self.redraw()

    def _refresh_properties(self):
        item=self._selected()
        route = self.selection and self.selection[0]=='routes'
        allowed = {'label','policy','capacity','weight','slots'} if route else {'label','radius','heading','priority'} if item else set()
        for key, widgets in self.item_fields.items():
            for widget in widgets:
                widget.grid() if key in allowed else widget.grid_remove()
        for widget in (self.classes_frame,self.points,self.point_actions):
            widget.grid() if route else widget.grid_remove()
        for key,var in self.item_vars.items():
            val=(item or {}).get(key,'')
            if key=='slots' and isinstance(val,list):val=','.join(str(v+1) for v in val)
            var.set(str(val))
        for key,var in self.class_vars.items():var.set(key in (item or {}).get('classes',()))
        pts=(item or {}).get('points',[])
        self.points.config(values=['%d: %.1f, %.1f%s'%(i+1,p[0],p[1],
            (' [%s]'%self.tr('一直停留','Hold')) if len(p)>3 and p[3]<0 else
            (' [%gs]'%p[3]) if len(p)>3 and p[3]>0 else ' *' if p[2] else '')
            for i,p in enumerate(pts)])
        if self.selected_point is not None and self.selected_point<len(pts):self.points.current(self.selected_point)
        else:self.points.set('')

    def new_route(self):
        self.checkpoint();identity='r_'+uuid.uuid4().hex[:12]
        scope=self.route_class_var.get()
        self._ensure_entry()['routes'].append(dict(id=identity,label=self.tr('新路线','New route'),team=self.team,
            classes=list(contract.CLASSES[:-1]) if scope=='all' else [scope],slots=[],policy='preferred',capacity=6,weight=1.0,points=[]))
        self.selection=('routes',identity);self._refresh_items();self.mark()
        self.status.set(self.tr('在地图上点击添加路径点；最多16个。','Click the map to add up to 16 route points.'))

    def new_position(self):
        self.route_class_var.set('SPG')
        self.checkpoint();identity='p_'+uuid.uuid4().hex[:12]
        base=contract.MAPS[self.map_name]['bases'][self.team-1]
        other=contract.MAPS[self.map_name]['bases'][2-self.team]
        heading=math.degrees(math.atan2(other[0]-base[0],other[1]-base[1]))
        self._ensure_entry()['positions'].append(dict(id=identity,label=self.tr('新炮位','New SPG zone'),team=self.team,
            point=list(base),radius=16.0,heading=heading,priority=5))
        self.selection=('positions',identity);self._refresh_items();self.mark()

    def duplicate_item(self):
        item=self._selected()
        if self.selection and self.selection[0]=='builtin':
            source=next((v for v in self.graph_cache[self.map_name].get('routes',{}).get(str(self.team),()) if v['id']==self.selection[1]),None)
            if source:
                self.checkpoint();identity='r_'+uuid.uuid4().hex[:12]
                new=dict(id=identity,label=labels.route_label(source['id'],self.language)+self.tr(' 副本',' copy'),team=self.team,
                    classes=list(contract.CLASSES[:-1]) if self.route_class_var.get()=='all' else [self.route_class_var.get()],slots=[],policy='preferred',capacity=source.get('capacity',6),weight=1.0,
                    points=[[float(p[0]),float(p[1]),int(bool(p[2]))] for p in source['waypoints']])
                self._ensure_entry()['routes'].append(new);self.selection=('routes',identity);self._refresh_items();self.mark()
            return
        if item is None:return
        self.checkpoint();new=copy.deepcopy(item);new['id']=('r_' if self.selection[0]=='routes' else 'p_')+uuid.uuid4().hex[:12];new['label']+=self.tr(' 副本',' copy')
        self.entry()[self.selection[0]].append(new);self.selection=(self.selection[0],new['id']);self._refresh_items();self.mark()

    def delete_item(self):
        item=self._selected()
        if item is None:return
        if not messagebox.askyesno(self.tr('删除','Delete'),item['label']+'?',parent=self.root):return
        self.checkpoint();self.entry()[self.selection[0]].remove(item);self.selection=None;self._refresh_items();self.mark()

    def update_properties(self):
        item=self._selected()
        if item is None:return
        try:
            new=copy.deepcopy(item);new['label']=self.item_vars['label'].get()
            if self.selection[0]=='routes':
                new.update(policy=self.item_vars['policy'].get(),capacity=int(self.item_vars['capacity'].get()),
                    weight=float(self.item_vars['weight'].get()),classes=[k for k,v in self.class_vars.items() if v.get()],
                    slots=[int(v.strip())-1 for v in self.item_vars['slots'].get().split(',') if v.strip()])
            else:
                new.update(radius=float(self.item_vars['radius'].get()),heading=float(self.item_vars['heading'].get()),priority=int(self.item_vars['priority'].get()))
            # Validate atomically; malformed properties never mutate the draft.
            test=contract.empty();test['maps'][self.map_name]=copy.deepcopy(self.entry())
            test['maps'][self.map_name][self.selection[0]]=[new]
            test['maps'][self.map_name]['positions' if self.selection[0]=='routes' else 'routes']=[]
            contract.canonical(test)
            self.checkpoint();item.clear();item.update(new);self._refresh_items();self.mark();return True
        except (ValueError,contract.TacticsError) as e:self.error(e);return False

    def choose_point(self):
        self.selected_point=self.points.current();self.redraw()

    def delete_point(self):
        item=self._selected()
        if item is not None and self.selection[0]=='routes' and self.selected_point is not None and self.selected_point<len(item['points']):
            self.checkpoint();del item['points'][self.selected_point];self.selected_point=None;self._refresh_properties();self.mark()

    def toggle_hold(self):
        item=self._selected()
        if item is not None and self.selection[0]=='routes' and self.selected_point is not None and self.selected_point<len(item['points']):
            self.checkpoint();p=item['points'][self.selected_point];p[2]=1-p[2]
            p[3:]=[-1.0 if p[2] else 0.0];self._refresh_properties();self.mark()

    def edit_point_condition(self,event=None):
        item=self._selected()
        if item is None or self.selection[0]!='routes':return
        if event is not None:
            self.selected_point=next((i for i,p in enumerate(item['points'])
                if math.hypot(*(a-b for a,b in zip(self.view.screen(p),
                    (event.x,event.y))))<10),None)
        if self.selected_point is None:return
        point=item['points'][self.selected_point]
        seconds=simpledialog.askfloat(self.tr('停留条件','Wait condition'),
            self.tr('到达后停留秒数：0=直接前进，-1=一直停留。','Seconds after arrival: 0 = continue, -1 = stay here.'),
            parent=self.root,initialvalue=point[3] if len(point)>3 else 0,
            minvalue=-1,maxvalue=3600)
        if seconds is None:return
        if not math.isfinite(seconds) or -1<seconds<0:
            self.error(self.tr('请输入 -1 或 0–3600 秒。','Enter -1 or 0–3600 seconds.'));return
        self.checkpoint();point[3:]=[seconds];point[2]=int(seconds!=0)
        self.drag=None;self._refresh_properties();self.mark()
        return 'break'

    def press(self,event):
        self.canvas.focus_set();item=self._selected()
        if item is None:return
        p=self.view.world(event.x,event.y)
        if self.selection[0]=='positions':
            self.checkpoint();item['point']=list(p);self.drag=('position',None);self.mark();return
        pts=item['points']
        nearest=next((i for i,pt in enumerate(pts) if math.hypot(*(a-b for a,b in zip(self.view.screen(pt), (event.x,event.y))))<10),None)
        self.checkpoint()
        if nearest is None:
            if len(pts)>=16:self.error(self.tr('每条路线最多16点。','A route allows at most 16 points.'));return
            nearest=self.selected_point+1 if event.state & 1 and self.selected_point is not None else len(pts)
            pts.insert(nearest,list(p)+[0])
        self.selected_point=nearest;self.drag=('route',nearest);self._refresh_properties();self.mark()

    def motion(self,event):
        item=self._selected()
        if item is None or self.drag is None:return
        p=list(self.view.world(event.x,event.y))
        if self.drag[0]=='position':item['point']=p
        else:item['points'][self.drag[1]][:2]=p
        self.redraw()

    def release(self,event):
        if self.drag:self.drag=None;self._refresh_properties();self.mark()

    def cursor(self,event):
        if hasattr(self,'view'):
            x,z=self.view.world(event.x,event.y);self.coords.set('X %.2f   Z %.2f'%(x,z))

    def pan_start(self,event):self.pan=(event.x,event.y,self.view.pan_x,self.view.pan_y)
    def pan_move(self,event):
        if hasattr(self,'pan'):
            self.view.pan_x=self.pan[2]+event.x-self.pan[0];self.view.pan_y=self.pan[3]+event.y-self.pan[1];self.redraw()

    def wheel(self,event,delta=None):
        if not hasattr(self,'view'):return
        delta=delta if delta is not None else 1 if event.delta>0 else -1
        before=self.view.world(event.x,event.y);self.view.zoom=max(.5,min(4,self.view.zoom*(1.15 if delta>0 else 1/1.15)))
        x,y=self.view.screen(before);self.view.pan_x+=event.x-x;self.view.pan_y+=event.y-y;self.redraw()

    def fit_view(self):
        self.view.zoom=1;self.view.pan_x=self.view.pan_y=0;self.redraw()

    def redraw(self):
        if not hasattr(self,'view'):return
        c=self.canvas;c.delete('all');self.view.width=max(150,c.winfo_width());self.view.height=max(150,c.winfo_height())
        b=self.view.bounds;left,top,scale=self.view.frame();w=(b[2]-b[0])*scale;h=(b[3]-b[1])*scale
        if self.background is not None:
            from PIL import Image,ImageTk
            # Draw only the visible crop at high zoom; bounded pixels/memory.
            resized=self.background.resize((max(1,int(w)),max(1,int(h))),Image.Resampling.BILINEAR)
            self.photo=ImageTk.PhotoImage(resized,master=self.root);c.create_image(left,top,image=self.photo,anchor='nw')
        c.create_rectangle(left,top,left+w,top+h,outline='#c6cece')
        for i in range(11):
            c.create_line(left+w*i/10,top,left+w*i/10,top+h,fill='#6d787d',dash=(2,6))
            c.create_line(left,top+h*i/10,left+w,top+h*i/10,fill='#6d787d',dash=(2,6))
            if i<10:
                c.create_text(left+w*(i+.5)/10,top-12,text='1234567890'[i],fill='white')
                c.create_text(left-12,top+h*(i+.5)/10,text='ABCDEFGHJK'[i],fill='white')
        # Read-only built-in routes help author edits without changing them.
        graph=self.graph_cache.get(self.map_name)
        if graph:
            for r in graph.get('routes',{}).get(str(self.team),()):
                if not self._visible_for_class('builtin',r):continue
                coords=[v for p in r.get('waypoints',()) for v in self.view.screen(p)]
                if len(coords)>=4:c.create_line(*coords,fill='#818789',width=1,dash=(5,5))
        for i,p in enumerate(contract.MAPS[self.map_name]['bases'],1):
            x,y=self.view.screen(p);c.create_oval(x-12,y-12,x+12,y+12,outline='#8de3cf' if i==self.team else '#ddaaaa',width=2)
            c.create_text(x,y,text=str(i),fill='white')
        for kind in ('routes','positions'):
            for item in self.entry()[kind]:
                if item['team']!=self.team or not self._visible_for_class(kind,item):continue
                chosen=self.selection==(kind,item['id']);color='#ffc85b' if chosen else '#69cfe0'
                if kind=='routes':
                    coords=[v for p in item['points'] for v in self.view.screen(p)]
                    if len(coords)>=4:c.create_line(*coords,fill=color,width=3 if chosen else 2,arrow='last')
                    for i,p in enumerate(item['points']):
                        x,y=self.view.screen(p);rr=7 if chosen and i==self.selected_point else 5
                        c.create_oval(x-rr,y-rr,x+rr,y+rr,fill=color,outline='white' if p[2] else color)
                        c.create_text(x+10,y-10,text=str(i+1),fill='white',anchor='w')
                else:
                    x,y=self.view.screen(item['point']);radius=item['radius']*scale;angle=math.radians(item['heading'])
                    c.create_oval(x-radius,y-radius,x+radius,y+radius,outline=color,width=2)
                    c.create_rectangle(x-5,y-5,x+5,y+5,fill=color,outline='white')
                    c.create_line(x,y,x+math.sin(angle)*28,y-math.cos(angle)*28,fill=color,width=2,arrow='last')
                    c.create_text(x+12,y+12,text=item['label'],fill='white',anchor='nw')

    def check_map(self):
        try:
            doc=contract.canonical(self.document);graph=self.graph_cache.get(self.map_name)
            result=storage.runtime.authoring_check(doc,self.map_name,graph)
            names={v['id']:v['label'] for kind in ('routes','positions') for v in self.entry()[kind]}
            text='\n'.join('%s: %s'%(names.get(identity,identity),labels.validation_label(status,self.language)) for identity,status in result) or self.tr('本图没有自定义数据。','No custom data on this map.')
            text+='\n\n'+self.tr('仅验证烘焙图连通性及通用停车空间。非原版车体/弹道验证；开炮仍需游戏中检查。',
                                 'Baked connectivity / generic parking only. Native hull and firing-arc checks still run in game.')
            messagebox.showinfo(self.tr('验证结果','Validation'),text,parent=self.root)
        except Exception as e:self.error(e)

    def load_background(self):
        filename=filedialog.askopenfilename(parent=self.root,filetypes=[(self.tr('图片','Image'),'*.png *.jpg *.jpeg *.bmp')])
        if not filename:return
        if not messagebox.askokcancel(self.tr('底图校准','Map calibration'),self.tr(
            '必须是北朝上的完整地图，四边对应本图边界。不接受裁剪图；图片不会作为战术数据分发。',
            'Use a north-up, uncropped full-map image matching arena bounds. Image bytes are not included in exported tactics.'),parent=self.root):return
        try:
            from PIL import Image
            with Image.open(filename) as image:
                if image.width>4096 or image.height>4096:raise ValueError(self.tr('图片尺寸过大','Image too large'))
                self.background=image.convert('RGB')
            self.image_cache[self.map_name]=(self.background,self.tr('手动导入底图：需自行确认边界对应','Imported image: confirm arena alignment'))
            self.map_status.config(text=self.image_cache[self.map_name][1]);self.redraw()
        except Exception as e:self.error(e)

    def save(self,apply=False):
        if self._selected() is not None and not self.update_properties():
            return
        try:
            doc=copy.deepcopy(self.document);doc['name']=self.profile_name.get()
            doc=contract.canonical(doc)
            self.document=self.store.save(doc,apply);self.original=copy.deepcopy(self.document)
            self._refresh_profiles();self._refresh_items()
            short=contract.digest(self.document)[:12]
            self.status.set((self.tr('已应用，下次房主开始战斗生效。当前战斗不变。','Applied for the next host-started battle. Current battle unchanged.')
                             if apply else self.tr('草稿已保存，当前应用方案未改变。','Draft saved; active profile unchanged.'))+'  '+short)
            if apply:self.log('BOT TACTICS applied '+short)
        except Exception as e:self.error(e)

    def discard_prompt(self):
        return not self.dirty() or messagebox.askyesno(self.tr('未保存','Unsaved changes'),self.tr('放弃未保存的草稿？','Discard unsaved draft?'),parent=self.root)

    def adopt(self,doc):
        self.document=copy.deepcopy(doc);self.original=copy.deepcopy(doc);self.profile_name.set(doc['name'])
        self.undo_stack=[];self.redo_stack=[];self.selection=None;self._refresh_rules();self._refresh_items()
        self.status.set(self.tr('已打开草稿；点击应用才会更新下一局配置。','Draft opened. Apply to change the next battle configuration.'))

    def open_profile(self):
        if self.discard_prompt():
            try:self.adopt(self.store.read(self.profile_name.get()))
            except Exception as e:self.error(e)

    def new_profile(self):
        if self.discard_prompt():self.adopt(contract.empty(self.tr('新方案','New profile')))

    def default_profile(self):
        if self.discard_prompt():self.adopt(contract.empty('Default'))

    def import_profile(self):
        filename=filedialog.askopenfilename(parent=self.root,filetypes=[(self.tr('Bot 战术配置','Bot tactics'),'*.json')])
        if filename and self.discard_prompt():
            try:self.adopt(self.store.import_file(filename))
            except Exception as e:self.error(e)

    def export_profile(self):
        filename=filedialog.asksaveasfilename(parent=self.root,defaultextension='.json',filetypes=[(self.tr('Bot 战术配置','Bot tactics'),'*.json')])
        if filename:
            try:
                doc=copy.deepcopy(self.document);doc['name']=self.profile_name.get();self.store.export(doc,filename)
                self.status.set(self.tr('方案已导出，不包含游戏资源或账户数据。','Exported configuration; no game artwork or account data.'))
            except Exception as e:self.error(e)

    def error(self,error):
        messagebox.showerror(self.tr('Bot 配置','Bot configuration'),str(error),parent=self.root)

    def close(self):
        if self.discard_prompt():self.root.destroy()


def open_editor(parent,game_root='',language='zh',log=None):
    return BotTacticsEditor(parent,game_root,language=language,log=log)
