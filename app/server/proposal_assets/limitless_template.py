"""
Commercial proposal PDF template for Limitless Mechanical Services, Inc.
-- original design system (v2, brand-matched) --

A technical/industrial visual identity built for a mechanical contractor,
keyed off the company's actual logo (near-black + bright sky-blue,
interlocking-circle mark): charcoal/near-black ink, a two-weight blue
accent (bright for dark backgrounds, a deeper shade for on-white text and
lines), condensed display type for headlines, an indexed section system
(01 / 02 / 03...), open-line tables, and stat-card pricing instead of a
boxed corporate letterhead look.

Usage:
    from proposal_template_v2 import build_proposal_pdf
    build_proposal_pdf(data, "output.pdf")

`data` uses the same shape as before -- see ESTILL_DATA at the bottom.
Swap in a new dict for the next job and re-run to generate that proposal.

Requires `limitless_logo.png` (the cropped wordmark/circles logo) to sit
in the same folder as this script -- it is drawn into the cover banner.
"""

import os

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, NextPageTemplate,
    Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable, KeepTogether, KeepInFrame,
)
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pathlib import Path
from reportlab.lib.utils import ImageReader

# ---------------------------------------------------------------------------
# Type system -- DejaVu Sans Condensed for display/labels, DejaVu Sans for
# reading text. Registered once at import time.
# ---------------------------------------------------------------------------
def _first_font(*paths):
    for path in paths:
        if Path(path).exists():
            return path
    raise FileNotFoundError(f"No supported proposal font found: {paths}")

pdfmetrics.registerFont(TTFont("Cond", _first_font(
    str(Path(__file__).parent / "fonts" / "DejaVuSansCondensed.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf", "C:/Windows/Fonts/arial.ttf")))
pdfmetrics.registerFont(TTFont("Cond-Bold", _first_font(
    str(Path(__file__).parent / "fonts" / "DejaVuSansCondensed-Bold.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf")))
pdfmetrics.registerFont(TTFont("Body", _first_font(
    str(Path(__file__).parent / "fonts" / "DejaVuSans.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf")))
pdfmetrics.registerFont(TTFont("Body-Bold", _first_font(
    str(Path(__file__).parent / "fonts" / "DejaVuSans-Bold.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf")))

# ---------------------------------------------------------------------------
# Logo asset -- the company's own mark, cropped tight with its native
# near-black background so it drops into the dark banner with no seam.
# ---------------------------------------------------------------------------
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "limitless_logo.png")
_LOGO_PX_W, _LOGO_PX_H = 900, 308  # native pixel size of limitless_logo.png
LOGO_ASPECT = _LOGO_PX_W / _LOGO_PX_H

# ---------------------------------------------------------------------------
# Palette -- sampled directly from the Limitless logo: near-black ink and a
# bright sky-blue, split into two weights (bright for dark surfaces, a
# deeper shade for legible on-white text/lines/rules).
# ---------------------------------------------------------------------------
INK = colors.HexColor("#0B0D0F")
INK_SOFT = colors.HexColor("#3A4149")
SLATE = colors.HexColor("#6B7480")
ACCENT_BRIGHT = colors.HexColor("#6CCEF7")   # true logo blue -- dark surfaces, large numerals
ACCENT = colors.HexColor("#0B84B8")          # deepened for contrast on white -- text, rules, lines
ACCENT_DEEP = colors.HexColor("#086992")
ACCENT_TINT = colors.HexColor("#E6F5FC")
LINE = colors.HexColor("#DBDFE2")
SURFACE = colors.HexColor("#F4F5F6")
WHITE = colors.white

PAGE_W, PAGE_H = letter
M = 0.72 * inch
BANNER_H = 1.72 * inch
COVER_TOP = BANNER_H + 0.55 * inch
INTERIOR_TOP = 0.78 * inch
BOTTOM = 0.75 * inch

# ---------------------------------------------------------------------------
# Small helper: fake tracking/letterspacing for short uppercase labels by
# joining characters with a thin space. Used sparingly (eyebrows, stat
# labels) -- never on long text.
# ---------------------------------------------------------------------------
def tracked(text, letter_gap="\u00A0", word_gap="\u00A0\u00A0\u00A0"):
    # NBSP is used because reportlab's paragraph XML parser collapses runs
    # of regular whitespace (like HTML) -- NBSP survives, giving real tracking.
    words = text.split(" ")
    return word_gap.join(letter_gap.join(list(w)) for w in words)


# ---------------------------------------------------------------------------
# Paragraph styles
# ---------------------------------------------------------------------------
style_body = ParagraphStyle(
    "body", fontName="Body", fontSize=9.6, leading=14.5, textColor=INK_SOFT,
)
style_body_tight = ParagraphStyle(
    "body_tight", parent=style_body, spaceAfter=0,
)
style_eyebrow = ParagraphStyle(
    "eyebrow", fontName="Cond-Bold", fontSize=9.5, leading=12, textColor=ACCENT,
)
style_h1_cover = ParagraphStyle(
    "h1_cover", fontName="Cond-Bold", fontSize=30, leading=32, textColor=INK,
)
style_subtitle_cover = ParagraphStyle(
    "subtitle_cover", fontName="Body", fontSize=12.5, leading=16, textColor=SLATE,
)
style_meta_label = ParagraphStyle(
    "meta_label", fontName="Cond-Bold", fontSize=8, leading=10, textColor=ACCENT,
)
style_meta_value = ParagraphStyle(
    "meta_value", fontName="Body", fontSize=10, leading=13.5, textColor=INK,
)
style_meta_value_bold = ParagraphStyle(
    "meta_value_bold", parent=style_meta_value, fontName="Body-Bold",
)
style_section_index = ParagraphStyle(
    "section_index", fontName="Cond-Bold", fontSize=22, leading=22, textColor=LINE,
)
style_section_title = ParagraphStyle(
    "section_title", fontName="Cond-Bold", fontSize=17, leading=20, textColor=INK,
)
style_table_header = ParagraphStyle(
    "table_header", fontName="Cond-Bold", fontSize=8.7, leading=11, textColor=ACCENT,
)
style_table_cell_bold = ParagraphStyle(
    "table_cell_bold", fontName="Body-Bold", fontSize=9.6, leading=13.5, textColor=INK,
)
style_table_cell = ParagraphStyle(
    "table_cell", fontName="Body", fontSize=9.6, leading=13.5, textColor=INK_SOFT,
)
style_list_label = ParagraphStyle(
    "list_label", fontName="Body-Bold", fontSize=9.6, leading=14.5, textColor=INK,
)
style_note = ParagraphStyle(
    "note", fontName="Body", fontSize=9, leading=13, textColor=INK_SOFT,
)
style_note_strong = ParagraphStyle(
    "note_strong", parent=style_note, fontName="Body-Bold", textColor=INK,
)
style_stat_label = ParagraphStyle(
    "stat_label", fontName="Cond-Bold", fontSize=8.3, leading=11, textColor=SLATE,
)
style_stat_label_on_dark = ParagraphStyle(
    "stat_label_on_dark", parent=style_stat_label, textColor=colors.HexColor("#9FDDF9"),
)
style_stat_value = ParagraphStyle(
    "stat_value", fontName="Cond-Bold", fontSize=20, leading=22, textColor=INK,
)
style_stat_value_on_dark = ParagraphStyle(
    "stat_value_on_dark", parent=style_stat_value, textColor=WHITE, fontSize=26, leading=28,
)
style_form_label = ParagraphStyle(
    "form_label", fontName="Cond-Bold", fontSize=8, leading=10, textColor=SLATE,
)
style_closer_name = ParagraphStyle(
    "closer_name", fontName="Cond-Bold", fontSize=12.5, leading=15, textColor=INK,
)
style_thankyou = ParagraphStyle(
    "thankyou", fontName="Body-Bold", fontSize=10, leading=15, textColor=INK,
)


# ---------------------------------------------------------------------------
# Reusable component builders
# ---------------------------------------------------------------------------
def content_width():
    return PAGE_W - 2 * M


def section_header(index, title):
    t = Table(
        [[Paragraph(f"{index:02d}", style_section_index), Paragraph(title.upper(), style_section_title)]],
        colWidths=[0.5 * inch, content_width() - 0.5 * inch],
    )
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [
        Spacer(1, 20),
        t,
        HRFlowable(width="100%", thickness=1.3, color=ACCENT, spaceBefore=2, spaceAfter=12, hAlign="LEFT"),
    ]


def meta_strip(pairs):
    """pairs: list of ((label, value), (label, value)) two-column rows."""
    data = []
    for (l1, v1), (l2, v2) in pairs:
        data.append([Paragraph(tracked(l1), style_meta_label), Paragraph(tracked(l2), style_meta_label)])
        data.append([Paragraph(v1, style_meta_value_bold if l1 == "PREPARED FOR" else style_meta_value),
                     Paragraph(v2, style_meta_value)])
    t = Table(data, colWidths=[content_width() / 2] * 2)
    cmds = [
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, -1), 22),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for r in range(0, len(data), 2):
        cmds.append(("TOPPADDING", (0, r), (-1, r), 12))
        cmds.append(("BOTTOMPADDING", (0, r + 1), (-1, r + 1), 10))
        cmds.append(("LINEBELOW", (0, r + 1), (-1, r + 1), 0.75, LINE))
    t.setStyle(TableStyle(cmds))
    return t


def hero_price(label, price, description):
    left = [
        Paragraph(tracked(label), style_eyebrow),
        Spacer(1, 6),
        Paragraph(description, style_body),
    ]
    right = [
        Paragraph(tracked("INVESTMENT"), style_stat_label_on_dark),
        Spacer(1, 4),
        Paragraph(price, style_stat_value_on_dark),
    ]
    t = Table([[left, right]], colWidths=[content_width() * 0.60, content_width() * 0.40])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (1, 0), (1, 0), INK),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 24),
        ("LEFTPADDING", (1, 0), (1, 0), 22),
        ("RIGHTPADDING", (1, 0), (1, 0), 22),
        ("TOPPADDING", (1, 0), (1, 0), 20),
        ("BOTTOMPADDING", (1, 0), (1, 0), 20),
        ("LINEABOVE", (1, 0), (1, 0), 2.5, ACCENT_BRIGHT),
    ]))
    return t


def note_bar(label, text):
    inner = Paragraph(f'<b>{label}</b>&nbsp;&nbsp;{text}', style_note)
    t = Table([[Paragraph("", style_note), inner]], colWidths=[0.09 * inch, content_width() - 0.09 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), ACCENT),
        ("BACKGROUND", (1, 0), (1, 0), ACCENT_TINT),
        ("LEFTPADDING", (1, 0), (1, 0), 14),
        ("RIGHTPADDING", (1, 0), (1, 0), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def equipment_table(rows):
    header = [Paragraph(tracked("COMPONENT"), style_table_header),
              Paragraph(tracked("PROPOSED EQUIPMENT / PERFORMANCE"), style_table_header)]
    data = [header]
    for comp, desc in rows:
        data.append([Paragraph(comp, style_table_cell_bold), Paragraph(desc, style_table_cell)])
    t = Table(data, colWidths=[1.65 * inch, content_width() - 1.65 * inch], repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 1), (-1, -1), 7.5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 7.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0, ACCENT),
    ]
    for i in range(1, len(data)):
        cmds.append(("LINEBELOW", (0, i), (-1, i), 0.75, LINE))
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), SURFACE))
    t.setStyle(TableStyle(cmds))
    return t


def dash_list(items):
    flow = []
    for label, desc in items:
        if label:
            txt = f'<font face="Body-Bold" color="#181C20">{label}</font> {desc}'
        else:
            txt = desc
        row = Table([[Paragraph("—", style_list_label), Paragraph(txt, style_body)]],
                     colWidths=[0.22 * inch, content_width() - 0.22 * inch])
        row.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TEXTCOLOR", (0, 0), (0, 0), ACCENT),
        ]))
        flow.append(row)
        flow.append(Spacer(1, 7))
    return flow


def payment_cards(total, deposit, balance, deposit_label="DEPOSIT", balance_label="BALANCE"):
    col_w = content_width() / 3
    total_cell = [
        Paragraph(tracked("TOTAL PROJECT PRICE"), style_stat_label_on_dark),
        Spacer(1, 5),
        Paragraph(total, ParagraphStyle("tv", parent=style_stat_value_on_dark, fontSize=19, leading=21)),
    ]
    deposit_cell = [
        Paragraph(tracked(deposit_label), style_stat_label),
        Spacer(1, 5),
        Paragraph(deposit, ParagraphStyle("dv", parent=style_stat_value, fontSize=19, leading=21)),
    ]
    balance_cell = [
        Paragraph(tracked(balance_label), style_stat_label),
        Spacer(1, 5),
        Paragraph(balance, ParagraphStyle("bv", parent=style_stat_value, fontSize=19, leading=21)),
    ]
    t = Table([[total_cell, deposit_cell, balance_cell]], colWidths=[col_w] * 3)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), INK),
        ("LINEABOVE", (0, 0), (0, 0), 2.5, ACCENT_BRIGHT),
        ("LINEABOVE", (1, 0), (1, 0), 2.5, LINE),
        ("LINEABOVE", (2, 0), (2, 0), 2.5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LEFTPADDING", (0, 0), (0, 0), 16),
        ("RIGHTPADDING", (0, 0), (0, 0), 12),
        ("LEFTPADDING", (1, 0), (-1, 0), 16),
        ("RIGHTPADDING", (1, 0), (-1, 0), 12),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def signature_form(fields_row1, fields_row2):
    """Each field is (label, colspan_weight). Builds two rows of underline
    fields in a signature-form style: blank line to sign, label beneath."""
    def field_flow(label):
        return [
            Spacer(1, 22),
            HRFlowable(width="100%", thickness=0.75, color=SLATE, spaceAfter=4),
            Paragraph(tracked(label), style_form_label),
        ]

    def build_row(fields):
        widths = [content_width() * w for _, w in fields]
        cells = [field_flow(label) for label, _ in fields]
        t = Table([cells], colWidths=widths)
        t.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 26),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ]))
        return t

    return [build_row(fields_row1), Spacer(1, 4), build_row(fields_row2)]


# ---------------------------------------------------------------------------
# Canvas subclass: draws the cover banner, running header, and a footer
# with true "Page X of N" (requires a two-pass save, standard reportlab
# pattern for a total-page-count footer).
# ---------------------------------------------------------------------------
def make_canvas_class(company):
    class ProposalCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_states = []

        def showPage(self):
            self._saved_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_states)
            for state in self._saved_states:
                self.__dict__.update(state)
                self._draw_furniture(total)
                super().showPage()
            super().save()

        def _draw_furniture(self, total_pages):
            page_num = self.getPageNumber()
            if page_num == 1:
                self._draw_cover_banner()
            else:
                self._draw_running_header()
            self._draw_footer(page_num, total_pages)

        def _draw_cover_banner(self):
            c = self
            c.saveState()
            c.setFillColor(INK)
            c.rect(0, PAGE_H - BANNER_H, PAGE_W, BANNER_H, stroke=0, fill=1)

            # The logo carries its own near-black background (matched to INK
            # above) so it drops into the banner with no visible seam.
            logo_h = 0.60 * inch
            logo_w = logo_h * LOGO_ASPECT
            logo_top = PAGE_H - 0.28 * inch
            if os.path.exists(LOGO_PATH):
                c.drawImage(ImageReader(LOGO_PATH), M, logo_top - logo_h,
                            width=logo_w, height=logo_h, mask="auto", preserveAspectRatio=True)
            else:
                c.setFillColor(WHITE)
                c.setFont("Cond-Bold", 30)
                c.drawString(M, logo_top - logo_h * 0.62, company["name"])

            c.setFont("Cond-Bold", 11)
            c.setFillColor(ACCENT_BRIGHT)
            tagline_y = logo_top - logo_h - 0.19 * inch
            c.drawString(M, tagline_y, tracked(company["tagline"]))

            c.setFont("Cond-Bold", 7.8)
            c.setFillColor(colors.HexColor("#9AA4AC"))
            license_y = tagline_y - 0.20 * inch
            address_y = license_y - 0.155 * inch
            c.drawString(M, license_y, tracked(company["license"]))
            c.drawString(M, address_y, tracked(company["footer_left"]))

            c.setStrokeColor(ACCENT_BRIGHT)
            c.setLineWidth(2)
            c.line(0, PAGE_H - BANNER_H, PAGE_W, PAGE_H - BANNER_H)
            c.restoreState()

        def _draw_running_header(self):
            c = self
            c.saveState()
            c.setFont("Cond-Bold", 8.3)
            c.setFillColor(SLATE)
            c.drawString(M, PAGE_H - 0.5 * inch, tracked(company["name_line"]))
            c.setStrokeColor(LINE)
            c.setLineWidth(0.75)
            c.line(M, PAGE_H - 0.58 * inch, PAGE_W - M, PAGE_H - 0.58 * inch)
            c.restoreState()

        def _draw_footer(self, page_num, total_pages):
            c = self
            c.saveState()
            c.setStrokeColor(ACCENT)
            c.setLineWidth(1.3)
            c.line(M, 0.62 * inch, PAGE_W - M, 0.62 * inch)
            c.setFont("Cond-Bold", 7.6)
            c.setFillColor(SLATE)
            c.drawString(M, 0.46 * inch, tracked(company["footer_left"]))
            c.drawRightString(PAGE_W - M, 0.46 * inch, tracked(f"PAGE {page_num} OF {total_pages}"))
            c.restoreState()

    return ProposalCanvas


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------
def build_proposal_pdf(data, output_path):
    company = data["company"]

    doc = BaseDocTemplate(
        output_path, pagesize=letter,
        title=f'{data["project_title"]} - Proposal', author=company["prepared_by"],
    )
    frame_cover = Frame(M, BOTTOM, content_width(), PAGE_H - COVER_TOP - BOTTOM, id="cover")
    frame_interior = Frame(M, BOTTOM, content_width(), PAGE_H - INTERIOR_TOP - BOTTOM, id="interior")
    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[frame_cover]),
        PageTemplate(id="Interior", frames=[frame_interior]),
    ])

    story = []

    # ---- Cover ---------------------------------------------------------
    story.append(Paragraph(tracked("FORMAL PROJECT PROPOSAL"), style_eyebrow))
    story.append(Spacer(1, 6))
    story.append(Paragraph(data["project_title"], ParagraphStyle("cover_title", parent=style_h1_cover, fontSize=18 if len(data["project_title"])>100 else 30, leading=22 if len(data["project_title"])>100 else 32)))
    story.append(Spacer(1, 3))
    story.append(Paragraph(data["project_subtitle"], style_subtitle_cover))
    story.append(Spacer(1, 18))

    story.append(meta_strip([
        (("PREPARED FOR", data["prepared_for"]), ("PREPARED BY", company["prepared_by"])),
        (("PROJECT SITE", data["project_site"]), ("COMPANY", company["name_full"])),
        (("CLIENT PHONE", data["client_phone"]), ("CONTACT", company["contact"])),
        (("PROPOSAL DATE", data["proposal_date"]), ("LICENSE", company["license"])),
    ]))
    story.append(Spacer(1, 20))
    story.append(hero_price("SCOPE SUMMARY", data["total_price"], data["price_description"]))
    story.append(Spacer(1, 14))
    story.append(note_bar(data.get("validity_label", "Proposal validity"), data["validity_text"]))

    story = [KeepInFrame(content_width() - 12, PAGE_H - COVER_TOP - BOTTOM - 12, story, mode="shrink")]
    story.append(NextPageTemplate("Interior"))
    story.append(PageBreak())

    # ---- 01 Project Understanding + Equipment + Scope -------------------
    story.extend(section_header(1, "Project Understanding"))
    story.append(Paragraph(data["project_understanding"], style_body))

    story.extend(section_header(2, "Equipment Schedule"))
    story.append(equipment_table(data["equipment_schedule"]))

    story.extend(section_header(3, "Included Scope of Work"))
    story.extend(dash_list(data["scope_of_work"]))

    # ---- Warranty + Exclusions ------------------------------------------
    story.extend(section_header(4, "Warranty"))
    story.append(Paragraph(data["warranty_text"], style_body))
    if data.get("warranty_note"):
        story.append(Spacer(1, 10))
        story.append(note_bar("Warranty service note", data["warranty_note"]))

    story.extend(section_header(5, "Exclusions & Clarifications"))
    story.extend(dash_list([("", item) for item in data["exclusions"]]))

    # ---- Investment + Acceptance -----------------------------------------
    commercial_start = len(story)
    prices = data.get("pricing_options") or [data]
    for index, item in enumerate(prices):
        heading = section_header(6, "Investment & Payment Terms") if index == 0 else []
        if item.get("name"):
            heading += [Paragraph(item["name"], style_subtitle_cover), Spacer(1, 8)]
        if item.get("show_deposit"):
            card = payment_cards(item["total_price"], item["deposit"], item["balance"], item["deposit_label"], item["balance_label"])
        else:
            card = hero_price("INVESTMENT", item["total_price"], "See option scope below." if item.get("description") else "")
        story.append(KeepTogether(heading + [card]))
        if item.get("description"):
            story.append(Spacer(1, 8))
            story.append(Paragraph(item["description"], style_body))
        story.append(Spacer(1, 12))


    acceptance = [Paragraph(data["payment_note"], style_note), Spacer(1, 10)] + section_header(7, "Acceptance") + [Paragraph(data["acceptance_text"], style_body), Spacer(1, 4)]
    acceptance += signature_form(
        [("AUTHORIZED CUSTOMER NAME / TITLE", 1.0)],
        [("AUTHORIZED SIGNATURE", 0.5), ("DATE", 0.22), ("PURCHASE ORDER (IF APPLICABLE)", 0.28)],
    )
    story.append(KeepTogether(acceptance))
    story.append(Spacer(1, 26))

    story.append(Paragraph(company["name_full"], style_closer_name))
    story.append(Spacer(1, 2))
    story.append(Paragraph(company["closer_line"], style_note))
    story.append(Spacer(1, 12))
    story.append(Paragraph(data["thank_you"], style_thankyou))

    if len(prices) <= 2 and sum(len(item.get("description", "")) for item in prices) < 800:
        story = story[:commercial_start] + [KeepTogether(story[commercial_start:])]
    doc.build(story, canvasmaker=make_canvas_class(company))


# ---------------------------------------------------------------------------
# Example data -- Estill County Cooperative Extension Service, Cooling
# Tower Replacement (same shape/content as the source proposal).
# ---------------------------------------------------------------------------
