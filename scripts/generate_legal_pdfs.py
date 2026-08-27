"""Generate immutable PDF copies of the registered legal DOCX editions.

Run this only when adding a new published legal version. Existing PDF files are
release artifacts and must not be regenerated after publication.
"""
from __future__ import annotations

from html import escape
import logging
import os
from pathlib import Path
import sys

from reportlab.lib.colors import black
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from legal_documents import LEGAL_DOCUMENTS


LOGGER = logging.getLogger("spyon.legal_pdf")
FONT_NAME = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


def register_fonts() -> None:
    """Use an installed Unicode font when available, without requiring one OS."""
    global FONT_NAME, FONT_BOLD
    candidates = [
        (Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "times.ttf",
         Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "timesbd.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")),
        (Path("/Library/Fonts/Times New Roman.ttf"),
         Path("/Library/Fonts/Times New Roman Bold.ttf")),
    ]
    for normal_path, bold_path in candidates:
        if not (normal_path.is_file() and bold_path.is_file()):
            continue
        try:
            normal_name = "SpyonLegalNormal"
            bold_name = "SpyonLegalBold"
            pdfmetrics.registerFont(TTFont(normal_name, str(normal_path)))
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
            pdfmetrics.registerFontFamily(normal_name, normal=normal_name, bold=bold_name)
            FONT_NAME, FONT_BOLD = normal_name, bold_name
            return
        except Exception as exc:  # An optional asset must not block publication.
            LOGGER.warning("Legal PDF font %s is unavailable: %s", normal_path, exc)
    LOGGER.warning("No Unicode serif font was found; using ReportLab fallback fonts.")


def footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont(FONT_NAME, 8)
    canvas.setFillColor(black)
    canvas.drawString(20 * mm, 14 * mm, "Spyon · юридический документ")
    canvas.drawRightString(A4[0] - 20 * mm, 14 * mm, f"Страница {document.page}")
    canvas.restoreState()


def build(definition) -> None:
    definition.pdf_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("LegalTitle", parent=styles["Title"], fontName=FONT_BOLD, fontSize=14, leading=18, alignment=TA_CENTER, textColor=black, spaceAfter=10)
    meta = ParagraphStyle("LegalMeta", parent=styles["Normal"], fontName=FONT_NAME, fontSize=10, leading=14, alignment=TA_CENTER, textColor=black, spaceAfter=16)
    heading = ParagraphStyle("LegalHeading", parent=styles["Heading2"], fontName=FONT_BOLD, fontSize=11, leading=14, textColor=black, spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("LegalBody", parent=styles["BodyText"], fontName=FONT_NAME, fontSize=10, leading=14, alignment=TA_LEFT, textColor=black, spaceAfter=7)
    story = [
        Paragraph(escape(definition.title), title),
        Paragraph(f"№ {escape(definition.number)} · Версия {escape(definition.version)}", meta),
    ]
    for block in LEGAL_DOCUMENTS.blocks(definition):
        if block["kind"] == "heading":
            story.append(Paragraph(escape(block["text"]), heading))
        elif block["kind"] == "table":
            data = [[Paragraph(escape(str(cell)), body) for cell in row] for row in block["rows"]]
            table = Table(data, repeatRows=0, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.extend([table, Spacer(1, 8)])
        else:
            prefix = "• " if block["kind"] == "list" else ""
            story.append(Paragraph(escape(prefix + block["text"]).replace("\n", "<br/>"), body))
    pdf = SimpleDocTemplate(
        str(definition.pdf_path), pagesize=A4,
        rightMargin=20 * mm, leftMargin=20 * mm, topMargin=20 * mm, bottomMargin=22 * mm,
        title=definition.title, author="Spyon",
    )
    pdf.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    register_fonts()
    for item in LEGAL_DOCUMENTS.current_documents():
        build(item)
