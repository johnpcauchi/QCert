"""Layout profile persistence — save / load / duplicate / version."""

import copy
import json
import os
import shutil
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class TextFieldDef:
    """A single text element placed on the certificate."""
    id: str = ""
    source_column: str = ""          # spreadsheet column name (empty = static text)
    static_text: str = ""            # used when source_column is empty
    combined_columns: list[str] = field(default_factory=list)  # multiple columns to join
    separator: str = " "             # separator for combined columns
    x: float = 50.0                  # mm from left
    y: float = 50.0                  # mm from top
    width: float = 100.0             # mm
    alignment: str = "center"        # left | center | right
    font_family: str = "Helvetica"
    font_size: float = 12.0          # pt
    font_weight: str = "normal"      # normal | bold
    font_colour: str = "#000000"
    line_spacing: float = 1.2
    wrap: bool = True
    format_rule: str = ""            # e.g. "uppercase", "titlecase", "sentencecase", "DD MMM YYYY"
    show_bbox: bool = False

    def display_label(self) -> str:
        if self.combined_columns:
            cols = " + ".join(f"[{c}]" for c in self.combined_columns)
            return cols[:30] if len(cols) <= 30 else cols[:27] + "..."
        if self.source_column:
            return f"[{self.source_column}]"
        if self.static_text:
            return self.static_text[:30] if len(self.static_text) <= 30 else self.static_text[:27] + "..."
        return "(empty)"


@dataclass
class ImageLayerDef:
    """An image overlay (logo, signature, stamp)."""
    id: str = ""
    file_path: str = ""              # relative or absolute
    source_column: str = ""          # if set, path comes from spreadsheet per-record
    x: float = 10.0
    y: float = 10.0
    width: float = 30.0              # mm
    height: float = 30.0             # mm
    lock_aspect: bool = True
    rotation: float = 0.0            # degrees
    opacity: float = 1.0


@dataclass
class LayoutProfile:
    """Complete certificate layout configuration."""
    name: str = "Untitled"
    page_size: str = "A4"            # A4 | Letter
    orientation: str = "landscape"   # landscape | portrait
    template_pdf: str = ""           # path to background PDF
    text_fields: list[TextFieldDef] = field(default_factory=list)
    image_layers: list[ImageLayerDef] = field(default_factory=list)
    output_name_template: str = "certificate_{Index}"
    output_mode: str = "individual"  # individual | combined
    output_dir: str = ""             # target folder for exports
    output_dpi: int = 150            # DPI for rasterised output
    version: int = 1
    created: str = ""
    modified: str = ""

    def page_dimensions_mm(self) -> tuple[float, float]:
        """Return (width, height) in mm."""
        sizes = {
            "A4": (210.0, 297.0),
            "Letter": (215.9, 279.4),
        }
        w, h = sizes.get(self.page_size, (210.0, 297.0))
        if self.orientation == "landscape":
            return (h, w)
        return (w, h)


# ------------------------------------------------------------------
# ID generation helper
# ------------------------------------------------------------------
_counter = 0


def _next_id(prefix: str = "elem") -> str:
    global _counter
    _counter += 1
    return f"{prefix}_{_counter}_{int(time.time() * 1000) % 100000}"


def new_text_field(**kwargs) -> TextFieldDef:
    return TextFieldDef(id=_next_id("txt"), **kwargs)


def new_image_layer(**kwargs) -> ImageLayerDef:
    return ImageLayerDef(id=_next_id("img"), **kwargs)


# ------------------------------------------------------------------
# Serialisation
# ------------------------------------------------------------------

def _layout_to_dict(layout: LayoutProfile) -> dict:
    return asdict(layout)


def _layout_from_dict(d: dict) -> LayoutProfile:
    text_fields = [TextFieldDef(**tf) for tf in d.pop("text_fields", [])]
    image_layers = [ImageLayerDef(**il) for il in d.pop("image_layers", [])]
    lp = LayoutProfile(**d)
    lp.text_fields = text_fields
    lp.image_layers = image_layers
    return lp


def save_layout(layout: LayoutProfile, path: str) -> None:
    """Write *layout* as JSON to *path*, creating a backup of the previous version."""
    layout.modified = time.strftime("%Y-%m-%dT%H:%M:%S")
    if not layout.created:
        layout.created = layout.modified

    # Backup existing file
    if os.path.isfile(path):
        backup = path + f".bak.{int(time.time())}"
        shutil.copy2(path, backup)
        # Keep only the 5 most recent backups
        _prune_backups(path, keep=5)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_layout_to_dict(layout), f, indent=2, ensure_ascii=False)


def load_layout(path: str) -> LayoutProfile:
    """Read a LayoutProfile from a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _layout_from_dict(data)


def duplicate_layout(layout: LayoutProfile) -> LayoutProfile:
    """Return a deep copy with a new name."""
    dup = copy.deepcopy(layout)
    dup.name = layout.name + " (copy)"
    dup.version = 1
    dup.created = ""
    dup.modified = ""
    return dup


def default_layout() -> LayoutProfile:
    """Return a blank default layout."""
    return LayoutProfile(
        name="Default",
        page_size="A4",
        orientation="landscape",
    )


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def join_combined_values(parts: list[str], separator: str) -> str:
    """Join multiple column values with smart separator logic.

    For & / and / , separators: auto-capitalise each part.
    For & / and with 3+ items: "A, B & C" or "A, B and C".
    """
    parts = [p for p in parts if p]
    if not parts:
        return ""

    sep_clean = separator.strip()
    sep_lower = sep_clean.lower()
    smart = sep_lower in ("&", "and", ",")

    if smart:
        parts = [p.strip().title() for p in parts]

    if len(parts) == 1:
        return parts[0]

    # For & / and: use commas between early items, separator before last
    if sep_lower in ("&", "and"):
        if len(parts) == 2:
            return f"{parts[0]} {sep_clean} {parts[1]}"
        return ", ".join(parts[:-1]) + f" {sep_clean} " + parts[-1]

    return separator.join(parts)


def _prune_backups(base_path: str, keep: int = 5) -> None:
    directory = os.path.dirname(base_path) or "."
    base_name = os.path.basename(base_path)
    backups = sorted(
        [f for f in os.listdir(directory) if f.startswith(base_name + ".bak.")],
        reverse=True,
    )
    for old in backups[keep:]:
        try:
            os.remove(os.path.join(directory, old))
        except OSError:
            pass
