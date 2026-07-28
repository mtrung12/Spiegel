"""
Render testdata/asseco-campaign-brief.md into a PDF for upload testing.

Usage:
    pip install reportlab
    python testdata/make_pdf.py
"""

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).parent
SOURCE = HERE / "asseco-campaign-brief.md"
TARGET = HERE / "asseco-campaign-brief.pdf"

ACCENT = colors.HexColor("#C8102E")   # Asseco red
INK = colors.HexColor("#1A1A1A")
MUTED = colors.HexColor("#666666")
RULE = colors.HexColor("#DDDDDD")


def build_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=22, leading=27, textColor=INK, spaceAfter=4, alignment=0,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=13, leading=17, textColor=ACCENT, spaceBefore=14, spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=INK, spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"], fontName="Helvetica",
            fontSize=9.5, leading=14, textColor=INK, spaceAfter=6, alignment=TA_JUSTIFY,
        ),
        "meta": ParagraphStyle(
            "meta", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.5, leading=12, textColor=MUTED, spaceAfter=4,
        ),
        "quote": ParagraphStyle(
            "quote", parent=base["BodyText"], fontName="Helvetica-Oblique",
            fontSize=9.5, leading=14, textColor=INK,
            leftIndent=10, borderPadding=0, spaceBefore=4, spaceAfter=8,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base["BodyText"], fontName="Helvetica",
            fontSize=9.5, leading=13.5, textColor=INK,
            leftIndent=12, bulletIndent=3, spaceAfter=3,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.5, leading=11.5, textColor=INK, spaceAfter=0,
        ),
        "cellhead": ParagraphStyle(
            "cellhead", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=8.5, leading=11.5, textColor=colors.white, spaceAfter=0,
        ),
    }


# The built-in Helvetica is Latin-1, so typographic punctuation would extract as
# replacement characters. Fold it to ASCII before it reaches the canvas.
PUNCTUATION = {
    "—": " - ", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "·": "-",
    "→": "->", "≥": ">=", "≤": "<=",
}


def inline(text):
    """Markdown emphasis -> reportlab inline markup."""
    for src, dst in PUNCTUATION.items():
        text = text.replace(src, dst)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", text)
    return text


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def make_table(rows, styles, width):
    header, *body = rows
    data = [[Paragraph(inline(c), styles["cellhead"]) for c in header]]
    data += [[Paragraph(inline(c), styles["cell"]) for c in r] for r in body]

    cols = len(header)
    # First column carries the labels, so give it more room.
    if cols == 2:
        widths = [width * 0.42, width * 0.58]
    elif cols == 3:
        widths = [width * 0.30, width * 0.42, width * 0.28]
    else:
        widths = [width / cols] * cols

    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F7F7")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("BOX", (0, 0), (-1, -1), 0.4, RULE),
    ]))
    return table


def parse(md, styles, width):
    story = []
    lines = md.split("\n")
    i = 0
    para: list[str] = []
    quote: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            story.append(Paragraph(inline(" ".join(para)), styles["body"]))
            para = []

    def flush_quote():
        nonlocal quote
        if quote:
            story.append(HRFlowable(width="100%", thickness=0.4, color=RULE,
                                    spaceBefore=2, spaceAfter=5))
            story.append(Paragraph(inline(" ".join(quote)), styles["quote"]))
            story.append(HRFlowable(width="100%", thickness=0.4, color=RULE,
                                    spaceBefore=0, spaceAfter=7))
            quote = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Table: a header row followed by a |---|---| separator
        if (stripped.startswith("|") and i + 1 < len(lines)
                and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip())):
            flush_para()
            flush_quote()
            rows = [split_row(stripped)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            story.append(Spacer(1, 3))
            story.append(make_table(rows, styles, width))
            story.append(Spacer(1, 8))
            continue

        if not stripped:
            flush_para()
            flush_quote()
        elif stripped.startswith("> "):
            flush_para()
            quote.append(stripped[2:])
        elif stripped == "---":
            flush_para()
            flush_quote()
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.7, color=RULE,
                                    spaceBefore=2, spaceAfter=8))
        elif stripped.startswith("### "):
            flush_para()
            flush_quote()
            story.append(Paragraph(inline(stripped[4:]), styles["h3"]))
        elif stripped.startswith("## "):
            flush_para()
            flush_quote()
            story.append(Paragraph(inline(stripped[3:]), styles["h2"]))
        elif stripped.startswith("# "):
            flush_para()
            flush_quote()
            story.append(Paragraph(inline(stripped[2:]), styles["title"]))
        elif re.match(r"^[-*] ", stripped) or re.match(r"^\d+\. ", stripped):
            flush_para()
            flush_quote()
            if re.match(r"^[-*] ", stripped):
                bullet, text = "•", stripped[2:]
            else:
                num, text = stripped.split(". ", 1)
                bullet = f"{num}."
            # A wrapped list item continues on the next indented line; fold it in
            # so the continuation keeps the bullet's indent.
            while (i + 1 < len(lines) and lines[i + 1].startswith((" ", "\t"))
                   and lines[i + 1].strip()
                   and not re.match(r"^[-*] |^\d+\. ", lines[i + 1].strip())):
                i += 1
                text += " " + lines[i].strip()
            story.append(Paragraph(inline(text), styles["bullet"],
                                   bulletText=bullet))
        elif stripped.startswith("*") and stripped.endswith("*") and len(story) > 5:
            # Trailing italic footer block
            flush_para()
            story.append(Paragraph(inline(stripped), styles["meta"]))
        else:
            quote and flush_quote()
            para.append(stripped)

        i += 1

    flush_para()
    flush_quote()
    return story


def decorate(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 10.5 * mm, "Asseco Poland S.A. - Campaign ACP-BOOX-2026")
    canvas.drawRightString(A4[0] - 20 * mm, 10.5 * mm, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def main():
    styles = build_styles()
    doc = SimpleDocTemplate(
        str(TARGET), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=20 * mm,
        title="Asseco BooX - Campaign Brief and Communication Policy",
        author="Asseco Poland S.A.",
        subject="Marketing campaign brief ACP-BOOX-2026",
    )
    width = doc.width
    story = parse(SOURCE.read_text(encoding="utf-8"), styles, width)
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    print(f"wrote {TARGET} ({TARGET.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
