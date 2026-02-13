#!/usr/bin/env python3
"""Generate example files for QCert: sample spreadsheet, background template PDF, and layout."""

import os
import json

# ------------------------------------------------------------------
# 1. Example spreadsheet (requires openpyxl)
# ------------------------------------------------------------------
def create_sample_spreadsheet():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Certificates"

    headers = ["Name", "ID", "Course", "Date", "Grade", "Instructor"]
    ws.append(headers)

    data = [
        ["Alice Johnson", "CERT-001", "Advanced Python Programming", "2025-06-15", "Distinction", "Dr. Smith"],
        ["Bob Williams", "CERT-002", "Data Science Fundamentals", "2025-06-15", "Merit", "Prof. Chen"],
        ["Carol Martinez", "CERT-003", "Web Development Bootcamp", "2025-07-01", "Pass", "Dr. Smith"],
        ["David Brown", "CERT-004", "Machine Learning Essentials", "2025-07-10", "Distinction", "Prof. Chen"],
        ["Eva Garcia", "CERT-005", "Cloud Architecture", "2025-07-10", "Merit", "Dr. Patel"],
        ["Frank Lee", "CERT-006", "Cybersecurity Basics", "2025-08-01", "Pass", "Prof. Adams"],
        ["Grace Kim", "CERT-007", "Advanced Python Programming", "2025-08-15", "Distinction", "Dr. Smith"],
        ["Henry Davis", "CERT-008", "Data Science Fundamentals", "2025-08-15", "Merit", "Prof. Chen"],
        ["Iris Wilson", "CERT-009", "Database Design", "2025-09-01", "Distinction", "Dr. Patel"],
        ["Jack Taylor", "CERT-010", "DevOps Engineering", "2025-09-15", "Pass", "Prof. Adams"],
    ]
    for row in data:
        ws.append(row)

    path = "examples/data/sample_students.xlsx"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)
    print(f"  Created {path}")


# ------------------------------------------------------------------
# 2. Example background template PDF (using reportlab)
# ------------------------------------------------------------------
def create_sample_template():
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    path = "examples/templates/certificate_bg.pdf"
    os.makedirs(os.path.dirname(path), exist_ok=True)

    ps = landscape(A4)
    w, h = ps
    c = canvas.Canvas(path, pagesize=ps)

    # Soft cream background
    c.setFillColor(HexColor("#FFF8F0"))
    c.rect(0, 0, w, h, fill=1, stroke=0)

    # Decorative border
    margin = 15 * mm
    c.setStrokeColor(HexColor("#8B7355"))
    c.setLineWidth(3)
    c.rect(margin, margin, w - 2 * margin, h - 2 * margin, fill=0, stroke=1)
    c.setLineWidth(1)
    c.rect(margin + 3 * mm, margin + 3 * mm,
           w - 2 * margin - 6 * mm, h - 2 * margin - 6 * mm, fill=0, stroke=1)

    # Inner decorative corners
    corner_size = 12 * mm
    for cx, cy in [
        (margin + 8 * mm, margin + 8 * mm),
        (w - margin - 8 * mm, margin + 8 * mm),
        (margin + 8 * mm, h - margin - 8 * mm),
        (w - margin - 8 * mm, h - margin - 8 * mm),
    ]:
        c.setStrokeColor(HexColor("#C4A35A"))
        c.setLineWidth(1.5)
        c.circle(cx, cy, 3 * mm, fill=0, stroke=1)

    # Title area
    c.setFillColor(HexColor("#2C3E50"))
    c.setFont("Times-Bold", 32)
    c.drawCentredString(w / 2, h - 55 * mm, "CERTIFICATE OF COMPLETION")

    # Decorative line under title
    c.setStrokeColor(HexColor("#C4A35A"))
    c.setLineWidth(2)
    line_w = 120 * mm
    c.line(w / 2 - line_w / 2, h - 60 * mm, w / 2 + line_w / 2, h - 60 * mm)

    # Preamble text
    c.setFillColor(HexColor("#555555"))
    c.setFont("Times-Roman", 14)
    c.drawCentredString(w / 2, h - 75 * mm, "This is to certify that")

    # Footer text
    c.setFont("Times-Italic", 10)
    c.setFillColor(HexColor("#888888"))
    c.drawCentredString(w / 2, margin + 12 * mm, "QCert Academy  |  www.example.com")

    c.showPage()
    c.save()
    print(f"  Created {path}")


# ------------------------------------------------------------------
# 3. Example layout profile
# ------------------------------------------------------------------
def create_sample_layout():
    layout = {
        "name": "Standard Certificate",
        "page_size": "A4",
        "orientation": "landscape",
        "template_pdf": "examples/templates/certificate_bg.pdf",
        "text_fields": [
            {
                "id": "txt_name",
                "source_column": "Name",
                "static_text": "",
                "x": 60.0, "y": 88.0, "width": 177.0,
                "alignment": "center",
                "font_family": "Times-Roman",
                "font_size": 28.0,
                "font_weight": "bold",
                "font_colour": "#1A237E",
                "line_spacing": 1.2,
                "wrap": False,
                "format_rule": "uppercase",
                "show_bbox": False,
            },
            {
                "id": "txt_preamble",
                "source_column": "",
                "static_text": "has successfully completed the course",
                "x": 60.0, "y": 102.0, "width": 177.0,
                "alignment": "center",
                "font_family": "Times-Roman",
                "font_size": 13.0,
                "font_weight": "normal",
                "font_colour": "#555555",
                "line_spacing": 1.2,
                "wrap": False,
                "format_rule": "",
                "show_bbox": False,
            },
            {
                "id": "txt_course",
                "source_column": "Course",
                "static_text": "",
                "x": 60.0, "y": 115.0, "width": 177.0,
                "alignment": "center",
                "font_family": "Times-Bold",
                "font_size": 20.0,
                "font_weight": "bold",
                "font_colour": "#2C3E50",
                "line_spacing": 1.2,
                "wrap": False,
                "format_rule": "titlecase",
                "show_bbox": False,
            },
            {
                "id": "txt_grade",
                "source_column": "Grade",
                "static_text": "",
                "x": 60.0, "y": 130.0, "width": 177.0,
                "alignment": "center",
                "font_family": "Times-Roman",
                "font_size": 14.0,
                "font_weight": "normal",
                "font_colour": "#8B7355",
                "line_spacing": 1.2,
                "wrap": False,
                "format_rule": "",
                "show_bbox": False,
            },
            {
                "id": "txt_date",
                "source_column": "Date",
                "static_text": "",
                "x": 55.0, "y": 160.0, "width": 80.0,
                "alignment": "center",
                "font_family": "Times-Roman",
                "font_size": 11.0,
                "font_weight": "normal",
                "font_colour": "#555555",
                "line_spacing": 1.2,
                "wrap": False,
                "format_rule": "DD MMM YYYY",
                "show_bbox": False,
            },
            {
                "id": "txt_date_label",
                "source_column": "",
                "static_text": "Date",
                "x": 55.0, "y": 168.0, "width": 80.0,
                "alignment": "center",
                "font_family": "Times-Roman",
                "font_size": 9.0,
                "font_weight": "normal",
                "font_colour": "#888888",
                "line_spacing": 1.2,
                "wrap": False,
                "format_rule": "",
                "show_bbox": False,
            },
            {
                "id": "txt_id",
                "source_column": "ID",
                "static_text": "",
                "x": 220.0, "y": 185.0, "width": 60.0,
                "alignment": "right",
                "font_family": "Courier",
                "font_size": 8.0,
                "font_weight": "normal",
                "font_colour": "#AAAAAA",
                "line_spacing": 1.0,
                "wrap": False,
                "format_rule": "",
                "show_bbox": False,
            },
            {
                "id": "txt_instructor",
                "source_column": "Instructor",
                "static_text": "",
                "x": 160.0, "y": 160.0, "width": 80.0,
                "alignment": "center",
                "font_family": "Times-Roman",
                "font_size": 11.0,
                "font_weight": "normal",
                "font_colour": "#555555",
                "line_spacing": 1.2,
                "wrap": False,
                "format_rule": "",
                "show_bbox": False,
            },
            {
                "id": "txt_instructor_label",
                "source_column": "",
                "static_text": "Instructor",
                "x": 160.0, "y": 168.0, "width": 80.0,
                "alignment": "center",
                "font_family": "Times-Roman",
                "font_size": 9.0,
                "font_weight": "normal",
                "font_colour": "#888888",
                "line_spacing": 1.2,
                "wrap": False,
                "format_rule": "",
                "show_bbox": False,
            },
        ],
        "image_layers": [],
        "output_name_template": "{Name}_{ID}",
        "output_mode": "individual",
        "version": 1,
        "created": "2025-01-01T00:00:00",
        "modified": "2025-01-01T00:00:00",
    }

    path = "layouts/standard_certificate.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=2)
    print(f"  Created {path}")


# ------------------------------------------------------------------
# 4. Example signature placeholder image
# ------------------------------------------------------------------
def create_sample_signature():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  Skipped signature image (Pillow not installed)")
        return

    img = Image.new("RGBA", (400, 150), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    # Draw a stylized signature line
    draw.line([(30, 110), (370, 110)], fill=(100, 100, 100, 200), width=2)

    # Draw cursive-ish text
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except OSError:
        font = ImageFont.load_default()
    draw.text((80, 50), "Dr. Smith", fill=(30, 30, 80, 220), font=font)

    path = "examples/signatures/dr_smith.png"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    print(f"  Created {path}")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
if __name__ == "__main__":
    print("Creating QCert example files…")
    create_sample_spreadsheet()
    create_sample_template()
    create_sample_layout()
    create_sample_signature()
    print("Done.")
