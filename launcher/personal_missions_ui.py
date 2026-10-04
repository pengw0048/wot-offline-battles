"""Regular campaign completion editor, separate from daily missions."""

try:
    from . import save_personal_missions as progress_store
    from . import save_ledger, save_slots
except ImportError:
    import save_personal_missions as progress_store
    import save_ledger
    import save_slots


class PersonalMissionsDialog(object):
    def __init__(self, owner):
        self.owner = owner
        self.slot_id = owner._save_slot_id
        self.game_root = owner.game_root.get().strip() or None
        self.progress = progress_store.read_progress(self.slot_id, self.game_root)
        tk, ttk, tr = owner._tk, owner._ttk, owner._t
        self.window = tk.Toplevel(owner.save_dialog)
        self.window.title(tr("Personal missions"))
        self.window.transient(owner.save_dialog)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.operations = tuple(tr(value) for value in progress_store.OPERATIONS)
        self.chains = tuple(tr(value) for value in progress_store.CHAINS)
        self.operation = tk.StringVar(value=self.operations[0])
        self.chain = tk.StringVar(value=self.chains[0])
        selectors = tk.Frame(self.window, padx=12, pady=10)
        selectors.pack(fill="x")
        for row, (label, variable, values) in enumerate((
                ("Operation", self.operation, self.operations),
                ("Vehicle type", self.chain, self.chains))):
            tk.Label(selectors, text=tr(label)).grid(row=row, column=0, sticky="w")
            box = ttk.Combobox(selectors, textvariable=variable, values=values,
                               state="readonly", width=36)
            box.grid(row=row, column=1, sticky="we", padx=(12, 0), pady=3)
            box.bind("<<ComboboxSelected>>", self.refresh)
        selectors.grid_columnconfigure(1, weight=1)
        table = tk.Frame(self.window, padx=12)
        table.pack(fill="x")
        for col, label in enumerate(("Mission", "Completed", "Completed with honors")):
            tk.Label(table, text=tr(label)).grid(row=0, column=col, padx=12, pady=4)
        self.rows = []
        for index in range(15):
            label = tk.Label(table, text="", anchor="w")
            label.grid(row=index + 1, column=0, sticky="w", padx=12)
            completed = tk.BooleanVar(value=False)
            honors = tk.BooleanVar(value=False)
            for col, (variable, field) in enumerate(((completed, "completed"),
                                                    (honors, "honors")), 1):
                tk.Checkbutton(table, variable=variable,
                    command=lambda i=index, f=field: self.changed(i, f)).grid(
                        row=index + 1, column=col, pady=1)
            self.rows.append((label, completed, honors))
        actions = tk.Frame(self.window, padx=12, pady=8)
        actions.pack(fill="x")
        for text, value in (("Complete this chain", 1),
                            ("Honor this chain", 2), ("Reset this chain", 0)):
            tk.Button(actions, text=tr(text), command=lambda v=value: self.set_chain(v)).pack(
                side="left", fill="x", expand=True)
        tk.Label(self.window, text=tr(
            "Completing a later mission fills required earlier tasks without honors. "
            "Clearing completion resets this chain's final and every later operation. "
            "Clearing honors affects only this task. Corresponding rewards, including "
            "tanks, are withdrawn. Changes apply on next game launch. "
            "Close the game before saving."),
            wraplength=520, justify="left").pack(
                fill="x", padx=12, pady=4)
        self.feedback = tk.Label(self.window, text="", wraplength=520, justify="left")
        self.feedback.pack(fill="x", padx=12)
        buttons = tk.Frame(self.window, padx=12, pady=10)
        buttons.pack(fill="x")
        tk.Button(buttons, text=tr("Save"), command=self.save).pack(side="left", expand=True, fill="x")
        tk.Button(buttons, text=tr("Close"), command=self.close).pack(side="right", expand=True, fill="x")
        self.refresh()
        status = progress_store.read_edit_status(self.slot_id, self.game_root)
        if status["error"]:
            error_key, separator, detail = status["error"].partition(":")
            error_text = tr(error_key) + (": " + detail.strip() if separator else "")
            self.feedback.config(text=tr("Mission edit was not applied: %s") % error_text)
        elif status["pending"]:
            self.feedback.config(text=tr("Mission edits will be applied on the next game launch."))
        self.window.grab_set()

    def ids(self):
        return progress_store.mission_ids(self.operations.index(self.operation.get()),
                                         self.chains.index(self.chain.get()))

    def refresh(self, unused_event=None):
        for index, (qid, (label, completed, honors)) in enumerate(zip(self.ids(), self.rows), 1):
            label.config(text="%s-%d" % (self.chain.get(), index))
            value = self.progress.get(str(qid), 0)
            completed.set(value > 0)
            honors.set(value == 2)

    def changed(self, index, field):
        unused, completed, honors = self.rows[index]
        if field == "honors" and honors.get():
            completed.set(True)
        if field == "completed" and not completed.get():
            honors.set(False)
        value = 2 if honors.get() else (1 if completed.get() else 0)
        self.progress = progress_store.edit_progress(
            self.progress, [self.ids()[index]], value)
        self.refresh()

    def set_chain(self, value):
        self.progress = progress_store.edit_progress(self.progress, self.ids(), value)
        self.refresh()

    def save(self):
        if self.owner._busy or self.owner._maintenance_busy:
            self.feedback.config(text=self.owner._t("Wait for the current launcher operation to finish."))
            return False
        try:
            self.progress = progress_store.write_progress(
                self.slot_id, self.progress, self.game_root)
        except (save_ledger.SaveLedgerError, save_slots.SaveSlotError) as error:
            self.feedback.config(text=self.owner._t(str(error)))
            return False
        self.refresh()
        self.feedback.config(text=self.owner._t("Mission edits will be applied on the next game launch."))
        return True

    def close(self):
        self.window.grab_release()
        self.window.destroy()
        self.owner.save_dialog.grab_set()


class BadgesDialog(object):
    def __init__(self, owner):
        self.owner = owner
        self.slot_id = owner._save_slot_id
        self.game_root = owner.game_root.get().strip() or None
        self.catalogue = progress_store.badge_catalogue(self.game_root)
        fields = progress_store.read_account_fields(self.slot_id, self.game_root)
        self.owned = set(int(key) for key in fields["badges"])
        tk, ttk, tr = owner._tk, owner._ttk, owner._t
        self.window = tk.Toplevel(owner.save_dialog)
        self.window.title(tr("Account badges"))
        self.window.transient(owner.save_dialog)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.labels = tuple(row["label"] for row in self.catalogue)
        self.selection = tk.StringVar(value=self.labels[0] if self.labels else "")
        box = ttk.Combobox(self.window, values=self.labels, textvariable=self.selection,
                          state="readonly", width=48)
        box.pack(fill="x", padx=12, pady=12)
        box.bind("<<ComboboxSelected>>", self.refresh)
        self.achieved = tk.BooleanVar(value=False)
        tk.Checkbutton(self.window, text=tr("Acquired"), variable=self.achieved,
                       command=self.changed).pack(anchor="w", padx=12)
        self.feedback = tk.Label(self.window, text="", wraplength=440, justify="left")
        self.feedback.pack(fill="x", padx=12, pady=8)
        buttons = tk.Frame(self.window, padx=12, pady=10)
        buttons.pack(fill="x")
        tk.Button(buttons, text=tr("Save"), command=self.save).pack(side="left", fill="x", expand=True)
        tk.Button(buttons, text=tr("Close"), command=self.close).pack(side="right", fill="x", expand=True)
        self.refresh()
        self.window.grab_set()

    def selected_id(self):
        return self.catalogue[self.labels.index(self.selection.get())]["id"]

    def refresh(self, unused_event=None):
        if self.labels:
            self.achieved.set(self.selected_id() in self.owned)

    def changed(self):
        if self.achieved.get():
            self.owned.add(self.selected_id())
        else:
            self.owned.discard(self.selected_id())

    def save(self):
        if self.owner._busy or self.owner._maintenance_busy:
            self.feedback.config(text=self.owner._t("Wait for the current launcher operation to finish."))
            return False
        try:
            progress_store.write_account_fields(self.slot_id, self.game_root,
                                                badges=sorted(self.owned))
        except (save_ledger.SaveLedgerError, save_slots.SaveSlotError,
                progress_store.vehicle_overlays.VehicleOverlayError) as error:
            self.feedback.config(text=self.owner._t(str(error)))
            return False
        self.feedback.config(text=self.owner._t("Account badges saved."))
        return True

    def close(self):
        self.window.grab_release()
        self.window.destroy()
        self.owner.save_dialog.grab_set()
