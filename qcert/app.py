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

try:
    from PyPDF2 import PdfReader
except ImportError:
    from PyPDF2 import PdfFileReader as PdfReader

from .data_import import DataStore
from .layout import (
    LayoutProfile, TextFieldDef, ImageLayerDef,
    new_text_field, new_image_layer,
    save_layout, load_layout, duplicate_layout, default_layout,
    join_combined_values,
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
RESIZE_HANDLE_MM = 3.0      # corner handle hit-zone in mm
MIN_ELEMENT_SIZE_MM = 5.0    # minimum width/height when resizing


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
        self._center_grid = False
        self._zoom = 1.0
        self._cursor_pos_mm = (0.0, 0.0)
        self._drag_start = None
        self._drag_elem_start = None
        self._resize_mode = False
        self._resize_edge = None       # 'tl', 'tr', 'bl', 'br'
        self._resize_start_size = None # (width, height) at drag start
        self._refreshing = False  # guard against _on_field_select during list rebuilds

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

        # --- LEFT: Data + Tools panel ---
        left = ttk.Frame(main, width=310)
        main.add(left, weight=0)
        left_pane = ttk.PanedWindow(left, orient=tk.VERTICAL)
        left_pane.pack(fill=tk.BOTH, expand=True)
        data_frame = tk.Frame(left_pane, bg="#c8e6c9")  # pastel green
        left_pane.add(data_frame, weight=1)
        self._build_data_panel(data_frame)
        tools_frame = tk.Frame(left_pane, bg="#fff8e1")  # light yellow-cream
        left_pane.add(tools_frame, weight=1)
        self._build_tools_panel(tools_frame)

        # --- MIDDLE: Controls ---
        mid = tk.Frame(main, bg="#bbdefb")  # pastel light blue
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
        bg = parent.cget("bg")
        tk.Label(parent, text="Data", font=("", 13, "bold"), bg=bg).pack(anchor=tk.W, padx=4, pady=(4, 0))

        btn_frame = tk.Frame(parent, bg=bg)
        btn_frame.pack(fill=tk.X, padx=4, pady=2)
        ttk.Button(btn_frame, text="Open Spreadsheet…", command=self._open_spreadsheet).pack(side=tk.LEFT)

        self.file_label = tk.Label(parent, text="No file loaded", fg="gray", bg=bg)
        self.file_label.pack(anchor=tk.W, padx=4)

        # Navigation
        nav = tk.Frame(parent, bg=bg)
        nav.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(nav, text="<< Prev", width=8, command=self._prev_record).pack(side=tk.LEFT)
        self.rec_var = tk.StringVar(value="0 / 0")
        tk.Label(nav, textvariable=self.rec_var, width=12, anchor=tk.CENTER, bg=bg).pack(side=tk.LEFT, padx=4)
        ttk.Button(nav, text="Next >>", width=8, command=self._next_record).pack(side=tk.LEFT)

        # Go-to / search
        sf = tk.Frame(parent, bg=bg)
        sf.pack(fill=tk.X, padx=4, pady=2)
        tk.Label(sf, text="Go to row:", bg=bg).pack(side=tk.LEFT)
        self.goto_var = tk.StringVar()
        ttk.Entry(sf, textvariable=self.goto_var, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Button(sf, text="Go", command=self._goto_record).pack(side=tk.LEFT)

        sf2 = tk.Frame(parent, bg=bg)
        sf2.pack(fill=tk.X, padx=4, pady=2)
        tk.Label(sf2, text="Search:", bg=bg).pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ttk.Entry(sf2, textvariable=self.search_var, width=14).pack(side=tk.LEFT, padx=2)
        ttk.Button(sf2, text="Find", command=self._search_records).pack(side=tk.LEFT)

        # Data table
        tk.Label(parent, text="Current Record", font=("", 10, "bold"), bg=bg).pack(anchor=tk.W, padx=4, pady=(6, 0))
        tree_frame = tk.Frame(parent, bg=bg)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.data_tree = ttk.Treeview(tree_frame, columns=("Column", "Value"), show="headings", height=4)
        self.data_tree.heading("Column", text="Column")
        self.data_tree.heading("Value", text="Value")
        self.data_tree.column("Column", width=100)
        self.data_tree.column("Value", width=160)
        sb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.data_tree.yview)
        self.data_tree.configure(yscrollcommand=sb.set)
        self.data_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_tools_panel(self, parent):
        bg = parent.cget("bg")
        tk.Label(parent, text="Tools", font=("", 13, "bold"), bg=bg).pack(anchor=tk.W, padx=4, pady=(4, 0))

        # Center Grid toggle
        self._center_grid_var = tk.BooleanVar(value=False)
        grid_btn = tk.Checkbutton(parent, text="Center Grid", variable=self._center_grid_var,
                                   bg=bg, activebackground=bg, anchor=tk.W,
                                   command=self._toggle_center_grid)
        grid_btn.pack(fill=tk.X, padx=8, pady=2)

        # Center Text Field button
        ttk.Button(parent, text="Center Selected Field", command=self._center_selected_field).pack(
            fill=tk.X, padx=8, pady=2)

        # Insert Date button
        ttk.Button(parent, text="Insert Date", command=self._insert_date_field).pack(
            fill=tk.X, padx=8, pady=(2, 8))

    # ------------------------------------------------------------------
    # MIDDLE panel: field / image controls
    # ------------------------------------------------------------------
    def _build_controls_panel(self, parent):
        bg = parent.cget("bg") if isinstance(parent, tk.Frame) else None
        if bg:
            tk.Label(parent, text="Fields & Layers", font=("", 13, "bold"),
                     bg=bg).pack(anchor=tk.W, padx=4, pady=(4, 0))
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
        # Scrollable container for the entire text field controls
        canvas_frame = ttk.Frame(parent)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        scroll_canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=scroll_canvas.yview)
        scroll_inner = ttk.Frame(scroll_canvas)

        scroll_inner.bind("<Configure>", lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
        scroll_canvas.create_window((0, 0), window=scroll_inner, anchor="nw")
        scroll_canvas.configure(yscrollcommand=scrollbar.set)

        scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mousewheel scrolling
        def _on_mousewheel(event):
            scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        def _on_mousewheel_linux(event):
            if event.num == 4:
                scroll_canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                scroll_canvas.yview_scroll(1, "units")
        scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel)
        scroll_canvas.bind_all("<Button-4>", _on_mousewheel_linux)
        scroll_canvas.bind_all("<Button-5>", _on_mousewheel_linux)

        top = ttk.Frame(scroll_inner)
        top.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(top, text="+ Add Text Field", command=self._add_text_field).pack(side=tk.LEFT)
        ttk.Button(top, text="- Remove", command=self._remove_text_field).pack(side=tk.LEFT, padx=4)

        # Listbox of fields
        self.fields_listbox = tk.Listbox(scroll_inner, height=6, exportselection=False)
        self.fields_listbox.pack(fill=tk.X, padx=4, pady=2)
        self.fields_listbox.bind("<<ListboxSelect>>", self._on_field_select)

        # --- Single Column source ---
        props = ttk.LabelFrame(scroll_inner, text="Field Properties")
        props.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._tf_vars: dict[str, tk.Variable] = {}
        row = 0
        for label, key, default, widget_type in [
            ("Source Column", "source_column", "", "combo"),
            ("Static Text", "static_text", "", "entry"),
            ("X (mm)", "x", "50", "entry"),
            ("Y (mm)", "y", "50", "entry"),
            ("Width (mm)", "width", "100", "entry"),
            ("Alignment", "alignment", "center", "align"),
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
                cb.bind("<<ComboboxSelected>>", self._on_source_column_change)
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

        # --- Custom Fields (combine multiple columns) ---
        custom = ttk.LabelFrame(scroll_inner, text="Custom Fields (combine columns)")
        custom.pack(fill=tk.X, padx=4, pady=4)

        ttk.Label(custom, text="Select columns to combine:", foreground="gray").pack(anchor=tk.W, padx=4, pady=(4, 0))

        list_frame = ttk.Frame(custom)
        list_frame.pack(fill=tk.X, padx=4, pady=2)
        self._combined_listbox = tk.Listbox(list_frame, height=5, selectmode=tk.MULTIPLE,
                                             exportselection=False)
        self._combined_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        csb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._combined_listbox.yview)
        self._combined_listbox.configure(yscrollcommand=csb.set)
        csb.pack(side=tk.RIGHT, fill=tk.Y)

        sep_frame = ttk.Frame(custom)
        sep_frame.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(sep_frame, text="Separator:").pack(side=tk.LEFT)
        self._separator_var = tk.StringVar(value=" ")
        sep_combo = ttk.Combobox(sep_frame, textvariable=self._separator_var, width=10,
                                  values=["(space)", "(none)", " & ", " and ", ", ", " - "])
        sep_combo.pack(side=tk.LEFT, padx=4)
        ttk.Label(sep_frame, text="or type custom", foreground="gray").pack(side=tk.LEFT)

        ttk.Button(custom, text="Apply Custom Fields", command=self._apply_custom_fields).pack(pady=4)

    def _build_image_controls(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(top, text="+ Add Image", command=self._add_image_layer).pack(side=tk.LEFT)
        ttk.Button(top, text="- Remove", command=self._remove_image_layer).pack(side=tk.LEFT, padx=4)

        self.images_listbox = tk.Listbox(parent, height=5, exportselection=False)
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
        f.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # --- Output directory ---
        ttk.Label(f, text="Output folder:", font=("", 9, "bold")).pack(anchor=tk.W)
        dir_frame = ttk.Frame(f)
        dir_frame.pack(fill=tk.X, pady=2)
        self.outdir_var = tk.StringVar(value=self.layout.output_dir)
        dir_entry = ttk.Entry(dir_frame, textvariable=self.outdir_var)
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dir_frame, text="Browse…", width=8,
                   command=self._browse_output_dir).pack(side=tk.LEFT, padx=(4, 0))

        sep1 = ttk.Separator(f, orient=tk.HORIZONTAL)
        sep1.pack(fill=tk.X, pady=6)

        # --- Filename template ---
        ttk.Label(f, text="Filename template:", font=("", 9, "bold")).pack(anchor=tk.W)
        self.fname_var = tk.StringVar(value=self.layout.output_name_template)
        self.fname_var.trace_add("write", lambda *_: self._update_fname_preview())
        ttk.Entry(f, textvariable=self.fname_var).pack(fill=tk.X, pady=2)

        # Column picker — insert {Column} into the template
        pick_frame = ttk.Frame(f)
        pick_frame.pack(fill=tk.X, pady=2)
        ttk.Label(pick_frame, text="Insert column:").pack(side=tk.LEFT)
        self._fname_col_combo = ttk.Combobox(pick_frame, state="readonly", width=18)
        self._fname_col_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(pick_frame, text="Add", width=5,
                   command=self._insert_fname_column).pack(side=tk.LEFT)
        ttk.Label(f, text="Tip: combine columns like {First}_{Last}",
                  foreground="gray", font=("", 8)).pack(anchor=tk.W)

        # Live preview of resolved filename
        self.fname_preview_var = tk.StringVar(value="")
        ttk.Label(f, text="Preview:").pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(f, textvariable=self.fname_preview_var,
                  foreground="#555", wraplength=300).pack(anchor=tk.W)

        sep2 = ttk.Separator(f, orient=tk.HORIZONTAL)
        sep2.pack(fill=tk.X, pady=6)

        # --- Output mode ---
        ttk.Label(f, text="Output mode:", font=("", 9, "bold")).pack(anchor=tk.W)
        self.outmode_var = tk.StringVar(value=self.layout.output_mode)
        ttk.Radiobutton(f, text="One PDF per record",
                        variable=self.outmode_var, value="individual").pack(anchor=tk.W)
        ttk.Radiobutton(f, text="Combined multi-page PDF",
                        variable=self.outmode_var, value="combined").pack(anchor=tk.W)

        sep3 = ttk.Separator(f, orient=tk.HORIZONTAL)
        sep3.pack(fill=tk.X, pady=6)

        # --- Export buttons ---
        ttk.Button(f, text="Export Current Record",
                   command=self._export_current).pack(fill=tk.X, pady=2)
        ttk.Button(f, text="Export All Records",
                   command=self._export_all).pack(fill=tk.X, pady=2)
        ttk.Button(f, text="Export Selected…",
                   command=self._export_selected).pack(fill=tk.X, pady=2)

        # Row count label (updated when data loads)
        self._output_rows_label = ttk.Label(f, text="No data loaded", foreground="gray")
        self._output_rows_label.pack(anchor=tk.W, pady=(6, 0))

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

        # Canvas — bd=0 ensures no border offset between event coords and canvas coords
        self.canvas = tk.Canvas(parent, bg="#d0d0d0", highlightthickness=0, bd=0)
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
        # Update column combo and combined columns listbox
        if hasattr(self, "_col_combo"):
            self._col_combo["values"] = [""] + self.data.headers
        if hasattr(self, "_combined_listbox"):
            self._combined_listbox.delete(0, tk.END)
            for hdr in self.data.headers:
                self._combined_listbox.insert(tk.END, hdr)
        self._refresh_data_view()
        self._refresh_fname_columns()
        self._update_fname_preview()
        self._update_output_rows_label()
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
        self._update_fname_preview()
        self._refresh_preview()

    def _prev_record(self):
        self.data.prev()
        self._refresh_data_view()
        self._update_fname_preview()
        self._refresh_preview()

    def _goto_record(self):
        try:
            idx = int(self.goto_var.get()) - 1
            self.data.goto(idx)
            self._refresh_data_view()
            self._update_fname_preview()
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
            self.status_var.set(f"Background: {os.path.basename(path)}")
            self._refresh_preview()

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
        pw, ph = self.layout.page_dimensions_mm()
        tf = new_text_field(x=(pw - 100) / 2, y=ph / 2, width=100)
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
        self._refreshing = True
        self.fields_listbox.delete(0, tk.END)
        for tf in self.layout.text_fields:
            self.fields_listbox.insert(tk.END, tf.display_label())
        self._refreshing = False

    def _on_field_select(self, _event):
        if self._refreshing:
            return
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
        # Sync combined columns selection
        if hasattr(self, "_combined_listbox"):
            self._combined_listbox.selection_clear(0, tk.END)
            for i in range(self._combined_listbox.size()):
                if self._combined_listbox.get(i) in tf.combined_columns:
                    self._combined_listbox.selection_set(i)
        # Sync separator
        if hasattr(self, "_separator_var"):
            sep = tf.separator
            if sep == " ":
                self._separator_var.set("(space)")
            elif sep == "":
                self._separator_var.set("(none)")
            else:
                self._separator_var.set(sep)

    def _on_source_column_change(self, _event=None):
        """Auto-apply source column when user picks one from the combo."""
        sel = self.fields_listbox.curselection()
        if not sel:
            return
        tf = self.layout.text_fields[sel[0]]
        new_col = self._tf_vars["source_column"].get()
        if tf.source_column == new_col:
            return
        self._push_undo()
        tf.source_column = new_col
        if new_col:
            tf.combined_columns = []
        self._refresh_field_list()
        self.fields_listbox.selection_set(sel[0])
        self._refresh_preview()

    def _apply_text_field(self):
        sel = self.fields_listbox.curselection()
        if not sel:
            messagebox.showinfo("Info", "Click '+ Add Text Field' first, then configure and Apply.")
            return
        self._push_undo()
        tf = self.layout.text_fields[sel[0]]
        tf.source_column = self._tf_vars["source_column"].get()
        tf.static_text = self._tf_vars["static_text"].get()
        # If a single source column is set, clear combined columns
        if tf.source_column:
            tf.combined_columns = []
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

    def _get_separator_value(self) -> str:
        """Convert the separator combobox display value to actual separator string."""
        raw = self._separator_var.get()
        if raw == "(space)":
            return " "
        if raw == "(none)":
            return ""
        return raw

    def _apply_custom_fields(self):
        """Apply combined columns + separator to the selected text field."""
        sel = self.fields_listbox.curselection()
        if not sel:
            messagebox.showinfo("Info", "Click '+ Add Text Field' first, then select columns and Apply.")
            return
        self._push_undo()

        tf = self.layout.text_fields[sel[0]]

        # Get selected columns from the multi-select listbox
        selected_indices = self._combined_listbox.curselection()
        combined = [self._combined_listbox.get(i) for i in selected_indices]

        if len(combined) < 2:
            messagebox.showinfo("Custom Fields", "Select at least 2 columns to combine.")
            return

        tf.combined_columns = combined
        tf.separator = self._get_separator_value()
        # Clear single source_column since we're using combined mode
        tf.source_column = ""

        self._refresh_field_list()
        self.fields_listbox.selection_set(sel[0])
        self._refresh_preview()
        self.status_var.set(f"Combined {len(combined)} columns with separator '{tf.separator}'")

    # ==============================================================
    # Tools panel actions
    # ==============================================================
    def _toggle_center_grid(self):
        self._center_grid = self._center_grid_var.get()
        self._refresh_preview()

    def _center_selected_field(self):
        """Center the selected text field or image horizontally on the page."""
        obj = self._find_element(self._selected_element)
        if not obj:
            sel = self.fields_listbox.curselection()
            if sel:
                obj = self.layout.text_fields[sel[0]]
        if not obj:
            self.status_var.set("Select a field first")
            return
        self._push_undo()
        pw, ph = self.layout.page_dimensions_mm()
        bounds = self._element_bounds_mm(obj)
        if bounds:
            _, _, w, h = bounds
            obj.x = (pw - w) / 2
            obj.y = (ph - h) / 2
            self._sync_selected_to_panel()
            self._refresh_preview()
            self.status_var.set("Field centered")

    def _insert_date_field(self):
        """Insert today's date as a new static text field, centered on page."""
        import datetime
        today = datetime.date.today()
        day = today.day
        # Ordinal suffix
        if 11 <= day <= 13:
            suffix = "th"
        elif day % 10 == 1:
            suffix = "st"
        elif day % 10 == 2:
            suffix = "nd"
        elif day % 10 == 3:
            suffix = "rd"
        else:
            suffix = "th"
        date_str = f"{day}{suffix} {today.strftime('%B')}, {today.year}"

        self._push_undo()
        pw, ph = self.layout.page_dimensions_mm()
        tf = new_text_field(
            static_text=date_str,
            x=(pw - 80) / 2, y=ph / 2, width=80,
            font_size=12, alignment="center",
        )
        self.layout.text_fields.append(tf)
        self._refresh_field_list()
        self.fields_listbox.selection_set(tk.END)
        self._on_field_select(None)
        self._refresh_preview()
        self.status_var.set(f"Inserted date: {date_str}")

    def _draw_center_grid(self, page_w_px, page_h_px, ox, oy):
        """Draw centering guide lines (crosshair + thirds)."""
        cx = ox + page_w_px / 2
        cy = oy + page_h_px / 2
        # Center crosshair
        self.canvas.create_line(cx, oy, cx, oy + page_h_px,
                                 fill="#b0b0b0", dash=(4, 4), width=1)
        self.canvas.create_line(ox, cy, ox + page_w_px, cy,
                                 fill="#b0b0b0", dash=(4, 4), width=1)
        # Thirds
        for frac in (1/3, 2/3):
            x = ox + page_w_px * frac
            y = oy + page_h_px * frac
            self.canvas.create_line(x, oy, x, oy + page_h_px,
                                     fill="#d0d0d0", dash=(2, 4), width=1)
            self.canvas.create_line(ox, y, ox + page_w_px, y,
                                     fill="#d0d0d0", dash=(2, 4), width=1)

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
        self._refreshing = True
        self.images_listbox.delete(0, tk.END)
        for il in self.layout.image_layers:
            label = os.path.basename(il.file_path) if il.file_path else "(no file)"
            if il.source_column:
                label = f"[{il.source_column}]"
            self.images_listbox.insert(tk.END, label)
        self._refreshing = False

    def _on_image_select(self, _event):
        if self._refreshing:
            return
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
        self.outdir_var.set(self.layout.output_dir)
        self._refresh_fname_columns()
        self._update_fname_preview()
        self._update_output_rows_label()

    def _apply_output_settings(self):
        self.layout.output_name_template = self.fname_var.get()
        self.layout.output_mode = self.outmode_var.get()
        self.layout.output_dir = self.outdir_var.get()
        self.status_var.set("Output settings applied")

    def _browse_output_dir(self):
        d = filedialog.askdirectory(title="Select Output Folder",
                                    initialdir=self.outdir_var.get() or None)
        if d:
            self.outdir_var.set(d)
            self.layout.output_dir = d

    def _refresh_fname_columns(self):
        """Update the column dropdown in the filename builder."""
        cols = self.data.headers if self.data.rows else []
        self._fname_col_combo["values"] = cols
        if cols:
            self._fname_col_combo.current(0)

    def _insert_fname_column(self):
        """Insert the selected column as a {Column} placeholder into the filename template."""
        col = self._fname_col_combo.get()
        if not col:
            return
        current = self.fname_var.get()
        placeholder = "{" + col + "}"
        if current and not current.endswith(("_", "-", " ", "/")):
            placeholder = "_" + placeholder
        self.fname_var.set(current + placeholder)

    def _update_fname_preview(self):
        """Show a live preview of what the filename will look like for the current record."""
        from .formatter import safe_filename
        template = self.fname_var.get()
        record = self.data.current_record if self.data.rows else {}
        if record:
            preview = safe_filename(template, record) + ".pdf"
        else:
            preview = template + ".pdf"
        self.fname_preview_var.set(preview)

    def _update_output_rows_label(self):
        if self.data.rows:
            self._output_rows_label.config(
                text=f"{self.data.count} rows loaded from data")
        else:
            self._output_rows_label.config(text="No data loaded")

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
            pdf_bytes = render_single(self.layout, record,
                                      warn=lambda m: warnings.append(m),
                                      selected_id=self._selected_element)
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
                self._preview_has_pdf = False
                self._draw_placeholder_preview()
                return
            self._preview_has_pdf = True

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
            # Scale must be in screen-pixels-per-mm for hit-test/drag.
            # Use actual PDF page dimensions (from template if present)
            # rather than layout.page_dimensions_mm(), because when a
            # template PDF has different dimensions the rendered image
            # represents the template's page, not the layout's.
            pw_mm, ph_mm = self._actual_page_dims_mm(pdf_bytes)
            self._preview_scale = new_w / pw_mm
            self._preview_page_w = iw
            self._preview_page_h = ih

            self.canvas.delete("all")
            # Place image with NW anchor at a computed top-left so it's centered
            img_x = (cw - new_w) / 2
            img_y = (ch - new_h) / 2
            img_id = self.canvas.create_image(img_x, img_y, image=self._preview_image, anchor=tk.NW)
            # Query actual bounding box for bulletproof offset
            bbox = self.canvas.bbox(img_id)
            if bbox:
                self._preview_offset_x = bbox[0]
                self._preview_offset_y = bbox[1]
            else:
                self._preview_offset_x = img_x
                self._preview_offset_y = img_y

            # Draw grid overlay in test mode
            if self._test_mode:
                self._draw_grid_overlay(new_w, new_h)
            if self._center_grid:
                self._draw_center_grid(new_w, new_h,
                                        self._preview_offset_x, self._preview_offset_y)

            self._draw_selection_handles()

        except Exception as exc:
            self.status_var.set(f"Preview render: {exc}")
            self._preview_has_pdf = False
            self._draw_placeholder_preview()

        if warnings:
            self.status_var.set(f"Warnings: {'; '.join(warnings[:3])}")

    def _actual_page_dims_mm(self, pdf_bytes: bytes) -> tuple[float, float]:
        """Extract actual page dimensions (width, height) in mm from rendered PDF bytes.

        Falls back to layout.page_dimensions_mm() if extraction fails.
        """
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            mb = reader.pages[0].mediabox
            w_mm = float(mb.width) * 25.4 / 72.0
            h_mm = float(mb.height) * 25.4 / 72.0
            return (w_mm, h_mm)
        except Exception:
            return self.layout.page_dimensions_mm()

    def _pdf_bytes_to_image(self, pdf_bytes: bytes) -> Optional[Image.Image]:
        """Convert PDF bytes to a PIL Image. Tries multiple backends."""
        last_error = None

        # Try pdf2image (poppler) first
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(pdf_bytes, dpi=150, first_page=1, last_page=1)
            if images:
                return images[0]
        except ImportError:
            pass
        except Exception as exc:
            last_error = exc

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
        except Exception as exc:
            last_error = exc

        # Fallback: report error if backends failed with an exception
        if last_error:
            self.status_var.set(f"Preview backend error: {last_error}")
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
            bx, by, bw, bh = self._element_bounds_mm(tf)
            fx = ox + bx * scale
            fy = oy + by * scale
            fw = bw * scale
            fh = bh * scale

            if tf.combined_columns:
                parts = [record.get(col, "") for col in tf.combined_columns]
                raw = join_combined_values(parts, tf.separator)
            elif tf.source_column:
                raw = record.get(tf.source_column, tf.static_text)
            else:
                raw = tf.static_text
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

        # Center grid
        if self._center_grid:
            self._draw_center_grid(rw, rh, ox, oy)

        # Selection handles
        self._draw_selection_handles()

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
    # Canvas interaction (drag-drop, resize, coordinate display)
    # ==============================================================
    def _event_to_canvas(self, event) -> tuple[float, float]:
        """Convert event coordinates to canvas coordinates (accounts for border/scroll)."""
        return (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

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

    def _element_bounds_mm(self, elem) -> Optional[tuple[float, float, float, float]]:
        """Return (x, y, w, h) in mm for an element."""
        if isinstance(elem, TextFieldDef):
            h = elem.font_size * elem.line_spacing * MM_PER_PT
            return (elem.x, elem.y, elem.width, h)
        elif isinstance(elem, ImageLayerDef):
            return (elem.x, elem.y, elem.width, elem.height)
        return None

    def _corner_hit_test(self, mx, my, elem) -> Optional[str]:
        """Check if (mx, my) is near a resize corner of elem. Returns 'tl','tr','bl','br' or None."""
        bounds = self._element_bounds_mm(elem)
        if not bounds:
            return None
        x, y, w, h = bounds
        tol = RESIZE_HANDLE_MM
        corners = {
            'br': (x + w, y + h),
            'bl': (x, y + h),
            'tr': (x + w, y),
            'tl': (x, y),
        }
        for name, (cx, cy) in corners.items():
            if abs(mx - cx) <= tol and abs(my - cy) <= tol:
                return name
        return None

    def _on_canvas_motion(self, event):
        cx, cy = self._event_to_canvas(event)
        mx, my = self._canvas_to_mm(cx, cy)
        self._cursor_pos_mm = (mx, my)
        self.coord_label.config(text=f"X: {mx:.1f} mm  Y: {my:.1f} mm")

        # Update cursor based on what's under it
        if self._drag_start:
            return  # don't change cursor mid-drag
        elem_id = self._hit_test(mx, my)
        if elem_id:
            obj = self._find_element(elem_id)
            if obj:
                corner = self._corner_hit_test(mx, my, obj)
                if corner in ('br', 'tl'):
                    self.canvas.config(cursor="bottom_right_corner")
                elif corner in ('bl', 'tr'):
                    self.canvas.config(cursor="bottom_left_corner")
                else:
                    self.canvas.config(cursor="fleur")
            else:
                self.canvas.config(cursor="")
        else:
            self.canvas.config(cursor="")

    def _on_canvas_click(self, event):
        cx, cy = self._event_to_canvas(event)
        mx, my = self._canvas_to_mm(cx, cy)
        elem = self._hit_test(mx, my)
        if elem:
            self._selected_element = elem
            obj = self._find_element(elem)
            if obj:
                # Check if clicking a resize corner
                corner = self._corner_hit_test(mx, my, obj)
                self._drag_start = (cx, cy)
                self._drag_elem_start = (obj.x, obj.y)
                if corner:
                    self._resize_mode = True
                    self._resize_edge = corner
                    if isinstance(obj, ImageLayerDef):
                        self._resize_start_size = (obj.width, obj.height)
                    else:
                        bounds = self._element_bounds_mm(obj)
                        self._resize_start_size = (bounds[2], bounds[3]) if bounds else (obj.width, 10.0)
                else:
                    self._resize_mode = False
                    self._resize_edge = None
                    self._resize_start_size = None
            self._refresh_preview()  # redraw to show selection handles
        else:
            if self._selected_element:
                self._selected_element = None
                self._refresh_preview()  # redraw to remove handles

    def _on_canvas_drag(self, event):
        if not self._drag_start or not self._drag_elem_start:
            return
        cx, cy = self._event_to_canvas(event)
        scale = getattr(self, "_preview_scale", 1) or 1
        dx = (cx - self._drag_start[0]) / scale
        dy = (cy - self._drag_start[1]) / scale
        obj = self._find_element(self._selected_element)
        if not obj:
            return

        if self._resize_mode and self._resize_edge and self._resize_start_size:
            self._handle_resize(obj, dx, dy)
        else:
            new_x = self._drag_elem_start[0] + dx
            new_y = self._drag_elem_start[1] + dy
            if self._snap_to_grid:
                new_x = round(new_x / GRID_STEP_MM) * GRID_STEP_MM
                new_y = round(new_y / GRID_STEP_MM) * GRID_STEP_MM
            obj.x = new_x
            obj.y = new_y
        self._refresh_preview()

    def _handle_resize(self, obj, dx, dy):
        """Resize obj based on drag delta and active corner."""
        edge = self._resize_edge
        sw, sh = self._resize_start_size
        sx, sy = self._drag_elem_start

        is_image = isinstance(obj, ImageLayerDef)
        lock = is_image and obj.lock_aspect
        aspect = sh / sw if sw > 0 else 1.0

        if edge == 'br':
            new_w = max(MIN_ELEMENT_SIZE_MM, sw + dx)
            new_h = max(MIN_ELEMENT_SIZE_MM, sh + dy)
            if lock:
                new_h = new_w * aspect
        elif edge == 'bl':
            new_w = max(MIN_ELEMENT_SIZE_MM, sw - dx)
            new_h = max(MIN_ELEMENT_SIZE_MM, sh + dy)
            if lock:
                new_h = new_w * aspect
            obj.x = sx + sw - new_w
        elif edge == 'tr':
            new_w = max(MIN_ELEMENT_SIZE_MM, sw + dx)
            new_h = max(MIN_ELEMENT_SIZE_MM, sh - dy)
            if lock:
                new_h = new_w * aspect
            obj.y = sy + sh - new_h
        elif edge == 'tl':
            new_w = max(MIN_ELEMENT_SIZE_MM, sw - dx)
            if lock:
                new_h = new_w * aspect
            else:
                new_h = max(MIN_ELEMENT_SIZE_MM, sh - dy)
            obj.x = sx + sw - new_w
            obj.y = sy + sh - new_h
        else:
            return

        if self._snap_to_grid:
            new_w = max(MIN_ELEMENT_SIZE_MM, round(new_w / GRID_STEP_MM) * GRID_STEP_MM)
            new_h = max(MIN_ELEMENT_SIZE_MM, round(new_h / GRID_STEP_MM) * GRID_STEP_MM)

        if isinstance(obj, TextFieldDef):
            obj.width = new_w  # text fields only resize width
        elif isinstance(obj, ImageLayerDef):
            obj.width = new_w
            obj.height = new_h

    def _on_canvas_release(self, event):
        if self._drag_start and self._drag_elem_start:
            self._push_undo()
            self._sync_selected_to_panel()
        self._drag_start = None
        self._drag_elem_start = None
        self._resize_mode = False
        self._resize_edge = None
        self._resize_start_size = None

    def _hit_test(self, mx, my) -> Optional[str]:
        """Return element id at (mx, my) mm, or None."""
        min_click = 4.0  # mm — minimum clickable zone so small elements are easy to hit
        # Check text fields (reverse order = topmost first)
        for tf in reversed(self.layout.text_fields):
            bounds = self._element_bounds_mm(tf)
            if bounds:
                x, y, w, h = bounds
                h = max(h, min_click)
                if x <= mx <= x + w and y <= my <= y + h:
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
            self._tf_vars["width"].set(f"{obj.width:.1f}")
        elif isinstance(obj, ImageLayerDef):
            self._img_vars["x"].set(f"{obj.x:.1f}")
            self._img_vars["y"].set(f"{obj.y:.1f}")
            self._img_vars["width"].set(f"{obj.width:.1f}")
            self._img_vars["height"].set(f"{obj.height:.1f}")

    def _draw_selection_handles(self):
        """Draw resize corner handles on the currently selected element.

        The selection rectangle itself is rendered into the PDF by the
        renderer (pixel-perfect).  Here we only draw the small corner
        grab-handles as canvas overlays.
        """
        obj = self._find_element(self._selected_element)
        if not obj:
            return
        bounds = self._element_bounds_mm(obj)
        if not bounds:
            return
        x, y, w, h = bounds
        scale = getattr(self, "_preview_scale", 1)
        ox = getattr(self, "_preview_offset_x", 0)
        oy = getattr(self, "_preview_offset_y", 0)

        handle_px = 5
        # Element edges in canvas pixels
        ex = ox + x * scale
        ey = oy + y * scale
        ew = w * scale
        eh = h * scale

        # For placeholder preview (no PDF bbox) draw a thin outline too
        if not getattr(self, "_preview_has_pdf", False):
            pad = 3
            self.canvas.create_rectangle(ex - pad, ey - pad, ex + ew + pad, ey + eh + pad,
                                          outline="#4a90d9", width=2)

        # Corner handles
        corners = [(ex, ey), (ex + ew, ey), (ex, ey + eh), (ex + ew, ey + eh)]
        for cx_px, cy_px in corners:
            self.canvas.create_rectangle(
                cx_px - handle_px, cy_px - handle_px,
                cx_px + handle_px, cy_px + handle_px,
                fill="#4a90d9", outline="#ffffff", width=1
            )

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
    def _ensure_output_dir(self) -> str:
        """Return the output directory, prompting the user to pick one if not set."""
        self._apply_output_settings()
        out_dir = self.layout.output_dir
        if not out_dir or not os.path.isdir(out_dir):
            out_dir = filedialog.askdirectory(
                title="Select Output Folder",
                initialdir=out_dir or None)
            if not out_dir:
                return ""
            self.outdir_var.set(out_dir)
            self.layout.output_dir = out_dir
        return out_dir

    def _export_current(self):
        if not self.data.rows:
            messagebox.showinfo("Export", "No data loaded.")
            return
        from .formatter import safe_filename
        out_dir = self._ensure_output_dir()
        if not out_dir:
            return
        template = self.layout.output_name_template
        record = self.data.current_record
        fname = safe_filename(template, record) + ".pdf"
        path = os.path.join(out_dir, fname)
        warnings: list[str] = []
        try:
            os.makedirs(out_dir, exist_ok=True)
            pdf_bytes = render_single(self.layout, record,
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
        out_dir = self._ensure_output_dir()
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
