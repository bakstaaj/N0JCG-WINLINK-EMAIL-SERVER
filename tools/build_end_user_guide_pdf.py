"""Build the branded guide PDF when LibreOffice is unavailable."""
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, KeepTogether, Image,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "N0JCG_Winlink_Email_Server_End_User_Guide.md"
OUTPUT = ROOT / "docs" / "N0JCG_Winlink_Email_Server_End_User_Guide.pdf"
NAVY = colors.HexColor("#0A1F44")
BLUE = colors.HexColor("#1565C0")
CYAN = colors.HexColor("#00B8D9")
SLATE = colors.HexColor("#536171")


def inline(value):
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"`(.+?)`", r"<font name='Courier'>\1</font>", value)
    return value


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(CYAN)
    canvas.setLineWidth(1)
    canvas.line(0.78 * inch, 0.55 * inch, 7.72 * inch, 0.55 * inch)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(SLATE)
    canvas.drawString(0.78 * inch, 0.36 * inch, "N0JCG Open Radio Platform  |  N0JCG Winlink Email Server v0.1.3")
    canvas.drawRightString(7.72 * inch, 0.36 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("CoverTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=25, leading=30, textColor=NAVY, alignment=TA_CENTER, spaceAfter=12))
    styles.add(ParagraphStyle("CoverSub", parent=styles["Normal"], fontSize=13, leading=18, textColor=SLATE, alignment=TA_CENTER, spaceAfter=18))
    styles.add(ParagraphStyle("H1N0", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=NAVY, spaceBefore=12, spaceAfter=7))
    styles.add(ParagraphStyle("H2N0", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=BLUE, spaceBefore=9, spaceAfter=4))
    styles.add(ParagraphStyle("BodyN0", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=14, textColor=NAVY, spaceAfter=6))
    styles.add(ParagraphStyle("BulletN0", parent=styles["BodyN0"], leftIndent=14, firstLineIndent=-8, bulletIndent=0))
    styles.add(ParagraphStyle("CodeN0", parent=styles["BodyN0"], fontName="Courier", fontSize=8.5, leading=12, textColor=colors.white, backColor=NAVY, borderPadding=6, spaceBefore=3, spaceAfter=7))
    doc = BaseDocTemplate(str(OUTPUT), pagesize=letter, leftMargin=0.78*inch, rightMargin=0.78*inch, topMargin=0.72*inch, bottomMargin=0.78*inch)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="n0jcg", frames=frame, onPage=footer)])
    story = []
    story.append(Table([["N0JCG OPEN RADIO PLATFORM"]], colWidths=[doc.width], style=TableStyle([("BACKGROUND", (0,0), (-1,-1), NAVY), ("TEXTCOLOR", (0,0), (-1,-1), colors.white), ("FONTNAME", (0,0), (-1,-1), "Helvetica-Bold"), ("FONTSIZE", (0,0), (-1,-1), 16), ("ALIGN", (0,0), (-1,-1), "CENTER"), ("TOPPADDING", (0,0), (-1,-1), 18), ("BOTTOMPADDING", (0,0), (-1,-1), 18)])))
    story += [Spacer(1, 0.35*inch), Paragraph("OPERATOR HANDBOOK", ParagraphStyle("CoverLabel", parent=styles["CoverSub"], fontName="Helvetica-Bold", fontSize=10, textColor=CYAN)), Paragraph("N0JCG Winlink Email Server", styles["CoverTitle"]), Paragraph("Installation, client setup, webmail operation, and troubleshooting", styles["CoverSub"]), Paragraph("Release 0.1.3  |  August 2026", ParagraphStyle("CoverMeta", parent=styles["BodyN0"], alignment=TA_CENTER, textColor=SLATE)), PageBreak()]
    story.append(Paragraph("Interface reference", styles["H1N0"]))
    story.append(Paragraph("Current N0JCG-branded operator and webmail views used throughout this guide.", styles["BodyN0"]))
    screenshot_specs = [
        (ROOT / "docs" / "assets" / "operator-status.png", "Operator status and transmit safety state."),
        (ROOT / "docs" / "assets" / "operator-diagnostics.png", "Operator diagnostics and safe next step."),
        (ROOT / "docs" / "assets" / "webmail-inbox.png", "Webmail inbox and mailbox status."),
        (ROOT / "docs" / "assets" / "mailbox-progress.png", "Mailbox synchronization progress."),
        (ROOT / "docs" / "assets" / "compose-message.png", "Compose and message controls."),
    ]
    for image_path, caption in screenshot_specs:
        if image_path.exists():
            image = Image(str(image_path))
            image._restrictSize(doc.width, 4.85 * inch)
            story.append(image)
            story.append(Paragraph(caption, ParagraphStyle("ScreenshotCaption", parent=styles["BodyN0"], fontSize=8.5, leading=11, textColor=SLATE, alignment=TA_CENTER, spaceAfter=10)))
    story.append(PageBreak())
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    in_code = False
    table_rows = []
    def flush_table():
        nonlocal table_rows
        if not table_rows:
            return
        rows = [[Paragraph(inline(c.strip()), styles["BodyN0"]) for c in row] for row in table_rows if not all(set(c.strip()) <= set("-|:") for c in row)]
        if rows:
            widths = [doc.width / len(rows[0])] * len(rows[0])
            t = Table(rows, colWidths=widths, repeatRows=1)
            t.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#C6D0DB")), ("VALIGN", (0,0), (-1,-1), "TOP"), ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F4F7FA")]), ("LEFTPADDING", (0,0), (-1,-1), 7), ("RIGHTPADDING", (0,0), (-1,-1), 7), ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
            story.append(t); story.append(Spacer(1, 6))
        table_rows = []
    for line in lines:
        if line.startswith("```"):
            flush_table(); in_code = not in_code; continue
        if in_code:
            story.append(Paragraph(inline(line) or " ", styles["CodeN0"])); continue
        if line.startswith("|"):
            table_rows.append([c for c in line.strip("|").split("|")]); continue
        flush_table()
        if not line.strip():
            continue
        if line.startswith("# "):
            story.append(Paragraph(inline(line[2:]), styles["H1N0"])); continue
        if line.startswith("## "):
            story.append(Paragraph(inline(line[3:]), styles["H1N0"])); continue
        if line.startswith("### "):
            story.append(Paragraph(inline(line[4:]), styles["H2N0"])); continue
        if line.startswith("- "):
            story.append(Paragraph(inline(line[2:]), styles["BulletN0"], bulletText="•")); continue
        if line.startswith("*") and line.endswith("*"):
            story.append(Paragraph(inline(line), ParagraphStyle("Foot", parent=styles["BodyN0"], fontSize=8, textColor=SLATE, alignment=TA_CENTER))); continue
        story.append(Paragraph(inline(line), styles["BodyN0"]))
    flush_table()
    doc.build(story)
    print(OUTPUT)


if __name__ == "__main__":
    build()
