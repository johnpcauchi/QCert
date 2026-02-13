# QCert — Desktop Certificate Generator

A form-driven, offline desktop application that lets you load a spreadsheet of records and produce consistent, print-ready PDF certificates by placing text and image elements onto a background template.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Generate the bundled example files
python create_examples.py

# 3. Launch the application
python run_qcert.py
```

On Windows you can also double-click `run_qcert.bat`.

## Project Structure

```
QCert/
├── qcert/                  # Application package
│   ├── __init__.py
│   ├── __main__.py         # python -m qcert
│   ├── app.py              # Main GUI (tkinter)
│   ├── data_import.py      # Excel loading & record navigation
│   ├── formatter.py        # Text formatting rules & sanitisation
│   ├── layout.py           # Layout profile persistence (JSON)
│   └── renderer.py         # PDF rendering engine (ReportLab + PyPDF2)
├── examples/
│   ├── data/               # Sample spreadsheet
│   ├── templates/          # Background PDF templates
│   └── signatures/         # Example signature images
├── layouts/                # Saved layout profiles (.json)
├── create_examples.py      # Generates example files
├── run_qcert.py            # Launcher (sets up paths, checks deps)
├── run_qcert.bat           # Windows double-click launcher
├── build.py                # PyInstaller packaging script
├── requirements.txt
└── README.md
```

## How to Use

### 1. Import Data

- **File > Open Spreadsheet** (or `Ctrl+O`) to load an `.xlsx` file.
- The first row is treated as column headers; every subsequent row is one certificate record.
- Use **Next/Prev** buttons (or press `n`/`p`) to navigate records.
- Use the **Go to row** field or **Search** to jump to a specific record.

### 2. Choose a Background Template

- **Template > Set Background PDF** to select a PDF file that will serve as the certificate page design.
- The background is not modified — your text and images are overlaid on top.

### 3. Design the Certificate

**Text Fields tab:**

1. Click **+ Add Text Field**.
2. Set **Source Column** to a spreadsheet column (e.g., `Name`) — or leave it blank and type **Static Text** for fixed labels.
3. Adjust position (X/Y in mm), width, alignment, font family, size, weight, colour.
4. Set a **Format Rule** (see below).
5. Click **Apply** to update the preview.

**Images tab:**

1. Click **+ Add Image** and browse to a PNG/JPG file.
2. Set position, size, rotation, opacity.
3. For per-record images (e.g., different signatures), set **Source Column** to a column containing file paths.

**Drag-and-drop:** Click an element on the preview canvas and drag it. Use arrow keys to nudge (Shift+arrow for fine nudge).

### 4. Format Rules

| Rule | Effect |
|---|---|
| `uppercase` | ALICE JOHNSON |
| `lowercase` | alice johnson |
| `titlecase` | Alice Johnson |
| `DD MMM YYYY` | 15 Jun 2025 |
| `DD/MM/YYYY` | 15/06/2025 |
| `YYYY-MM-DD` | 2025-06-15 |
| `today` | Today's date (dd Mon yyyy) |
| `today:%d %B %Y` | Today's date with custom strftime format |
| `number:2` | Format as number with 2 decimal places |

Missing values are handled gracefully — empty cells produce blank output and a warning.

### 5. Export Certificates

- **Export > Export Current Record** — single PDF for the displayed record.
- **Export > Export All Records** — batch export every row.
- **Export > Export Selected** — specify row numbers/ranges (e.g., `1-5, 8, 10`).

**Output settings** (Output tab):

- **Filename template:** uses `{ColumnName}` placeholders, e.g., `{Name}_{ID}` produces `Alice Johnson_CERT-001.pdf`.
- **Output mode:** one PDF per record, or a single combined multi-page PDF.

A progress bar and post-run summary (generated / skipped / errors) are shown during batch export.

### 6. Save and Load Layouts

- **File > Save Layout** (`Ctrl+S`) saves the current field definitions, image layers, template reference, and output rules as a `.json` file.
- **File > Load Layout** restores a saved profile.
- **File > Duplicate Layout** creates a copy for a new certificate type.
- **File > Reset to Default** clears everything.
- Backups of the last 5 versions are kept automatically alongside the layout file.

## Adding New Spreadsheet Columns

1. Open your `.xlsx` file in Excel and add a new column header in row 1.
2. Fill in the data for each row.
3. Reload the spreadsheet in QCert (**File > Open Spreadsheet**).
4. Add a new text field and set its **Source Column** to the new column name.
5. The column dropdown auto-populates from the spreadsheet headers.

## Creating New Template / Layout Profiles

### New background template

1. Design your certificate page in any tool (Word, Illustrator, Canva, etc.) and export as a single-page PDF.
2. In QCert: **Template > Set Background PDF** and select it.
3. Place your text fields and images relative to the new design.
4. Save the layout (**File > Save Layout As**) with a descriptive name.

### New layout profile from scratch

1. **File > Reset to Default**.
2. Set the page size and orientation under the **Template** menu.
3. Choose a background PDF.
4. Add text fields and images, position them, and save.

### Duplicating an existing layout

1. Load the layout you want to start from.
2. **File > Duplicate Layout** — this creates a copy with "(copy)" appended.
3. Modify as needed and save under a new filename.

## Layout Profile Format (JSON)

Layout files are plain JSON and can be edited by hand:

```json
{
  "name": "Standard Certificate",
  "page_size": "A4",
  "orientation": "landscape",
  "template_pdf": "examples/templates/certificate_bg.pdf",
  "text_fields": [
    {
      "id": "txt_name",
      "source_column": "Name",
      "static_text": "",
      "x": 60.0,
      "y": 88.0,
      "width": 177.0,
      "alignment": "center",
      "font_family": "Times-Roman",
      "font_size": 28.0,
      "font_weight": "bold",
      "font_colour": "#1A237E",
      "line_spacing": 1.2,
      "wrap": false,
      "format_rule": "uppercase",
      "show_bbox": false
    }
  ],
  "image_layers": [],
  "output_name_template": "{Name}_{ID}",
  "output_mode": "individual"
}
```

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `n` | Next record |
| `p` | Previous record |
| `Arrow keys` | Nudge selected element (0.5 mm) |
| `Shift+Arrow` | Fine nudge (0.1 mm) |
| `Ctrl+S` | Save layout |
| `Ctrl+O` | Open spreadsheet |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |

## Packaging for Distribution (Windows)

```bash
pip install pyinstaller
python build.py
```

This produces a `dist/QCert/` folder containing the standalone application with all dependencies bundled. Zip and distribute — no Python installation or admin rights required on the target machine.

## Troubleshooting

### Fonts

- QCert uses ReportLab's built-in PDF fonts by default: **Helvetica**, **Times-Roman**, **Courier** (plus bold/italic variants).
- To use a custom TrueType font, enter the full path to the `.ttf` file in the Font Family field (e.g., `C:/Windows/Fonts/calibri.ttf`).
- If a font cannot be loaded, QCert falls back to Helvetica and logs a warning.

### Preview not rendering (grey box)

The live preview converts the generated PDF to an image. This requires one of:
- **PyMuPDF** (`pip install PyMuPDF`) — recommended, pure Python.
- **pdf2image** + **Poppler** (`pip install pdf2image` and install Poppler binaries).

If neither is available, QCert shows a **placeholder preview** with element bounding boxes — the exported PDFs are still rendered correctly.

### Missing files

- If a background PDF or image file cannot be found, QCert warns and continues — the missing element is simply omitted from the output.
- Check that file paths in the layout JSON are correct (relative to the project root or absolute).

### Scaling / DPI drift

- All positions are specified in **millimetres** and converted to PDF points at exactly 1 mm = 2.8346 pt (72 pt/inch).
- The preview is a rasterised view for display only; the exported PDF is vector-accurate.
- If you notice slight differences between preview and export, they are display-only; the PDF output is authoritative.

### Illegal characters

- Text from the spreadsheet is automatically sanitised: null bytes and control characters are stripped.
- Filenames are sanitised to remove characters illegal on Windows (`< > : " / \ | ? *`).

## Requirements

- Python 3.9+
- openpyxl >= 3.1
- reportlab >= 4.0
- Pillow >= 10.0
- PyPDF2 >= 3.0
- (Optional) PyMuPDF or pdf2image for live preview rendering
- (Optional) PyInstaller >= 6.0 for standalone packaging

## License

This project is provided as-is for educational and internal use.
