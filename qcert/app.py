"""QCert — main GUI application (tkinter)."""

import copy
import io
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
from typing import Optional

from PIL import Image, ImageTk

from .data_import import DataStore
from .layout import (
    LayoutProfile, TextFieldDef, ImageLayerDef,
    new_text_field, new_image_layer,
    save_layout, load_layout, duplicate_layout, default_layout,
)
from .renderer import render_single, render_batch, _page_size
from .formatter import sanitize_for_pdf


# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

APP_TITLE = "QCert — Certificate Generator"
PREVIEW_MAX_W = 750
PREVIEW_MAX_H = 540
GRID_STEP_MM = 5.0
NUDGE_MM = 0.5
FINE_NUDGE_MM = 0.1
MM_PER_PT = 25.4 / 72


# ------------------------------------------------------------------
# Undo stack
# ------------------------------------------------------------------

class UndoStack:
    def __init__(self, limit: int = 50):
        self._stack: list = []
        self._redo: list = []
        self._limit = limit

    def push(self, state):
        self._stack.append(copy.deepcopy(state))
        if len(self._stack) > self._limit:
            self._stack.pop(0)
        self._redo.clear()

    def undo(self, current_state):
        if not self._stack:
            return current_state
        self._redo.append(copy.deepcopy(current_state))
        return self._stack.pop()

    def redo(self, current_state):
        if not self._redo:
            return current_state
        self._stack.append(copy.deepcopy(current_state))
        return self._redo.pop()

    @property
    def can_undo(self):
        return bool(self._stack)

    @property
    def can_redo(self):
        return bool(self._redo)


# ------------------------------------------------------------------
# Application
# ------------------------------------------------------------------

class QCertApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1360x820")
        self.root.minsize(1100, 700)

        # State
        self.data = DataStore()
        self.layout = default_layout()
        self.undo_stack = UndoStack()
        self._selected_element: Optional[str] = None  # element id
        self._preview_image: Optional[ImageTk.PhotoImage] = None
        self._test_mode = False
        self._snap_to_grid = False
        self._zoom = 1.0
        self._cursor_pos_mm = (0.0, 0.0)
        self._drag_start = None
        self._drag_elem_start = None

        self._build_menu()
        self._build_ui()
        self._bind_shortcuts()
        self._refresh_preview()

    # ==============================================================
    # Menu bar
    # ==============================================================
    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open Spreadsheet…", command=self._open_spreadsheet, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Load Layout…", command=self._load_layout)
        file_menu.add_command(label="Save Layout", command=self._save_layout, accelerator="Ctrl+S")
        file_menu.add_command(label="Save Layout As…", command=self._save_layout_as)
        file_menu.add_command(label="Duplicate Layout", command=self._duplicate_layout)
        file_menu.add_command(label="Reset to Default", command=self._reset_layout)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        template_menu = tk.Menu(menubar, tearoff=0)
        template_menu.add_command(label="Set Background PDF…", command=self._choose_background)
        template_menu.add_separator()
        template_menu.add_command(label="Page: A4 Landscape", command=lambda: self._set_page("A4", "landscape"))
        template_menu.add_command(label="Page: A4 Portrait", command=lambda: self._set_page("A4", "portrait"))
        template_menu.add_command(label="Page: Letter Landscape", command=lambda: self._set_page("Letter", "landscape"))
        template_menu.add_command(label="Page: Letter Portrait", command=lambda: self._set_page("Letter", "portrait"))
        menubar.add_cascade(label="Template", menu=template_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Toggle Test Mode", command=self._toggle_test_mode)
        view_menu.add_command(label="Toggle Snap to Grid", command=self._toggle_snap)
        view_menu.add_command(label="Zoom In", command=lambda: self._set_zoom(self._zoom + 0.1))
        view_menu.add_command(label="Zoom Out", command=lambda: self._set_zoom(self._zoom - 0.1))
        view_menu.add_command(label="Zoom Reset", command=lambda: self._set_zoom(1.0))
        menubar.add_cascade(label="View", menu=view_menu)

        export_menu = tk.Menu(menubar, tearoff=0)
        export_menu.add_command(label="Export Current Record…", command=self._export_current)
        export_menu.add_command(label="Export All Records…", command=self._export_all)
        export_menu.add_command(label="Export Selected…", command=self._export_selected)
        menubar.add_cascade(label="Export", menu=export_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Undo", command=self._undo, accelerator="Ctrl+Z")
        edit_menu.add_command(label="Redo", command=self._redo, accelerator="Ctrl+Y")
        menubar.add_cascade(label="Edit", menu=edit_menu)

        self.root.config(menu=menubar)

    # ==============================================================
    # UI layout — three main panels
    # ==============================================================
    def _build_ui(self):
        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # --- LEFT: Data panel ---
        left = ttk.Frame(main, width=310)
        main.add(left, weight=0)
        self._build_data_panel(left)

        # --- MIDDLE: Controls ---
        mid = ttk.Frame(main, width=310)
        main.add(mid, weight=0)
        self._build_controls_panel(mid)

        # --- RIGHT: Preview ---
        right = ttk.Frame(main)
        main.add(right, weight=1)
        self._build_preview_panel(right)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status.pack(side=tk.BOTTOM, fill=tk.X)

    # ------------------------------------------------------------------
    # LEFT panel: data import + record nav + data table
    # ------------------------------------------------------------------
    def _build_data_panel(self, parent):
        ttk.Label(parent, text="Data", font=("", 11, "bold")).pack(anchor=tk.W, padx=4, pady=(4, 0))

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, padx=4, pady=2)
        ttk.Button(btn_frame, text="Open Spreadsheet…", command=self._open_spreadsheet).pack(side=tk.LEFT)

        self.file_label = ttk.Label(parent, text="No file loaded", foreground="gray")
        self.file_label.pack(anchor=tk.W, padx=4)

        # Navigation
        nav = ttk.Frame(parent)
        nav.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(nav, text="<< Prev", width=8, command=self._prev_record).pack(side=tk.LEFT)
        self.rec_var = tk.StringVar(value="0 / 0")
        ttk.Label(nav, textvariable=self.rec_var, width=12, anchor=tk.CENTER).pack(side=tk.LEFT, padx=4)
        ttk.Button(nav, text="Next >>", width=8, command=self._next_record).pack(side=tk.LEFT)

        # Go-to / search
        sf = ttk.Frame(parent)
        sf.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(sf, text="Go to row:").pack(side=tk.LEFT)
        self.goto_var = tk.StringVar()
        ttk.Entry(sf, textvariable=self.goto_var, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Button(sf, text="Go", command=self._goto_record).pack(side=tk.LEFT)

        sf2 = ttk.Frame(parent)
        sf2.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(sf2, text="Search:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ttk.Entry(sf2, textvariable=self.search_var, width=14).pack(side=tk.LEFT, padx=2)
        ttk.Button(sf2, text="Find", command=self._search_records).pack(side=tk.LEFT)

        # Data table
        ttk.Label(parent, text="Current Record", font=("", 10, "bold")).pack(anchor=tk.W, padx=4, pady=(6, 0))
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.data_tree = ttk.Treeview(tree_frame, columns=("Column", "Value"), show="headings", height=12)
        self.data_tree.heading("Column", text="Column")
        self.data_tree.heading("Value", text="Value")
        self.data_tree.column("Column", width=100)
        self.data_tree.column("Value", width=160)
        sb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.data_tree.yview)
        self.data_tree.configure(yscrollcommand=sb.set)
        self.data_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ------------------------------------------------------------------
    # MIDDLE panel: field / image controls
    # ------------------------------------------------------------------
    def _build_controls_panel(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill=tk.BOTH, expand=True, padx=2)

        # --- Text Fields tab ---
        tf_tab = ttk.Frame(nb)
        nb.add(tf_tab, text="Text Fields")
        self._build_text_field_controls(tf_tab)

        # --- Images tab ---
        img_tab = ttk.Frame(nb)
        nb.add(img_tab, text="Images")
        self._build_image_controls(img_tab)

        # --- Output tab ---
        out_tab = ttk.Frame(nb)
        nb.add(out_tab, text="Output")
        self._build_output_controls(out_tab)

    def _build_text_field_controls(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(top, text="+ Add Text Field", command=self._add_text_field).pack(side=tk.LEFT)
        ttk.Button(top, text="- Remove", command=self._remove_text_field).pack(side=tk.LEFT, padx=4)

        # Listbox of fields
        self.fields_listbox = tk.Listbox(parent, height=6)
        self.fields_listbox.pack(fill=tk.X, padx=4, pady=2)
        self.fields_listbox.bind("<<ListboxSelect>>", self._on_field_select)

        # Property editor
        props = ttk.LabelFrame(parent, text="Field Properties")
        props.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._tf_vars: dict[str, tk.Variable] = {}
        row = 0
        for label, key, default, widget_type in [
            ("Source Column", "source_column", "", "combo"),
            ("Static Text", "static_text", "", "entry"),
            ("X (mm)", "x", "50", "entry"),
            ("Y (mm)", "y", "50", "entry"),
            ("Width (mm)", "width", "100", "entry"),
            ("Alignment", "alignment", "left", "align"),
            ("Font Family", "font_family", "Helvetica", "entry"),
            ("Font Size", "font_size", "12", "entry"),
            ("Weight", "font_weight", "normal", "weight"),
            ("Colour", "font_colour", "#000000", "colour"),
            ("Line Spacing", "line_spacing", "1.2", "entry"),
            ("Wrap", "wrap", True, "check"),
            ("Format Rule", "format_rule", "", "entry"),
            ("Show Bbox", "show_bbox", False, "check"),
        ]:
            ttk.Label(props, text=label).grid(row=row, column=0, sticky=tk.W, padx=2, pady=1)
            if widget_type == "entry":
                var = tk.StringVar(value=str(default))
                ttk.Entry(props, textvariable=var, width=14).grid(row=row, column=1, sticky=tk.EW, padx=2)
            elif widget_type == "combo":
                var = tk.StringVar(value=default)
                cb = ttk.Combobox(props, textvariable=var, width=12)
                cb.grid(row=row, column=1, sticky=tk.EW, padx=2)
                self._col_combo = cb
            elif widget_type == "align":
                var = tk.StringVar(value=default)
                ttk.Combobox(props, textvariable=var, values=["left", "center", "right"], width=12).grid(row=row, column=1, sticky=tk.EW, padx=2)
            elif widget_type == "weight":
                var = tk.StringVar(value=default)
                ttk.Combobox(props, textvariable=var, values=["normal", "bold"], width=12).grid(row=row, column=1, sticky=tk.EW, padx=2)
            elif widget_type == "colour":
                var = tk.StringVar(value=default)
                f = ttk.Frame(props)
                f.grid(row=row, column=1, sticky=tk.EW, padx=2)
                ttk.Entry(f, textvariable=var, width=9).pack(side=tk.LEFT)
                ttk.Button(f, text="…", width=2, command=lambda v=var: self._pick_colour(v)).pack(side=tk.LEFT)
            elif widget_type == "check":
                var = tk.BooleanVar(value=default)
                ttk.Checkbutton(props, variable=var).grid(row=row, column=1, sticky=tk.W, padx=2)
            self._tf_vars[key] = var
            row += 1

        props.columnconfigure(1, weight=1)
        ttk.Button(props, text="Apply", command=self._apply_text_field).grid(row=row, column=0, columnspan=2, pady=4)

    def _build_image_controls(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(top, text="+ Add Image", command=self._add_image_layer).pack(side=tk.LEFT)
        ttk.Button(top, text="- Remove", command=self._remove_image_layer).pack(side=tk.LEFT, padx=4)

        self.images_listbox = tk.Listbox(parent, height=5)
        self.images_listbox.pack(fill=tk.X, padx=4, pady=2)
        self.images_listbox.bind("<<ListboxSelect>>", self._on_image_select)

        props = ttk.LabelFrame(parent, text="Image Properties")
        props.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._img_vars: dict[str, tk.Variable] = {}
        row = 0
        for label, key, default, wtype in [
            ("File", "file_path", "", "file"),
            ("Source Column", "source_column", "", "entry"),
            ("X (mm)", "x", "10", "entry"),
            ("Y (mm)", "y", "10", "entry"),
            ("Width (mm)", "width", "30", "entry"),
            ("Height (mm)", "height", "30", "entry"),
            ("Lock Aspect", "lock_aspect", True, "check"),
            ("Rotation (deg)", "rotation", "0", "entry"),
            ("Opacity", "opacity", "1.0", "entry"),
        ]:
            ttk.Label(props, text=label).grid(row=row, column=0, sticky=tk.W, padx=2, pady=1)
            if wtype == "entry":
                var = tk.StringVar(value=str(default))
                ttk.Entry(props, textvariable=var, width=14).grid(row=row, column=1, sticky=tk.EW, padx=2)
            elif wtype == "file":
                var = tk.StringVar(value=default)
                f = ttk.Frame(props)
                f.grid(row=row, column=1, sticky=tk.EW, padx=2)
                ttk.Entry(f, textvariable=var, width=10).pack(side=tk.LEFT, fill=tk.X, expand=True)
                ttk.Button(f, text="…", width=2, command=lambda v=var: self._pick_image_file(v)).pack(side=tk.LEFT)
            elif wtype == "check":
                var = tk.BooleanVar(value=default)
                ttk.Checkbutton(props, variable=var).grid(row=row, column=1, sticky=tk.W, padx=2)
            self._img_vars[key] = var
            row += 1

        props.columnconfigure(1, weight=1)
        ttk.Button(props, text="Apply", command=self._apply_image_layer).grid(row=row, column=0, columnspan=2, pady=4)

    def _build_output_controls(self, parent):
        f = ttk.Frame(parent)
        f.pack(fill=tk.X, padx=8, pady=8)

        ttk.Label(f, text="Filename template:").pack(anchor=tk.W)
        self.fname_var = tk.StringVar(value=self.layout.output_name_template)
        ttk.Entry(f, textvariable=self.fname_var).pack(fill=tk.X, pady=2)
        ttk.Label(f, text="Use {ColumnName} placeholders", foreground="gray").pack(anchor=tk.W)

        ttk.Label(f, text="Output mode:").pack(anchor=tk.W, pady=(8, 0))
        self.outmode_var = tk.StringVar(value=self.layout.output_mode)
        ttk.Radiobutton(f, text="One PDF per record", variable=self.outmode_var, value="individual").pack(anchor=tk.W)
        ttk.Radiobutton(f, text="Combined multi-page PDF", variable=self.outmode_var, value="combined").pack(anchor=tk.W)

        ttk.Button(f, text="Apply Output Settings", command=self._apply_output_settings).pack(pady=8)

    # ------------------------------------------------------------------
    # RIGHT panel: certificate preview
    # ------------------------------------------------------------------
    def _build_preview_panel(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, padx=4, pady=2)

        ttk.Button(toolbar, text="Zoom +", command=lambda: self._set_zoom(self._zoom + 0.1)).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Zoom -", command=lambda: self._set_zoom(self._zoom - 0.1)).pack(side=tk.LEFT, padx=2)
        self.zoom_label = ttk.Label(toolbar, text="100%")
        self.zoom_label.pack(side=tk.LEFT, padx=4)

        self.test_btn = ttk.Button(toolbar, text="Test Mode: OFF", command=self._toggle_test_mode)
        self.test_btn.pack(side=tk.LEFT, padx=8)
        self.snap_btn = ttk.Button(toolbar, text="Snap: OFF", command=self._toggle_snap)
        self.snap_btn.pack(side=tk.LEFT)

        self.coord_label = ttk.Label(toolbar, text="X: -- Y: --", width=20)
        self.coord_label.pack(side=tk.RIGHT)

        # Canvas
        self.canvas = tk.Canvas(parent, bg="#d0d0d0", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_click)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)

    # ==============================================================
    # Keyboard shortcuts
    # ==============================================================
    def _bind_shortcuts(self):
        self.root.bind("<Control-o>", lambda e: self._open_spreadsheet())
        self.root.bind("<Control-s>", lambda e: self._save_layout())
        self.root.bind("<Control-z>", lambda e: self._undo())
        self.root.bind("<Control-y>", lambda e: self._redo())
        self.root.bind("<Control-Z>", lambda e: self._redo())  # Ctrl+Shift+Z
        self.root.bind("n", self._key_next)
        self.root.bind("p", self._key_prev)
        self.root.bind("<Left>", lambda e: self._nudge(-NUDGE_MM, 0))
        self.root.bind("<Right>", lambda e: self._nudge(NUDGE_MM, 0))
        self.root.bind("<Up>", lambda e: self._nudge(0, -NUDGE_MM))
        self.root.bind("<Down>", lambda e: self._nudge(0, NUDGE_MM))
        self.root.bind("<Shift-Left>", lambda e: self._nudge(-FINE_NUDGE_MM, 0))
        self.root.bind("<Shift-Right>", lambda e: self._nudge(FINE_NUDGE_MM, 0))
        self.root.bind("<Shift-Up>", lambda e: self._nudge(0, -FINE_NUDGE_MM))
        self.root.bind("<Shift-Down>", lambda e: self._nudge(0, FINE_NUDGE_MM))

    def _key_next(self, event):
        # Don't trigger when typing in an entry widget
        if isinstance(event.widget, (tk.Entry, ttk.Entry)):
            return
        self._next_record()

    def _key_prev(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Entry)):
            return
        self._prev_record()

    # ==============================================================
    # Data actions
    # ==============================================================
    def _open_spreadsheet(self):
        path = filedialog.askopenfilename(
            title="Select Spreadsheet",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.data.load(path)
        except Exception as exc:
            messagebox.showerror("Import Error", str(exc))
            return

        self.file_label.config(text=os.path.basename(path), foreground="black")
        # Update column combo
        if hasattr(self, "_col_combo"):
            self._col_combo["values"] = [""] + self.data.headers
        self._refresh_data_view()
        self._refresh_preview()
        self.status_var.set(f"Loaded {self.data.count} records from {os.path.basename(path)}")

    def _refresh_data_view(self):
        self.data_tree.delete(*self.data_tree.get_children())
        rec = self.data.current_record
        for hdr in self.data.headers:
            self.data_tree.insert("", tk.END, values=(hdr, rec.get(hdr, "")))
        self.rec_var.set(f"{self.data.current_index + 1} / {self.data.count}")

    def _next_record(self):
        self.data.next()
        self._refresh_data_view()
        self._refresh_preview()

    def _prev_record(self):
        self.data.prev()
        self._refresh_data_view()
        self._refresh_preview()

    def _goto_record(self):
        try:
            idx = int(self.goto_var.get()) - 1
            self.data.goto(idx)
            self._refresh_data_view()
            self._refresh_preview()
        except ValueError:
            messagebox.showwarning("Invalid", "Enter a valid row number.")

    def _search_records(self):
        q = self.search_var.get().strip()
        if not q:
            return
        hits = self.data.search(q)
        if not hits:
            messagebox.showinfo("Search", "No matching records found.")
            return
        self.data.goto(hits[0])
        self._refresh_data_view()
        self._refresh_preview()
        self.status_var.set(f"Found {len(hits)} matches — showing first")

    # ==============================================================
    # Layout / template actions
    # ==============================================================
    def _choose_background(self):
        path = filedialog.askopenfilename(
            title="Select Background PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            self.layout.template_pdf = path
            self._refresh_preview()
            self.status_var.set(f"Background: {os.path.basename(path)}")

    def _set_page(self, size: str, orient: str):
        self._push_undo()
        self.layout.page_size = size
        self.layout.orientation = orient
        self._refresh_preview()

    def _load_layout(self):
        path = filedialog.askopenfilename(
            title="Load Layout",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.layout = load_layout(path)
        except Exception as exc:
            messagebox.showerror("Load Error", str(exc))
            return
        self._refresh_field_list()
        self._refresh_image_list()
        self._sync_output_controls()
        self._refresh_preview()
        self.status_var.set(f"Layout loaded: {self.layout.name}")

    def _save_layout(self):
        if not hasattr(self, "_layout_path") or not self._layout_path:
            self._save_layout_as()
            return
        self._do_save(self._layout_path)

    def _save_layout_as(self):
        path = filedialog.asksaveasfilename(
            title="Save Layout As",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
        )
        if path:
            self._layout_path = path
            self._do_save(path)

    def _do_save(self, path):
        try:
            save_layout(self.layout, path)
            self.status_var.set(f"Layout saved: {path}")
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc))

    def _duplicate_layout(self):
        self.layout = duplicate_layout(self.layout)
        self._layout_path = None
        self.status_var.set(f"Duplicated as '{self.layout.name}'")

    def _reset_layout(self):
        if messagebox.askyesno("Reset", "Reset layout to defaults? Unsaved changes will be lost."):
            self.layout = default_layout()
            self._refresh_field_list()
            self._refresh_image_list()
            self._refresh_preview()

    # ==============================================================
    # Text field actions
    # ==============================================================
    def _add_text_field(self):
        self._push_undo()
        tf = new_text_field()
        self.layout.text_fields.append(tf)
        self._refresh_field_list()
        self.fields_listbox.selection_set(tk.END)
        self._on_field_select(None)
        self._refresh_preview()

    def _remove_text_field(self):
        sel = self.fields_listbox.curselection()
        if not sel:
            return
        self._push_undo()
        idx = sel[0]
        self.layout.text_fields.pop(idx)
        self._refresh_field_list()
        self._refresh_preview()

    def _refresh_field_list(self):
        self.fields_listbox.delete(0, tk.END)
        for tf in self.layout.text_fields:
            self.fields_listbox.insert(tk.END, tf.display_label())

    def _on_field_select(self, _event):
        sel = self.fields_listbox.curselection()
        if not sel:
            return
        tf = self.layout.text_fields[sel[0]]
        self._selected_element = tf.id
        self._tf_vars["source_column"].set(tf.source_column)
        self._tf_vars["static_text"].set(tf.static_text)
        self._tf_vars["x"].set(str(tf.x))
        self._tf_vars["y"].set(str(tf.y))
        self._tf_vars["width"].set(str(tf.width))
        self._tf_vars["alignment"].set(tf.alignment)
        self._tf_vars["font_family"].set(tf.font_family)
        self._tf_vars["font_size"].set(str(tf.font_size))
        self._tf_vars["font_weight"].set(tf.font_weight)
        self._tf_vars["font_colour"].set(tf.font_colour)
        self._tf_vars["line_spacing"].set(str(tf.line_spacing))
        self._tf_vars["wrap"].set(tf.wrap)
        self._tf_vars["format_rule"].set(tf.format_rule)
        self._tf_vars["show_bbox"].set(tf.show_bbox)

    def _apply_text_field(self):
        sel = self.fields_listbox.curselection()
        if not sel:
            messagebox.showinfo("Info", "Select a text field first.")
            return
        self._push_undo()
        tf = self.layout.text_fields[sel[0]]
        tf.source_column = self._tf_vars["source_column"].get()
        tf.static_text = self._tf_vars["static_text"].get()
        tf.x = float(self._tf_vars["x"].get() or 0)
        tf.y = float(self._tf_vars["y"].get() or 0)
        tf.width = float(self._tf_vars["width"].get() or 100)
        tf.alignment = self._tf_vars["alignment"].get()
        tf.font_family = self._tf_vars["font_family"].get()
        tf.font_size = float(self._tf_vars["font_size"].get() or 12)
        tf.font_weight = self._tf_vars["font_weight"].get()
        tf.font_colour = self._tf_vars["font_colour"].get()
        tf.line_spacing = float(self._tf_vars["line_spacing"].get() or 1.2)
        tf.wrap = self._tf_vars["wrap"].get()
        tf.format_rule = self._tf_vars["format_rule"].get()
        tf.show_bbox = self._tf_vars["show_bbox"].get()
        self._refresh_field_list()
        self.fields_listbox.selection_set(sel[0])
        self._refresh_preview()

    # ==============================================================
    # Image layer actions
    # ==============================================================
    def _add_image_layer(self):
        self._push_undo()
        il = new_image_layer()
        self.layout.image_layers.append(il)
        self._refresh_image_list()
        self.images_listbox.selection_set(tk.END)
        self._on_image_select(None)
        self._refresh_preview()

    def _remove_image_layer(self):
        sel = self.images_listbox.curselection()
        if not sel:
            return
        self._push_undo()
        self.layout.image_layers.pop(sel[0])
        self._refresh_image_list()
        self._refresh_preview()

    def _refresh_image_list(self):
        self.images_listbox.delete(0, tk.END)
        for il in self.layout.image_layers:
            label = os.path.basename(il.file_path) if il.file_path else "(no file)"
            if il.source_column:
                label = f"[{il.source_column}]"
            self.images_listbox.insert(tk.END, label)

    def _on_image_select(self, _event):
        sel = self.images_listbox.curselection()
        if not sel:
            return
        il = self.layout.image_layers[sel[0]]
        self._selected_element = il.id
        self._img_vars["file_path"].set(il.file_path)
        self._img_vars["source_column"].set(il.source_column)
        self._img_vars["x"].set(str(il.x))
        self._img_vars["y"].set(str(il.y))
        self._img_vars["width"].set(str(il.width))
        self._img_vars["height"].set(str(il.height))
        self._img_vars["lock_aspect"].set(il.lock_aspect)
        self._img_vars["rotation"].set(str(il.rotation))
        self._img_vars["opacity"].set(str(il.opacity))

    def _apply_image_layer(self):
        sel = self.images_listbox.curselection()
        if not sel:
            messagebox.showinfo("Info", "Select an image layer first.")
            return
        self._push_undo()
        il = self.layout.image_layers[sel[0]]
        il.file_path = self._img_vars["file_path"].get()
        il.source_column = self._img_vars["source_column"].get()
        il.x = float(self._img_vars["x"].get() or 0)
        il.y = float(self._img_vars["y"].get() or 0)
        il.width = float(self._img_vars["width"].get() or 30)
        il.height = float(self._img_vars["height"].get() or 30)
        il.lock_aspect = self._img_vars["lock_aspect"].get()
        il.rotation = float(self._img_vars["rotation"].get() or 0)
        il.opacity = float(self._img_vars["opacity"].get() or 1.0)
        self._refresh_image_list()
        self.images_listbox.selection_set(sel[0])
        self._refresh_preview()

    def _pick_image_file(self, var):
        path = filedialog.askopenfilename(
            title="Select Image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All files", "*.*")],
        )
        if path:
            var.set(path)

    def _pick_colour(self, var):
        colour = colorchooser.askcolor(initialcolor=var.get())
        if colour and colour[1]:
            var.set(colour[1])

    # ==============================================================
    # Output settings
    # ==============================================================
    def _sync_output_controls(self):
        self.fname_var.set(self.layout.output_name_template)
        self.outmode_var.set(self.layout.output_mode)

    def _apply_output_settings(self):
        self.layout.output_name_template = self.fname_var.get()
        self.layout.output_mode = self.outmode_var.get()
        self.status_var.set("Output settings applied")

    # ==============================================================
    # Preview rendering
    # ==============================================================
    def _refresh_preview(self):
        """Render the current record and display it on the canvas."""
        record = self.data.current_record if self.data.rows else {}

        # Force bounding boxes on in test mode
        if self._test_mode:
            for tf in self.layout.text_fields:
                tf.show_bbox = True

        warnings: list[str] = []
        try:
            pdf_bytes = render_single(self.layout, record, warn=lambda m: warnings.append(m))
        except Exception as exc:
            self.status_var.set(f"Preview error: {exc}")
            return

        # Restore show_bbox if test mode forced it
        if self._test_mode:
            for tf, var_val in zip(self.layout.text_fields,
                                    [tf.show_bbox for tf in self.layout.text_fields]):
                pass  # boxes already drawn in the PDF

        # Convert PDF bytes -> PIL Image -> Tk PhotoImage
        try:
            from PIL import Image
            # Use PyPDF2 to get page dimensions, then render with reportlab-produced content
            # For preview, we convert PDF -> image via a simple approach
            img = self._pdf_bytes_to_image(pdf_bytes)
            if img is None:
                self.status_var.set("Preview: install poppler for full preview, showing placeholder")
                self._draw_placeholder_preview()
                return

            # Scale to fit canvas
            cw = max(self.canvas.winfo_width(), PREVIEW_MAX_W)
            ch = max(self.canvas.winfo_height(), PREVIEW_MAX_H)
            iw, ih = img.size
            scale = min(cw / iw, ch / ih) * self._zoom
            new_w = max(1, int(iw * scale))
            new_h = max(1, int(ih * scale))
            img = img.resize((new_w, new_h), Image.LANCZOS)

            self._preview_pil = img
            self._preview_image = ImageTk.PhotoImage(img)
            self._preview_scale = scale
            self._preview_page_w = iw
            self._preview_page_h = ih

            self.canvas.delete("all")
            self.canvas.create_image(cw // 2, ch // 2, image=self._preview_image, anchor=tk.CENTER)
            self._preview_offset_x = cw // 2 - new_w // 2
            self._preview_offset_y = ch // 2 - new_h // 2

            # Draw grid overlay in test mode
            if self._test_mode:
                self._draw_grid_overlay(new_w, new_h)

        except Exception as exc:
            self.status_var.set(f"Preview render: {exc}")
            self._draw_placeholder_preview()

        if warnings:
            self.status_var.set(f"Warnings: {'; '.join(warnings[:3])}")

    def _pdf_bytes_to_image(self, pdf_bytes: bytes) -> Optional[Image.Image]:
        """Convert PDF bytes to a PIL Image. Tries multiple backends."""
        # Try pdf2image (poppler) first
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(pdf_bytes, dpi=150, first_page=1, last_page=1)
            if images:
                return images[0]
        except ImportError:
            pass
        except Exception:
            pass

        # Try fitz (PyMuPDF)
        try:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = doc[0]
            mat = fitz.Matrix(2.0, 2.0)  # 2x zoom = ~144 dpi
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            doc.close()
            return img
        except ImportError:
            pass
        except Exception:
            pass

        # Fallback: render a simple placeholder from layout dimensions
        return None

    def _draw_placeholder_preview(self):
        """Draw a basic rectangle representing the page with element positions marked."""
        self.canvas.delete("all")
        pw, ph = self.layout.page_dimensions_mm()
        cw = max(self.canvas.winfo_width(), PREVIEW_MAX_W)
        ch = max(self.canvas.winfo_height(), PREVIEW_MAX_H)

        scale = min((cw - 40) / pw, (ch - 40) / ph) * self._zoom
        rw = pw * scale
        rh = ph * scale
        ox = (cw - rw) / 2
        oy = (ch - rh) / 2

        self._preview_scale = scale
        self._preview_offset_x = ox
        self._preview_offset_y = oy

        # Page rectangle
        self.canvas.create_rectangle(ox, oy, ox + rw, oy + rh, fill="white", outline="#888")

        # Background label
        if self.layout.template_pdf:
            self.canvas.create_text(ox + rw / 2, oy + 14, text=f"BG: {os.path.basename(self.layout.template_pdf)}",
                                     fill="#999", font=("", 8))

        record = self.data.current_record if self.data.rows else {}

        # Draw text field markers
        for tf in self.layout.text_fields:
            fx = ox + tf.x * scale
            fy = oy + tf.y * scale
            fw = tf.width * scale
            fh = tf.font_size * tf.line_spacing * scale / 2.5  # approximate

            raw = record.get(tf.source_column, tf.static_text) if tf.source_column else tf.static_text
            if not raw:
                raw = tf.display_label()

            self.canvas.create_rectangle(fx, fy, fx + fw, fy + fh,
                                          outline="#4a90d9", dash=(3, 3))
            # Truncate display text
            display = raw[:40]
            self.canvas.create_text(fx + 2, fy + 2, text=display, anchor=tk.NW,
                                     fill=tf.font_colour, font=("", max(7, int(tf.font_size * scale / 4))))

        # Draw image layer markers
        for il in self.layout.image_layers:
            ix = ox + il.x * scale
            iy = oy + il.y * scale
            iw = il.width * scale
            ih = il.height * scale
            self.canvas.create_rectangle(ix, iy, ix + iw, iy + ih,
                                          outline="#e67e22", dash=(2, 2))
            label = os.path.basename(il.file_path) if il.file_path else "[img]"
            self.canvas.create_text(ix + iw / 2, iy + ih / 2, text=label,
                                     fill="#e67e22", font=("", 7))

        # Grid in test mode
        if self._test_mode:
            self._draw_grid_overlay(rw, rh, ox, oy)

    def _draw_grid_overlay(self, w, h, ox=None, oy=None):
        if ox is None:
            ox = self._preview_offset_x
        if oy is None:
            oy = self._preview_offset_y
        scale = self._preview_scale
        step = GRID_STEP_MM * scale
        x = ox
        while x <= ox + w:
            self.canvas.create_line(x, oy, x, oy + h, fill="#ddd", dash=(1, 3))
            x += step
        y = oy
        while y <= oy + h:
            self.canvas.create_line(ox, y, ox + w, y, fill="#ddd", dash=(1, 3))
            y += step

    # ==============================================================
    # Canvas interaction (drag-drop, coordinate display)
    # ==============================================================
    def _canvas_to_mm(self, cx, cy) -> tuple[float, float]:
        """Convert canvas pixel coords to mm on the page."""
        ox = getattr(self, "_preview_offset_x", 0)
        oy = getattr(self, "_preview_offset_y", 0)
        scale = getattr(self, "_preview_scale", 1)
        if scale == 0:
            scale = 1
        mx = (cx - ox) / scale
        my = (cy - oy) / scale
        return (mx, my)

    def _on_canvas_motion(self, event):
        mx, my = self._canvas_to_mm(event.x, event.y)
        self._cursor_pos_mm = (mx, my)
        self.coord_label.config(text=f"X: {mx:.1f} mm  Y: {my:.1f} mm")

    def _on_canvas_click(self, event):
        mx, my = self._canvas_to_mm(event.x, event.y)
        # Find element under cursor
        elem = self._hit_test(mx, my)
        if elem:
            self._selected_element = elem
            self._drag_start = (event.x, event.y)
            # Store element start position
            obj = self._find_element(elem)
            if obj:
                self._drag_elem_start = (obj.x, obj.y)

    def _on_canvas_drag(self, event):
        if not self._drag_start or not self._drag_elem_start:
            return
        scale = getattr(self, "_preview_scale", 1) or 1
        dx = (event.x - self._drag_start[0]) / scale
        dy = (event.y - self._drag_start[1]) / scale
        obj = self._find_element(self._selected_element)
        if obj:
            new_x = self._drag_elem_start[0] + dx
            new_y = self._drag_elem_start[1] + dy
            if self._snap_to_grid:
                new_x = round(new_x / GRID_STEP_MM) * GRID_STEP_MM
                new_y = round(new_y / GRID_STEP_MM) * GRID_STEP_MM
            obj.x = new_x
            obj.y = new_y
            self._refresh_preview()

    def _on_canvas_release(self, event):
        if self._drag_start and self._drag_elem_start:
            self._push_undo()
            # Update property panel if text field is selected
            self._sync_selected_to_panel()
        self._drag_start = None
        self._drag_elem_start = None

    def _hit_test(self, mx, my) -> Optional[str]:
        """Return element id at (mx, my) mm, or None."""
        # Check text fields (reverse order = topmost first)
        for tf in reversed(self.layout.text_fields):
            fh = tf.font_size * tf.line_spacing * MM_PER_PT * 2  # rough height
            if tf.x <= mx <= tf.x + tf.width and tf.y <= my <= tf.y + fh:
                return tf.id
        for il in reversed(self.layout.image_layers):
            if il.x <= mx <= il.x + il.width and il.y <= my <= il.y + il.height:
                return il.id
        return None

    def _find_element(self, elem_id):
        if not elem_id:
            return None
        for tf in self.layout.text_fields:
            if tf.id == elem_id:
                return tf
        for il in self.layout.image_layers:
            if il.id == elem_id:
                return il
        return None

    def _sync_selected_to_panel(self):
        obj = self._find_element(self._selected_element)
        if isinstance(obj, TextFieldDef):
            self._tf_vars["x"].set(f"{obj.x:.1f}")
            self._tf_vars["y"].set(f"{obj.y:.1f}")
        elif isinstance(obj, ImageLayerDef):
            self._img_vars["x"].set(f"{obj.x:.1f}")
            self._img_vars["y"].set(f"{obj.y:.1f}")

    # ==============================================================
    # Nudge, zoom, test mode
    # ==============================================================
    def _nudge(self, dx_mm, dy_mm):
        obj = self._find_element(self._selected_element)
        if not obj:
            return
        self._push_undo()
        obj.x += dx_mm
        obj.y += dy_mm
        if self._snap_to_grid:
            obj.x = round(obj.x / GRID_STEP_MM) * GRID_STEP_MM
            obj.y = round(obj.y / GRID_STEP_MM) * GRID_STEP_MM
        self._sync_selected_to_panel()
        self._refresh_preview()

    def _set_zoom(self, level):
        self._zoom = max(0.3, min(3.0, level))
        self.zoom_label.config(text=f"{int(self._zoom * 100)}%")
        self._refresh_preview()

    def _toggle_test_mode(self):
        self._test_mode = not self._test_mode
        self.test_btn.config(text=f"Test Mode: {'ON' if self._test_mode else 'OFF'}")
        self._refresh_preview()

    def _toggle_snap(self):
        self._snap_to_grid = not self._snap_to_grid
        self.snap_btn.config(text=f"Snap: {'ON' if self._snap_to_grid else 'OFF'}")

    # ==============================================================
    # Undo / redo
    # ==============================================================
    def _push_undo(self):
        self.undo_stack.push(self.layout)

    def _undo(self):
        self.layout = self.undo_stack.undo(self.layout)
        self._refresh_field_list()
        self._refresh_image_list()
        self._refresh_preview()
        self.status_var.set("Undo")

    def _redo(self):
        self.layout = self.undo_stack.redo(self.layout)
        self._refresh_field_list()
        self._refresh_image_list()
        self._refresh_preview()
        self.status_var.set("Redo")

    # ==============================================================
    # Export / batch generation
    # ==============================================================
    def _export_current(self):
        if not self.data.rows:
            messagebox.showinfo("Export", "No data loaded.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Current Certificate",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return
        warnings: list[str] = []
        try:
            pdf_bytes = render_single(self.layout, self.data.current_record,
                                       warn=lambda m: warnings.append(m))
            with open(path, "wb") as f:
                f.write(pdf_bytes)
            self.status_var.set(f"Exported: {path}")
            if warnings:
                messagebox.showwarning("Warnings", "\n".join(warnings))
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc))

    def _export_all(self):
        self._run_batch(indices=None)

    def _export_selected(self):
        if not self.data.rows:
            messagebox.showinfo("Export", "No data loaded.")
            return
        dialog = _RangeDialog(self.root, self.data.count)
        if dialog.result is not None:
            self._run_batch(indices=dialog.result)

    def _run_batch(self, indices):
        if not self.data.rows:
            messagebox.showinfo("Export", "No data loaded.")
            return
        out_dir = filedialog.askdirectory(title="Select Output Folder")
        if not out_dir:
            return

        # Progress window
        prog_win = tk.Toplevel(self.root)
        prog_win.title("Exporting…")
        prog_win.geometry("350x120")
        prog_win.transient(self.root)
        prog_label = ttk.Label(prog_win, text="Starting…")
        prog_label.pack(padx=10, pady=10)
        prog_bar = ttk.Progressbar(prog_win, length=300, mode="determinate")
        prog_bar.pack(padx=10, pady=5)

        def progress_cb(done, total):
            pct = int(done / total * 100) if total else 100
            prog_bar["value"] = pct
            prog_label.config(text=f"Generating {done} / {total}…")
            prog_win.update_idletasks()

        def run():
            warnings: list[str] = []
            summary = render_batch(
                self.layout, self.data.rows, out_dir,
                indices=indices,
                progress=progress_cb,
                warn=lambda m: warnings.append(m),
            )
            prog_win.destroy()
            msg = (
                f"Generated: {summary['generated']}\n"
                f"Skipped: {summary['skipped']}\n"
                f"Errors: {len(summary['errors'])}"
            )
            if summary["errors"]:
                msg += "\n\nErrors:\n" + "\n".join(summary["errors"][:10])
            if warnings:
                msg += f"\n\nWarnings: {len(warnings)}"
            messagebox.showinfo("Batch Export Complete", msg)
            self.status_var.set(f"Exported {summary['generated']} certificates to {out_dir}")

        # Run in a thread to keep UI responsive
        threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------
# Row-range selection dialog
# ------------------------------------------------------------------

class _RangeDialog(tk.Toplevel):
    def __init__(self, parent, total):
        super().__init__(parent)
        self.title("Select Rows")
        self.geometry("300x150")
        self.transient(parent)
        self.grab_set()
        self.result = None

        ttk.Label(self, text=f"Total rows: {total}").pack(padx=10, pady=5)
        ttk.Label(self, text="Enter row numbers (comma-separated)\nor ranges like 1-10:").pack(padx=10)
        self.entry_var = tk.StringVar(value=f"1-{total}")
        ttk.Entry(self, textvariable=self.entry_var, width=30).pack(padx=10, pady=5)

        bf = ttk.Frame(self)
        bf.pack(pady=5)
        ttk.Button(bf, text="OK", command=self._ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=4)

        self.wait_window()

    def _ok(self):
        text = self.entry_var.get().strip()
        indices = []
        for part in text.split(","):
            part = part.strip()
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    indices.extend(range(int(a) - 1, int(b)))
                except ValueError:
                    pass
            else:
                try:
                    indices.append(int(part) - 1)
                except ValueError:
                    pass
        self.result = indices
        self.destroy()


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main():
    root = tk.Tk()
    QCertApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
