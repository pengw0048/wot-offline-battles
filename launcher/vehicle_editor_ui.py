"""Tk window for named 0.9.22 vehicle-data profiles."""

from __future__ import annotations

try:
    from . import i18n, vehicle_armor_viewer, vehicle_overlays
except ImportError:
    import i18n
    import vehicle_armor_viewer
    import vehicle_overlays


DEFAULT_MEMBER = "scripts/item_defs/vehicles/ussr/R11_MS-1.xml"
DEFAULT_FIELD = "speedLimits/forward"
DEFAULT_NATION = "ussr"
DEFAULT_VEHICLE = "R11_MS-1"


_CHINESE = {
    "0.9.22 vehicle profile: %s": "0.9.22 车辆属性方案：%s",
    "Editing profile '%s'. Choose a nation and vehicle, then a category and "
    "field. Changes are saved outside res_mods and are materialized while "
    "this profile runs in single player or is pinned by a LAN room you host. "
    "Shared guns, engines and other components show every vehicle they "
    "affect. IDs, resource paths, topology and unknown fields remain locked.":
        "正在编辑方案“%s”。请依次选择系别、车辆、类别和属性。修改保存在 "
        "res_mods 之外，会在单人游戏使用该方案、或您开房时固定该方案时生效。"
        "共用的火炮、发动机等部件会列出所有受影响的车辆。ID、资源路径、"
        "数据结构和未知属性不可修改。",
    "Nation": "系别",
    "Only nations found in the original vehicle definitions.":
        "只显示原始车辆数据中存在的系别。",
    "Vehicle": "车辆",
    "Type to search the selected nation's vehicles, then choose one or press "
    "Enter.": "输入名称搜索所选系别的车辆，然后选中或按回车。",
    "Category": "类别",
    "Only categories with an existing safe field are shown.":
        "只显示包含可安全修改属性的类别。",
    "Field": "属性",
    "The exact package member and field path stay internal.":
        "实际数据包成员和属性路径由程序内部管理。",
    "Impact": "影响范围",
    "Original value": "原始值",
    "Current value": "当前值",
    "Packed type": "数据类型",
    "Constraint": "数值限制",
    "Technical source": "数据来源",
    "Profile file": "方案文件",
    "Replacement value": "新值",
    "Save field to profile": "保存到方案",
    "Clear all edits in this profile...": "清除该方案的全部修改…",
    "Choose a vehicle field.": "请选择一项车辆属性。",
    "Choose one listed vehicle field.": "请选择列表中的一项车辆属性。",
    "Field is safe to edit.": "该属性可以安全修改。",
    "Validation error": "验证错误",
    "No supported vehicles were found in scripts.pkg.":
        "scripts.pkg 中没有找到受支持的车辆。",
    "The selected nation has no supported vehicles.":
        "所选系别中没有受支持的车辆。",
    "Choose one listed vehicle.": "请选择列表中的一辆车。",
    "No vehicles match this search.": "没有匹配当前搜索的车辆。",
    "Choose a matching vehicle or press Enter to load the first result.":
        "请选中匹配车辆，或按回车加载第一项。",
    "This vehicle has no existing fields in the safe allowlist.":
        "该车辆没有位于安全允许列表中的属性。",
    "This category has no existing fields in the safe allowlist.":
        "该类别没有位于安全允许列表中的属性。",
    "The original topology produced ambiguous field labels.":
        "原始数据结构中出现了无法区分的属性名。",
    "Profile edit saved and reparsed successfully.":
        "属性修改已保存，并已成功重新解析。",
    "Clear this vehicle profile?": "清除该车辆属性方案？",
    "Remove every saved vehicle edit from profile '%s'? The profile itself "
    "will remain.": "删除方案“%s”中保存的全部车辆修改？方案本身会保留。",
    "Profile clearing was cancelled.": "已取消清除方案。",
    "Close World of Tanks before changing vehicle data.":
        "请先关闭 World of Tanks，再修改车辆数据。",
}

_CATEGORY_CHINESE = {
    "Vehicle": "车辆", "Chassis": "悬挂装置", "Turret": "炮塔",
    "Engine": "发动机", "Fuel tank": "油箱", "Gun": "火炮",
    "Radio": "电台", "Shell": "炮弹",
}

_FIELD_CHINESE = {
    "Speed limits": "速度限制", "Forward speed": "前进速度",
    "Reverse speed": "倒车速度", "Hull": "车体", "Ammo rack": "弹药架",
    "Engine health": "发动机耐久", "Fuel tank health": "油箱耐久",
    "Radio health": "电台耐久", "Observation device": "观察装置",
    "Turret traverse": "炮塔旋转机构", "Weight": "重量",
    "Load limit": "载重上限", "Maximum health": "最大耐久",
    "Repair threshold": "修复阈值", "Power": "功率",
    "Traverse speed": "旋转速度", "Gun elevation limits": "火炮俯仰范围",
    "Hull traverse speed (deg/s)": "车体转向速度（度/秒）",
    "Gun elevation speed (deg/s)": "火炮俯仰速度（度/秒）",
    "Turret traverse speed (deg/s)": "炮塔水平转速（度/秒）",
    "Ground resistance (hard, medium, soft; lower is better)":
        "履带地形阻力（硬地 / 中地 / 软地，越低越好）",
    "Depression curve": "俯角曲线", "Elevation curve": "仰角曲线",
    "Horizontal traverse limits": "水平射界", "Hull aiming": "车体瞄准",
    "Suspension pitch limits": "悬挂俯仰范围", "Minimum pitch": "最小俯仰角",
    "Maximum pitch": "最大俯仰角", "Travel mode": "行驶模式",
    "Siege mode": "攻城模式", "Reload time": "装填时间",
    "Magazine firing rate (higher is a shorter reload)":
        "弹夹短装填射速（越高越短）",
    "Magazine": "弹夹", "Rounds per magazine": "弹夹容量（发）",
    "Aiming time": "瞄准时间", "Ammunition capacity": "弹药容量",
    "Base accuracy": "基础精度", "Dispersion factors": "扩圈系数",
    "Hull movement dispersion": "车体移动扩圈",
    "Hull rotation dispersion": "车体旋转扩圈",
    "Turret rotation dispersion": "炮塔旋转扩圈",
    "Firing dispersion": "开炮扩圈", "View range": "视野距离",
    "Firing camouflage factor": "开炮隐蔽系数",
    "Camouflage": "隐蔽值", "Moving camouflage": "移动隐蔽值",
    "Stationary camouflage": "静止隐蔽值",
    "Shell": "炮弹", "Projectile speed": "炮弹速度",
    "Maximum distance": "最大射程", "Gravity": "重力",
    "Penetration": "穿深", "Caliber": "口径", "Damage": "伤害",
    "Vehicle damage": "车辆伤害", "Module damage": "模块伤害",
    "Explosion radius": "HE 溅射范围",
}

_PACKED_TYPE_CHINESE = {
    "string": "字符串", "integer": "整数", "vector": "向量",
    "boolean": "布尔值", "compressed-string": "压缩字符串",
    "unknown": "未知",
}

_CONSTRAINT_CHINESE = (
    ("stock parser requires a positive number", "原版解析器要求正数"),
    ("stock parser requires a non-negative number", "原版解析器要求非负数"),
    ("ammunition capacity must be a non-negative integer", "弹药容量必须是非负整数"),
    ("magazine capacity must be a positive integer", "弹夹容量必须是正整数"),
    ("ground resistance must contain exactly three positive finite numbers "
     "in hard / medium / soft order; lower is better",
     "履带地形阻力必须依次包含硬地、中地、软地三个正的有限数，且越低越好"),
    ("device maximum health must be at least one", "模块最大耐久必须至少为 1"),
    ("regeneration health must be non-negative and no greater than maxHealth",
     "修复耐久必须为非负数，且不得超过 maxHealth"),
    ("penetration must contain exactly two positive finite numbers; the "
     "first value must be no less than the second",
     "穿深必须包含两个正的有限数，且第一个数不得小于第二个数"),
    ("angle may be one finite degree value or a complete 0..1 piecewise "
     "curve; every angle must be between -90 and 90 degrees",
     "可填写一个有限角度，或完整的 0..1 分段曲线；所有角度须在 -90 到 90 度之间"),
    ("horizontal traverse must contain exactly two finite degree values "
     "between -180 and 180; minimum must not exceed maximum",
     "水平射界须包含两个 -180 到 180 度之间的有限角度，且最小值不得大于最大值"),
    ("pitch angle must be finite and between -90 and 90 degrees",
     "俯仰角须为 -90 到 90 度之间的有限数值"),
    ("; finite value ", "；有限数值 "),
)


class VehicleEditorWindow(object):
    """Small advanced editor backed by the strict profile service."""

    def __init__(self, parent, game_root, profile_name, tk_module, ttk_module,
                 messagebox_module, log=None, service=vehicle_overlays,
                 language=i18n.LANGUAGE_ENGLISH, armor_viewer_factory=None):
        self._tk = tk_module
        self._ttk = ttk_module
        self._messagebox = messagebox_module
        self._service = service
        self._game_root = game_root
        self._profile_name = profile_name
        self._language = i18n.resolve_language(language)
        self._log = log or (lambda unused_message: None)
        self._armor_viewer_factory = armor_viewer_factory
        self._vehicle_choices = []
        self._selected_vehicle_choice = None
        self._filtered_vehicle_choices = []
        self._fields = []
        self._field_by_label = {}
        self._field_by_key = {}
        self._build(parent)
        self.refresh_catalog()

    def _build(self, parent):
        tk = self._tk
        self.root = tk.Toplevel(parent)
        self.root.title(
            self._t("0.9.22 vehicle profile: %s") % self._profile_name)

        frame = tk.Frame(self.root, padx=12, pady=12)
        frame.pack(fill="both", expand=True)

        explanation = self._t(
            "Editing profile '%s'. Choose a nation and vehicle, then a "
            "category and field. Changes are saved outside res_mods and are "
            "materialized only while this profile runs in single player. "
            "Shared guns, engines and other components show every vehicle "
            "they affect. IDs, resource paths, topology and unknown fields "
            "remain locked.") % self._profile_name
        tk.Label(frame, text=explanation, justify="left", anchor="w",
                 wraplength=720).grid(
                     row=0, column=0, columnspan=3, sticky="we",
                     pady=(0, 10))

        self.nation = tk.StringVar(value=DEFAULT_NATION)
        self.vehicle = tk.StringVar(value=DEFAULT_VEHICLE)
        self.category = tk.StringVar(value=self._category_label("Vehicle"))
        self.field = tk.StringVar(value="")
        self.member = tk.StringVar(value=DEFAULT_MEMBER)
        self.field_path = tk.StringVar(value=DEFAULT_FIELD)
        self.replacement = tk.StringVar(value="")
        self.original = tk.StringVar(value="-")
        self.current = tk.StringVar(value="-")
        self.packed_type = tk.StringVar(value="-")
        self.constraint = tk.StringVar(value="-")
        self.scope = tk.StringVar(value="-")
        self.source = tk.StringVar(value="-")
        self.overlay_path = tk.StringVar(value="-")
        self.status = tk.StringVar(value=self._t("Choose a vehicle field."))

        row = 1
        row, self.nation_box = self._selector_row(
            frame, row, self._t("Nation"), self.nation,
            self._t("Only nations found in the original vehicle definitions."))
        row, self.vehicle_box = self._selector_row(
            frame, row, self._t("Vehicle"), self.vehicle,
            self._t(
                "Type to search the selected nation's vehicles, then choose "
                "one or press Enter."), editable=True)
        row, self.category_box = self._selector_row(
            frame, row, self._t("Category"), self.category,
            self._t("Only categories with an existing safe field are shown."))
        row, self.field_box = self._selector_row(
            frame, row, self._t("Field"), self.field,
            self._t("The exact package member and field path stay internal."))
        self.nation_box.bind("<<ComboboxSelected>>", self.refresh_vehicles)
        self.vehicle_box.bind(
            "<<ComboboxSelected>>", self.select_vehicle_from_dropdown)
        self.vehicle_box.bind("<KeyRelease>", self.filter_vehicles)
        self.vehicle_box.bind("<Return>", self.commit_vehicle_search)
        self.vehicle_box.bind("<Escape>", self.restore_vehicle_selection)
        self.category_box.bind("<<ComboboxSelected>>", self.refresh_fields)
        self.field_box.bind("<<ComboboxSelected>>", self.inspect)

        for label, variable in (
                (self._t("Impact"), self.scope),
                (self._t("Original value"), self.original),
                (self._t("Current value"), self.current),
                (self._t("Packed type"), self.packed_type),
                (self._t("Constraint"), self.constraint),
                (self._t("Technical source"), self.source),
                (self._t("Profile file"), self.overlay_path)):
            tk.Label(frame, text=label, anchor="w").grid(
                row=row, column=0, sticky="nw", pady=(2, 0))
            tk.Label(frame, textvariable=variable, anchor="w",
                     justify="left", wraplength=570).grid(
                         row=row, column=1, columnspan=2, sticky="we",
                         padx=(8, 0), pady=(2, 0))
            row += 1

        tk.Label(frame, text=self._t("Replacement value"), anchor="w").grid(
            row=row, column=0, sticky="w", pady=(8, 0))
        self.replacement_entry = tk.Entry(
            frame, textvariable=self.replacement, width=32)
        self.replacement_entry.grid(
            row=row, column=1, sticky="we", padx=(8, 8), pady=(8, 0))
        self.apply_button = tk.Button(
            frame, text=self._t("Save field to profile"), command=self.apply)
        self.apply_button.grid(row=row, column=2, sticky="e", pady=(8, 0))
        row += 1

        self.restore_button = tk.Button(
            frame, text=self._t("Clear all edits in this profile..."),
            command=self.restore_defaults)
        self.restore_button.grid(
            row=row, column=1, columnspan=2, sticky="w", pady=(10, 0))
        row += 1

        tk.Label(frame, textvariable=self.status, anchor="w", justify="left",
                 wraplength=720).grid(
                     row=row, column=0, columnspan=3, sticky="we",
                     pady=(10, 0))

        viewer_factory = self._armor_viewer_factory
        if viewer_factory is None:
            viewer_factory = (vehicle_armor_viewer.ArmorViewerPanel
                              if hasattr(tk, "Canvas")
                              else vehicle_armor_viewer.NullArmorViewerPanel)
        self.armor_viewer = viewer_factory(
            frame, tk, self.select_field_from_viewer, self._language,
            self._game_root)
        self.armor_viewer.grid(
            row=1, column=3, rowspan=max(1, row), sticky="nsew",
            padx=(14, 0))

        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(3, weight=1)
        if hasattr(self.root, "minsize"):
            self.root.minsize(1180, 650)

    def _t(self, text):
        if self._language == i18n.LANGUAGE_CHINESE:
            return _CHINESE.get(text, text)
        return text

    def _category_label(self, label):
        if self._language == i18n.LANGUAGE_CHINESE:
            return _CATEGORY_CHINESE.get(label, label)
        return label

    def _field_label(self, label):
        if self._language != i18n.LANGUAGE_CHINESE:
            return label
        translated = []
        for part in label.split(" / "):
            if part.startswith("Armor thickness (") and part.endswith(")"):
                part = "装甲厚度（%s）" % part[len("Armor thickness ("):-1]
            else:
                part = _FIELD_CHINESE.get(part, part)
            translated.append(part)
        return " / ".join(translated)

    def _constraint_text(self, text):
        if self._language != i18n.LANGUAGE_CHINESE:
            return text
        for source, replacement in _CONSTRAINT_CHINESE:
            text = text.replace(source, replacement)
        return text

    def _scope_text(self, record):
        if self._language != i18n.LANGUAGE_CHINESE:
            return record["scope"]
        affected = tuple(record.get(
            "affectedVehicleLabels", record.get("affectedVehicles", ())))
        if record.get("shared"):
            field_parts = record.get("fieldPath", "").split("/")
            component = record.get("component")
            if not component and len(field_parts) >= 2:
                component = field_parts[1]
            return "共享%s %s；影响 %s 的 %d 辆车：%s" % (
                self._category_label(record.get("categoryLabel", "")),
                component or "-", record.get("nation", self.nation.get()),
                len(affected), ", ".join(affected))
        vehicle = record.get(
            "displayVehicle", record.get("vehicle", self.vehicle.get()))
        mode = record.get("mode")
        if mode == "all":
            return "同时存入 %s 的行驶模式与攻城模式数据；一次修改会应用到两者。" % vehicle
        if mode == "travel":
            return "仅存于 %s 的行驶模式数据。" % vehicle
        if mode == "siege":
            return "仅存于 %s 的攻城模式数据。" % vehicle
        result = "仅存于 %s；只影响该车。" % vehicle
        field_path = record.get("fieldPath", "")
        if (field_path == "hull/maxHealth" or
                (field_path.startswith("turrets") and
                 field_path.endswith("/maxHealth"))):
            result += " 实战耐久是车体最大耐久与已安装炮塔最大耐久之和。"
        return result

    def _error_text(self, message):
        if self._language != i18n.LANGUAGE_CHINESE:
            return message
        translated = _CHINESE.get(message)
        if translated is not None:
            return translated
        if message.startswith("Conflict:"):
            return "冲突：" + message[len("Conflict:"):].lstrip()
        return message

    def _selector_row(self, frame, row, label, variable, hint, button=None,
                      editable=False):
        tk = self._tk
        tk.Label(frame, text=label, anchor="w").grid(
            row=row, column=0, sticky="w")
        box = self._ttk.Combobox(
            frame, textvariable=variable, values=(), width=76,
            state="normal" if editable else "readonly")
        box.grid(row=row, column=1, columnspan=1 if button else 2,
                 sticky="we", padx=(8, 8 if button else 0))
        if button:
            text, command = button
            tk.Button(frame, text=text, command=command).grid(
                row=row, column=2, sticky="e")
        row += 1
        tk.Label(frame, text=hint, anchor="w").grid(
            row=row, column=1, columnspan=2, sticky="w", padx=(8, 0),
            pady=(0, 5))
        return row + 1, box

    def _selection(self):
        record = self._field_by_label.get(self.field.get().strip())
        if record is None:
            raise self._service.VehicleOverlayError(
                self._t("Choose one listed vehicle field."))
        return (record["member"], record["fieldPath"])

    def _show_result(self, result, success_message=None):
        self.original.set(result["originalValue"])
        self.current.set(result["currentValue"])
        packed_type = result["packedType"]
        if self._language == i18n.LANGUAGE_CHINESE:
            packed_type = _PACKED_TYPE_CHINESE.get(packed_type, packed_type)
        self.packed_type.set(packed_type)
        self.constraint.set(self._constraint_text(result["constraint"]))
        self.overlay_path.set(result["overlayPath"])
        conflict = result.get("conflict", "")
        self.apply_button.config(
            state="disabled" if conflict.startswith("Conflict:") else "normal")
        if conflict:
            self.status.set(self._error_text(conflict))
        else:
            self.status.set(
                success_message or self._t("Field is safe to edit."))

    def _show_error(self, error, clear_contract=False):
        message = self._error_text(str(error))
        self.status.set("%s: %s" % (self._t("Validation error"), message))
        self.apply_button.config(state="disabled")
        if clear_contract:
            self.original.set("-")
            self.current.set("-")
            self.packed_type.set("-")
            self.constraint.set("-")
            self.scope.set("-")
            self.source.set("-")
            self.overlay_path.set("-")
        return False

    def refresh_catalog(self):
        try:
            choices = self._service.list_vehicle_choices(self._game_root)
        except self._service.VehicleOverlayError as error:
            return self._show_error(error, clear_contract=True)
        if not choices:
            return self._show_error(
                self._t("No supported vehicles were found in scripts.pkg."),
                clear_contract=True)
        self._vehicle_choices = sorted(
            choices,
            key=lambda choice: (
                choice["nation"], self._choice_label(choice).casefold(),
                choice["vehicle"]))
        nations = sorted(set(choice["nation"] for choice in choices))
        self.nation_box.config(values=tuple(nations))
        if self.nation.get().strip() not in nations:
            self.nation.set(
                DEFAULT_NATION if DEFAULT_NATION in nations else nations[0])
        return self.refresh_vehicles()

    def refresh_members(self):
        """Compatibility alias for callers that refresh the editor."""
        return self.refresh_catalog()

    def refresh_vehicles(self, unused_event=None):
        nation = self.nation.get().strip()
        choices = self._nation_vehicle_choices(nation)
        self._show_vehicle_choices(choices)
        if not choices:
            return self._show_error(
                self._t("The selected nation has no supported vehicles."),
                clear_contract=True)
        selected = self.vehicle.get().strip()
        selected_choice = self._resolve_vehicle_choice(
            selected, choices=choices)
        if selected_choice is None:
            selected_choice = next((
                choice for choice in choices
                if choice["vehicle"] == DEFAULT_VEHICLE), choices[0])
        self.vehicle.set(self._choice_label(selected_choice))
        return self._load_vehicle_fields(selected_choice)

    @staticmethod
    def _vehicle_display_id(vehicle):
        value = str(vehicle or "")
        prefix, separator, remainder = value.partition("_")
        if (separator and remainder and
                any(character.isdigit() for character in prefix)):
            return remainder
        return value

    @classmethod
    def _choice_label(cls, choice):
        """Show the stock/localized name without repeating its resource ID."""
        label = str(choice.get("label") or choice["vehicle"])
        display_id = cls._vehicle_display_id(choice.get("vehicle", ""))
        for opening, closing in ((" (", ")"), ("（", "）")):
            suffix = opening + display_id + closing
            if display_id and label.endswith(suffix):
                return label[:-len(suffix)].rstrip()
        return label

    def _nation_vehicle_choices(self, nation=None):
        nation = self.nation.get().strip() if nation is None else nation
        return [choice for choice in self._vehicle_choices
                if choice["nation"] == nation]

    def _show_vehicle_choices(self, choices):
        self._filtered_vehicle_choices = list(choices)
        self.vehicle_box.config(values=tuple(
            self._choice_label(choice) for choice in choices))

    def _vehicle_search_matches(self, choice, query):
        query = query.strip().casefold()
        if not query:
            return True
        searchable = (
            self._choice_label(choice), choice.get("label", ""),
            choice.get("vehicle", ""),
            self._vehicle_display_id(choice.get("vehicle", "")))
        return any(query in str(value).casefold() for value in searchable)

    def _resolve_vehicle_choice(self, value, choices=None,
                                allow_partial=False):
        choices = (self._nation_vehicle_choices() if choices is None
                   else list(choices))
        needle = value.strip().casefold()
        exact = [
            choice for choice in choices
            if needle in (
                self._choice_label(choice).casefold(),
                str(choice.get("label", "")).casefold(),
                str(choice.get("vehicle", "")).casefold(),
                self._vehicle_display_id(
                    choice.get("vehicle", "")).casefold())]
        if exact:
            if self._selected_vehicle_choice in exact:
                return self._selected_vehicle_choice
            return exact[0]
        if allow_partial:
            return next((choice for choice in choices
                         if self._vehicle_search_matches(choice, value)), None)
        return None

    def filter_vehicles(self, event=None):
        if getattr(event, "keysym", "") in (
                "Return", "KP_Enter", "Escape", "Up", "Down", "Prior",
                "Next"):
            return True
        query = self.vehicle.get().strip()
        choices = [
            choice for choice in self._nation_vehicle_choices()
            if self._vehicle_search_matches(choice, query)]
        self._show_vehicle_choices(choices)
        selected_label = (
            self._choice_label(self._selected_vehicle_choice)
            if (self._selected_vehicle_choice is not None and
                self._selected_vehicle_choice.get("nation") ==
                self.nation.get().strip()) else None)
        if selected_label == self.vehicle.get().strip():
            return True
        self.apply_button.config(state="disabled")
        if not choices:
            self.status.set(self._t("No vehicles match this search."))
            return False
        self.status.set(self._t(
            "Choose a matching vehicle or press Enter to load the first "
            "result."))
        return True

    def commit_vehicle_search(self, unused_event=None):
        choice = self._resolve_vehicle_choice(
            self.vehicle.get(), allow_partial=True)
        if choice is None:
            self._show_vehicle_choices(self._nation_vehicle_choices())
            return self._show_error(
                self._t("No vehicles match this search."))
        self.vehicle.set(self._choice_label(choice))
        self._show_vehicle_choices(self._nation_vehicle_choices())
        return self._load_vehicle_fields(choice)

    def restore_vehicle_selection(self, unused_event=None):
        choices = self._nation_vehicle_choices()
        self._show_vehicle_choices(choices)
        choice = self._selected_vehicle_choice
        if choice not in choices:
            choice = choices[0] if choices else None
        if choice is None:
            return self._show_error(
                self._t("The selected nation has no supported vehicles."),
                clear_contract=True)
        self.vehicle.set(self._choice_label(choice))
        return self._load_vehicle_fields(choice)

    def select_vehicle_from_dropdown(self, unused_event=None):
        try:
            index = int(self.vehicle_box.current())
        except (AttributeError, TypeError, ValueError):
            index = -1
        if 0 <= index < len(self._filtered_vehicle_choices):
            choice = self._filtered_vehicle_choices[index]
            self.vehicle.set(self._choice_label(choice))
            self._show_vehicle_choices(self._nation_vehicle_choices())
            return self._load_vehicle_fields(choice)
        return self.refresh_vehicle_fields()

    def refresh_vehicle_fields(self, unused_event=None):
        vehicle = self.vehicle.get().strip()
        choice = self._resolve_vehicle_choice(vehicle)
        if choice is None:
            return self._show_error(
                self._t("Choose one listed vehicle."), clear_contract=True)
        self._show_vehicle_choices(self._nation_vehicle_choices())
        return self._load_vehicle_fields(choice)

    def _load_vehicle_fields(self, choice):
        self._selected_vehicle_choice = choice
        self.vehicle.set(self._choice_label(choice))
        try:
            batch = getattr(
                self._service, "list_vehicle_profile_field_choices", None)
            if batch is None:
                fields = self._service.list_vehicle_field_choices(
                    self._game_root, choice["member"])
            else:
                fields = batch(
                    self._game_root, self._profile_name, choice["member"])
        except self._service.VehicleOverlayError as error:
            return self._show_error(error, clear_contract=True)
        if not fields:
            return self._show_error(
                self._t(
                    "This vehicle has no existing fields in the safe allowlist."),
                clear_contract=True)
        self._fields = list(fields)
        self._field_by_key = dict(
            ((record["member"], record["fieldPath"]), record)
            for record in self._fields)
        self.armor_viewer.load_vehicle(choice["member"], self._fields)
        categories = []
        for record in self._fields:
            label = self._category_label(record["categoryLabel"])
            if label not in categories:
                categories.append(label)
        self.category_box.config(values=tuple(categories))
        if self.category.get().strip() not in categories:
            self.category.set(categories[0])
        return self.refresh_fields()

    def select_field_from_viewer(self, field_key):
        """Select one exact editor field after a model-surface click."""
        record = self._field_by_key.get(tuple(field_key))
        if record is None:
            return False
        category = self._category_label(record["categoryLabel"])
        fields = [candidate for candidate in self._fields
                  if self._category_label(
                      candidate["categoryLabel"]) == category]
        labels = [self._field_label(candidate["fieldLabel"])
                  for candidate in fields]
        target = self._field_label(record["fieldLabel"])
        if target not in labels or len(labels) != len(set(labels)):
            return False
        self.category.set(category)
        self._field_by_label = dict(
            (self._field_label(candidate["fieldLabel"]), candidate)
            for candidate in fields)
        self.field_box.config(values=tuple(labels))
        self.field.set(target)
        return self.inspect()

    def refresh_fields(self, unused_event=None):
        category = self.category.get().strip()
        fields = [record for record in self._fields
                  if self._category_label(record["categoryLabel"]) == category]
        if not fields:
            self.field_box.config(values=())
            return self._show_error(
                self._t(
                    "This category has no existing fields in the safe allowlist."),
                clear_contract=True)
        labels = [self._field_label(record["fieldLabel"])
                  for record in fields]
        if len(labels) != len(set(labels)):
            return self._show_error(
                self._t(
                    "The original topology produced ambiguous field labels."),
                clear_contract=True)
        self._field_by_label = dict(
            (self._field_label(record["fieldLabel"]), record)
            for record in fields)
        self.field_box.config(values=tuple(labels))
        if self.field.get().strip() not in labels:
            self.field.set(labels[0])
        return self.inspect()

    def inspect(self, unused_event=None):
        try:
            member, field_path = self._selection()
            record = self._field_by_label[self.field.get().strip()]
            result = self._service.inspect_profile_field(
                self._game_root, self._profile_name, member, field_path)
        except self._service.VehicleOverlayError as error:
            return self._show_error(error, clear_contract=True)
        self.member.set(member)
        self.field_path.set(field_path)
        self.scope.set(self._scope_text(record))
        self.source.set("%s :: %s" % (member, field_path))
        self.replacement.set(result["currentValue"])
        self._show_result(result)
        record["currentValue"] = result["currentValue"]
        self.armor_viewer.focus_field(record)
        return True

    def apply(self):
        try:
            member, field_path = self._selection()
            result = self._service.apply_profile_edit(
                self._game_root, self._profile_name, member, field_path,
                self.replacement.get())
        except self._service.VehicleOverlayError as error:
            return self._show_error(error)
        message = "Profile edit saved and reparsed successfully."
        self._show_result(result, self._t(message))
        record = self._field_by_key.get((member, field_path))
        if record is not None:
            record["currentValue"] = result["currentValue"]
        self.armor_viewer.update_field(
            member, field_path, result["currentValue"])
        self._log("Vehicle data editor: %s" % message)
        return True

    def restore_defaults(self):
        if not self._messagebox.askyesno(
                self._t("Clear this vehicle profile?"),
                self._t(
                    "Remove every saved vehicle edit from profile '%s'? "
                    "The profile itself will remain.") % self._profile_name,
                parent=self.root, icon="warning"):
            self.status.set(self._t("Profile clearing was cancelled."))
            return False
        try:
            count = self._service.clear_vehicle_profile(
                self._game_root, self._profile_name)
        except self._service.VehicleOverlayError as error:
            return self._show_error(error)
        message = (
            "Cleared edits from %d package member%s in profile '%s'." %
            (count, "" if count == 1 else "s", self._profile_name))
        self._log("Vehicle data editor: %s" % message)
        for record in self._fields:
            record["currentValue"] = record.get("originalValue", "0")
        self.armor_viewer.reset_values()
        if self.inspect():
            if self._language == i18n.LANGUAGE_CHINESE:
                self.status.set(
                    "已清除方案“%s”中 %d 个数据包成员的修改。" %
                    (self._profile_name, count))
            else:
                self.status.set(message)
        return True


def open_vehicle_editor(parent, game_root, profile_name, log=None,
                        language=i18n.LANGUAGE_AUTO):
    """Open a vehicle editor using the real Tk modules."""
    import tkinter
    from tkinter import messagebox, ttk

    return VehicleEditorWindow(
        parent, game_root, profile_name, tkinter, ttk, messagebox, log=log,
        language=language)
