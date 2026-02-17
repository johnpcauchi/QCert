"""PDF rendering engine — places text and images onto a background template."""

import io
import os
from typing import Optional, Callable

from reportlab.lib.pagesizes import A4, letter, landscape, portrait
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from qcert.layout import join_combined_values

try:
    from PyPDF2 import PdfReader, PdfWriter, PdfMerger
except ImportError:
    from PyPDF2 import PdfFileReader as PdfReader, PdfFileWriter as PdfWriter

from .layout import LayoutProfile, TextFieldDef, ImageLayerDef
from .formatter import apply_format, sanitize_for_pdf, safe_filename

# ------------------------------------------------------------------
# Font helpers
# ------------------------------------------------------------------

_BUILTIN_FONTS = {
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
}

_registered_fonts: set[str] = set()


def _resolve_font(family: str, weight: str) -> str:
    """Return a ReportLab font name, registering TTF files if needed."""
    # Map friendly names to ReportLab built-ins
    alias = {
        "helvetica": "Helvetica",
        "times": "Times-Roman",
        "courier": "Courier",
    }
    base = alias.get(family.lower(), family)

    if weight == "bold":
        bold_name = base + "-Bold"
        if bold_name in _BUILTIN_FONTS:
            return bold_name

    if base in _BUILTIN_FONTS or base in _registered_fonts:
        return base

    # Attempt to register as a TrueType file
    for ext in (".ttf", ".TTF"):
        if os.path.isfile(family + ext):
            try:
                pdfmetrics.registerFont(TTFont(family, family + ext))
                _registered_fonts.add(family)
                return family
            except Exception:
                pass
        if os.path.isfile(family):
            try:
                pdfmetrics.registerFont(TTFont(base, family))
                _registered_fonts.add(base)
                return base
            except Exception:
                pass

    # Fallback
    return "Helvetica-Bold" if weight == "bold" else "Helvetica"


def _page_size(layout: LayoutProfile):
    """Return a ReportLab page-size tuple."""
    base = A4 if layout.page_size == "A4" else letter
    if layout.orientation == "landscape":
        return landscape(base)
    return portrait(base)


# ------------------------------------------------------------------
# Single-page overlay rendering
# ------------------------------------------------------------------

def _get_template_page_size(template_pdf: Optional[str]):
    """Return (width_pt, height_pt) of page 1 of the template, or None."""
    if not template_pdf or not os.path.isfile(template_pdf):
        return None
    try:
        reader = PdfReader(template_pdf)
        mb = reader.pages[0].mediabox
        return (float(mb.width), float(mb.height))
    except Exception:
        return None


def _render_overlay(layout: LayoutProfile, record: dict[str, str],
                    warn: Optional[Callable] = None,
                    selected_id: Optional[str] = None,
                    page_size_override=None) -> bytes:
    """Render text fields and image layers into an in-memory PDF page (no background)."""
    buf = io.BytesIO()
    ps = page_size_override or _page_size(layout)
    page_w, page_h = ps

    c = rl_canvas.Canvas(buf, pagesize=ps)

    # --- Images first (behind text) ---
    for img_def in layout.image_layers:
        _draw_image(c, img_def, record, page_h, warn, selected_id=selected_id)

    # --- Text fields ---
    for tf in layout.text_fields:
        _draw_text(c, tf, record, page_h, warn, selected_id=selected_id)

    c.showPage()
    c.save()
    return buf.getvalue()


def _resolve_placeholders(text: str, record: dict[str, str]) -> str:
    """Replace {ColumnName} placeholders in *text* with values from *record*."""
    import re
    def _repl(m):
        key = m.group(1)
        return record.get(key, m.group(0))
    return re.sub(r"\{([^{}]+)\}", _repl, text)


def _draw_text(c, tf: TextFieldDef, record: dict[str, str],
               page_h: float, warn: Optional[Callable],
               selected_id: Optional[str] = None) -> None:
    if tf.combined_columns:
        # Join multiple columns with smart separator logic
        parts = [record.get(col, "") for col in tf.combined_columns]
        raw = join_combined_values(parts, tf.separator)
    elif tf.source_column:
        raw = record.get(tf.source_column, tf.static_text)
    else:
        # When no source column, resolve {Column} placeholders in static text
        raw = _resolve_placeholders(tf.static_text, record)
    if not raw and (tf.source_column or tf.combined_columns):
        if warn:
            cols = ", ".join(tf.combined_columns) if tf.combined_columns else tf.source_column
            warn(f"Missing value for column(s) '{cols}'")
        raw = ""

    text = sanitize_for_pdf(apply_format(raw, tf.format_rule, warn))
    if not text:
        return

    font_name = _resolve_font(tf.font_family, tf.font_weight)
    font_size = tf.font_size

    # Convert mm positions to points (ReportLab uses bottom-left origin)
    x_pt = tf.x * mm
    y_pt = page_h - tf.y * mm  # flip Y

    c.saveState()
    c.setFont(font_name, font_size)

    try:
        colour = HexColor(tf.font_colour)
    except Exception:
        colour = HexColor("#000000")
    c.setFillColor(colour)

    width_pt = tf.width * mm
    leading = font_size * tf.line_spacing

    # Debug bounding box
    if tf.show_bbox:
        c.saveState()
        c.setStrokeColor(HexColor("#FF0000"))
        c.setLineWidth(0.5)
        c.rect(x_pt, y_pt - leading, width_pt, leading, stroke=1, fill=0)
        c.restoreState()

    # Selection indicator (blue bbox when this element is selected)
    if selected_id and tf.id == selected_id:
        pad = 2  # points padding around the element
        c.saveState()
        c.setStrokeColor(HexColor("#4a90d9"))
        c.setLineWidth(1.5)
        c.setDash(3, 2)
        c.rect(x_pt - pad, y_pt - leading - pad,
               width_pt + 2 * pad, leading + 2 * pad,
               stroke=1, fill=0)
        c.restoreState()

    if tf.wrap:
        text_obj = c.beginText(x_pt, y_pt - font_size)
        text_obj.setFont(font_name, font_size, leading=leading)
        text_obj.setFillColor(colour)

        # Simple word-wrap
        for line in _wrap_text(text, font_name, font_size, width_pt):
            if tf.alignment == "center":
                lw = pdfmetrics.stringWidth(line, font_name, font_size)
                text_obj.setTextOrigin(x_pt + (width_pt - lw) / 2, text_obj.getY())
            elif tf.alignment == "right":
                lw = pdfmetrics.stringWidth(line, font_name, font_size)
                text_obj.setTextOrigin(x_pt + width_pt - lw, text_obj.getY())
            else:
                text_obj.setTextOrigin(x_pt, text_obj.getY())
            text_obj.textLine(line)
        c.drawText(text_obj)
    else:
        # Single-line
        if tf.alignment == "center":
            c.drawCentredString(x_pt + width_pt / 2, y_pt - font_size, text)
        elif tf.alignment == "right":
            c.drawRightString(x_pt + width_pt, y_pt - font_size, text)
        else:
            c.drawString(x_pt, y_pt - font_size, text)

    c.restoreState()


def _wrap_text(text: str, font_name: str, font_size: float,
               max_width: float) -> list[str]:
    """Simple greedy word-wrap."""
    words = text.replace("\n", " \n ").split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if word == "\n":
            lines.append(current)
            current = ""
            continue
        test = (current + " " + word).strip()
        w = pdfmetrics.stringWidth(test, font_name, font_size)
        if w <= max_width or not current:
            current = test
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_image(c, img: ImageLayerDef, record: dict[str, str],
                page_h: float, warn: Optional[Callable],
                selected_id: Optional[str] = None) -> None:
    path = img.file_path
    if img.source_column:
        path = record.get(img.source_column, path)
    if not path or not os.path.isfile(path):
        if warn:
            warn(f"Image not found: {path}")
        return

    x_pt = img.x * mm
    y_pt = page_h - img.y * mm - img.height * mm
    w_pt = img.width * mm
    h_pt = img.height * mm

    try:
        reader = ImageReader(path)
        c.saveState()
        if img.opacity < 1.0:
            c.setFillAlpha(img.opacity)
            c.setStrokeAlpha(img.opacity)
        if img.rotation:
            cx = x_pt + w_pt / 2
            cy = y_pt + h_pt / 2
            c.translate(cx, cy)
            c.rotate(img.rotation)
            c.drawImage(reader, -w_pt / 2, -h_pt / 2, w_pt, h_pt,
                        preserveAspectRatio=img.lock_aspect, mask="auto")
        else:
            c.drawImage(reader, x_pt, y_pt, w_pt, h_pt,
                        preserveAspectRatio=img.lock_aspect, mask="auto")
        c.restoreState()
    except Exception as exc:
        if warn:
            warn(f"Cannot draw image {path}: {exc}")

    # Selection indicator
    if selected_id and img.id == selected_id:
        pad = 2
        c.saveState()
        c.setStrokeColor(HexColor("#4a90d9"))
        c.setLineWidth(1.5)
        c.setDash(3, 2)
        c.rect(x_pt - pad, y_pt - pad,
               w_pt + 2 * pad, h_pt + 2 * pad,
               stroke=1, fill=0)
        c.restoreState()


# ------------------------------------------------------------------
# Merge overlay onto background template
# ------------------------------------------------------------------

def _merge_page(background_pdf: Optional[str], overlay_bytes: bytes) -> bytes:
    """Merge *overlay_bytes* (a single-page PDF) onto page 1 of *background_pdf*.
    If no background is provided, the overlay is returned as-is."""
    if not background_pdf or not os.path.isfile(background_pdf):
        return overlay_bytes

    bg_reader = PdfReader(background_pdf)
    bg_page = bg_reader.pages[0]

    overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
    overlay_page = overlay_reader.pages[0]

    bg_page.merge_page(overlay_page)

    writer = PdfWriter()
    writer.add_page(bg_page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def render_single(layout: LayoutProfile, record: dict[str, str],
                  warn: Optional[Callable] = None,
                  selected_id: Optional[str] = None) -> bytes:
    """Render one certificate and return raw PDF bytes."""
    # Use the template's page dimensions so the overlay coordinate system
    # matches the background — this prevents content from shifting when
    # the template dimensions differ from layout.page_size.
    bg_size = _get_template_page_size(layout.template_pdf)
    overlay = _render_overlay(layout, record, warn, selected_id=selected_id,
                              page_size_override=bg_size)
    return _merge_page(layout.template_pdf, overlay)


def render_batch(layout: LayoutProfile, records: list[dict[str, str]],
                 output_dir: str,
                 indices: Optional[list[int]] = None,
                 progress: Optional[Callable] = None,
                 warn: Optional[Callable] = None) -> dict:
    """Render multiple certificates.

    Returns a summary dict: {generated: int, skipped: int, errors: list[str], files: list[str]}
    """
    os.makedirs(output_dir, exist_ok=True)

    if indices is not None:
        selected = [(i, records[i]) for i in indices if 0 <= i < len(records)]
    else:
        selected = list(enumerate(records))

    total = len(selected)
    summary: dict = {"generated": 0, "skipped": 0, "errors": [], "files": []}

    if layout.output_mode == "combined":
        return _render_combined(layout, selected, output_dir, progress, warn, summary)

    for n, (idx, rec) in enumerate(selected):
        try:
            pdf_bytes = render_single(layout, rec, warn)
            fname = safe_filename(layout.output_name_template, rec) + ".pdf"
            fpath = os.path.join(output_dir, fname)
            # Avoid overwriting: append index if collision
            if os.path.exists(fpath):
                base, ext = os.path.splitext(fpath)
                fpath = f"{base}_{idx}{ext}"
            with open(fpath, "wb") as f:
                f.write(pdf_bytes)
            summary["generated"] += 1
            summary["files"].append(fpath)
        except Exception as exc:
            summary["errors"].append(f"Row {idx}: {exc}")
        if progress:
            progress(n + 1, total)

    summary["skipped"] = total - summary["generated"] - len(summary["errors"])
    return summary


def _render_combined(layout, selected, output_dir, progress, warn, summary):
    """Render all selected records into one multi-page PDF."""
    merger = PdfMerger()
    total = len(selected)

    for n, (idx, rec) in enumerate(selected):
        try:
            pdf_bytes = render_single(layout, rec, warn)
            merger.append(io.BytesIO(pdf_bytes))
            summary["generated"] += 1
        except Exception as exc:
            summary["errors"].append(f"Row {idx}: {exc}")
        if progress:
            progress(n + 1, total)

    fpath = os.path.join(output_dir, "certificates_combined.pdf")
    with open(fpath, "wb") as f:
        merger.write(f)
    merger.close()
    summary["files"].append(fpath)
    summary["skipped"] = total - summary["generated"] - len(summary["errors"])
    return summary
