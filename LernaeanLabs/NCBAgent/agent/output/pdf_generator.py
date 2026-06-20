"""
PDF Generator — Form C/D (daily register) and Form G bundle (consignment notes).
Uses fpdf2 (pure Python, no system dependencies).

Layout is fully content-driven:
  • Column widths for "From whom" / "To whom" / Form-G-ref columns are measured
    from actual entry data before drawing begins, so nothing clips regardless of
    name length or reference-number length.
  • Row heights per entry are computed from the actual line-count needed to display
    the longest counterparty name / URN at the chosen font size.  Short content →
    compact rows; long content → taller rows.  Total page width always sums to 281 mm.
"""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF

from agent.config_loader import ClientProfile
from agent.models import DailyRegisterEntry, NCBTransaction, SubstanceRegister

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
NAVY        = (26, 58, 92)
LIGHT_BLUE  = (235, 241, 248)
WHITE       = (255, 255, 255)
LIGHT_YELLOW = (255, 243, 205)
LIGHT_RED   = (253, 232, 232)
MID_GREY    = (120, 120, 120)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
_TOTAL_W   = 281.0   # usable mm in landscape A4 (8 mm margins each side)
_FONT_DATA = 6       # pt — counterparty name / URN in data rows
_FONT_NOTE = 6       # pt — Form-G reference numbers
_FONT_HDR  = 7       # pt — column-header text
_PT_MM     = 0.3528  # 1 pt → mm
_LINE_LEAD = 1.75    # line-height = font_mm × this (≈ 3.7 mm at 6 pt)
_MIN_SUB_H = 5.0     # mm — minimum height for any single sub-row


# ---------------------------------------------------------------------------
# Content-measurement helpers
# ---------------------------------------------------------------------------

def _count_lines(pdf: FPDF, text: str, col_w: float, font_size: float) -> int:
    """
    Number of lines needed to render *text* wrapped in *col_w* mm at *font_size* pt.
    Respects explicit \\n line-breaks.
    """
    if not text:
        return 1
    pdf.set_font("Helvetica", "", font_size)
    total = 0
    for para in text.split("\n"):
        w = pdf.get_string_width(para) if para else 0
        total += max(1, math.ceil(w / col_w)) if w > 0 else 1
    return max(total, 1)


def _sub_row_height(
    pdf: FPDF,
    recv_t,
    disp_t,
    w_from: float,
    w_to: float,
) -> float:
    """
    Height (mm) needed for one sub-row given the receipt and dispatch
    transactions it must display.  Result is always ≥ _MIN_SUB_H.
    """
    lines = 1
    if recv_t:
        txt = f"{recv_t.counterparty or ''}\n{recv_t.counterparty_urn or ''}"
        lines = max(lines, _count_lines(pdf, txt, w_from, _FONT_DATA))
    if disp_t:
        txt = f"{disp_t.counterparty or ''}\n{disp_t.counterparty_urn or ''}"
        lines = max(lines, _count_lines(pdf, txt, w_to, _FONT_DATA))
    line_h = _FONT_DATA * _PT_MM * _LINE_LEAD
    return max(_MIN_SUB_H, lines * line_h)


def _alloc_variable_cols(
    pdf: FPDF,
    entries: list,
    fixed_sum: float,
) -> tuple[float, float, float, float]:
    """
    Pre-scan *entries* and return (W_FROM, W_NOTE_RECV, W_TO, W_NOTE_DISP) such
    that the four values sum to (_TOTAL_W − fixed_sum).

    • W_NOTE_RECV / W_NOTE_DISP  ← widest Form-G reference number + 2 mm padding
    • W_FROM / W_TO              ← remaining space split equally
    """
    PAD = 2.0
    MIN_NOTE = 16.0
    MIN_FROM = 22.0   # comfortably fits one URN on a single line

    max_from = MIN_FROM
    max_note_recv = MIN_NOTE
    max_to   = MIN_FROM
    max_note_disp = MIN_NOTE

    for entry in entries:
        pdf.set_font("Helvetica", "", _FONT_DATA)
        for t in entry.receipts:
            max_from = max(max_from,
                           pdf.get_string_width(t.counterparty or ""),
                           pdf.get_string_width(t.counterparty_urn or ""))
            pdf.set_font("Helvetica", "", _FONT_NOTE)
            max_note_recv = max(max_note_recv,
                                pdf.get_string_width(t.form_g_no or ""))
            pdf.set_font("Helvetica", "", _FONT_DATA)

        for t in entry.dispatches:
            max_to = max(max_to,
                         pdf.get_string_width(t.counterparty or ""),
                         pdf.get_string_width(t.counterparty_urn or ""))
            pdf.set_font("Helvetica", "", _FONT_NOTE)
            max_note_disp = max(max_note_disp,
                                pdf.get_string_width(t.form_g_no or ""))
            pdf.set_font("Helvetica", "", _FONT_DATA)

    w_note_recv = round(max_note_recv + PAD, 1)
    w_note_disp = round(max_note_disp + PAD, 1)

    pool = _TOTAL_W - fixed_sum - w_note_recv - w_note_disp
    pool = max(pool, 2 * MIN_FROM)          # guard: never negative
    w_from = round(pool / 2, 1)
    w_to   = round(pool - w_from, 1)

    # Absorb any floating-point rounding error into w_to
    diff = _TOTAL_W - (fixed_sum + w_from + w_note_recv + w_to + w_note_disp)
    w_to = round(w_to + diff, 1)

    return w_from, w_note_recv, w_to, w_note_disp


# ---------------------------------------------------------------------------
# Base PDF class
# ---------------------------------------------------------------------------

class _NCBBase(FPDF):
    def __init__(self, client_name: str, urn: str, substance: str, month_label: str):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.client_name = client_name
        self.urn = urn
        self.substance = substance
        self.month_label = month_label
        self.set_margins(8, 8, 8)
        self.set_auto_page_break(auto=True, margin=12)

    def header(self) -> None:
        self.set_fill_color(*NAVY)
        # Use self.w so this works on both landscape (297 mm) and portrait (210 mm)
        self.rect(0, 0, self.w, 14, "F")
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 9)
        self.set_xy(8, 3)
        self.cell(0, 8,
                  f"NDPS (RCS) Order 2013  |  {self.client_name}  |  URN: {self.urn}",
                  ln=False)
        self.set_font("Helvetica", "", 8)
        self.set_xy(0, 3)
        # Use page width so right-aligned text stays on page for any orientation
        self.cell(self.w - self.r_margin, 8, self.month_label, align="R", ln=False)
        self.set_text_color(0, 0, 0)
        self.ln(14)

    def footer(self) -> None:
        self.set_y(-10)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*MID_GREY)
        self.cell(0, 5, (
            "Generated by NCBAgent | LernaeanLabs  |  "
            "This document is a preparation tool. "
            "Compliance officer is legally responsible for all submissions."
            f"  |  Page {self.page_no()}"
        ), align="C")
        self.set_text_color(0, 0, 0)

    def _th(self, w: float, txt: str, border: str = "1", align: str = "C",
            row_h: float = 8, fill: bool = True) -> None:
        """
        Draw one header cell of exactly *row_h* height.

        Text wrapping is handled gracefully: if the text requires more lines than
        originally anticipated, line-height shrinks so all content still fits
        within *row_h*.  The x cursor advances by *w*; y is left unchanged so
        sibling cells in the same row stay aligned.
        """
        x0, y0 = self.get_x(), self.get_y()
        self.set_fill_color(*NAVY)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", _FONT_HDR)

        if not txt:
            self.cell(w, row_h, "", border=border, align=align, fill=fill)
            self.set_text_color(0, 0, 0)
            return

        # Count actual lines needed (accounts for word-wrap inside each paragraph)
        total_lines = 0
        for para in txt.split("\n"):
            pw = self.get_string_width(para) if para.strip() else 0
            total_lines += max(1, math.ceil(pw / w)) if pw > 0 else 1
        total_lines = max(total_lines, 1)

        line_h = row_h / total_lines
        self.multi_cell(w, line_h, txt, border=border, align=align, fill=fill)
        self.set_xy(x0 + w, y0)
        self.set_text_color(0, 0, 0)

    def _td(self, w: float, txt: str, border: str = "1", align: str = "L",
            h: float = 5, fill_color: tuple | None = None, bold: bool = False,
            font_size: int = _FONT_HDR) -> None:
        if fill_color:
            self.set_fill_color(*fill_color)
            fill = True
        else:
            fill = False
        self.set_font("Helvetica", "B" if bold else "", font_size)
        self.cell(w, h, str(txt), border=border, align=align, fill=fill)
        if fill_color:
            self.set_fill_color(255, 255, 255)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def generate_daily_register(
    register: SubstanceRegister,
    profile: ClientProfile,
    year: int,
    month: int,
    output_dir: Path,
) -> list[Path]:
    month_name = MONTH_NAMES[month].lower()
    entity_type = profile.entity_type
    generated: list[Path] = []

    if entity_type not in ("manufacturer", "trader", "both"):
        raise ValueError(
            f"Unknown entity_type '{entity_type}' for client '{profile.client_id}'. "
            f"Expected: manufacturer | trader | both"
        )

    if entity_type in ("manufacturer", "both"):
        path = output_dir / f"form_c_{month_name}_{year}.pdf"
        generate_form_c(register, profile, year, month, path)
        generated.append(path)

    if entity_type in ("trader", "both"):
        path = output_dir / f"form_d_{month_name}_{year}.pdf"
        generate_form_d(register, profile, year, month, path)
        generated.append(path)

    return generated


# ---------------------------------------------------------------------------
# Form C — Daily Register (Manufacturer)
# ---------------------------------------------------------------------------

def generate_form_c(
    register: SubstanceRegister,
    profile: ClientProfile,
    year: int,
    month: int,
    output_path: Path,
) -> None:
    entries = register.entries_for_month(year, month)
    if not entries:
        print(f"[Form C] No entries for {register.substance} in {MONTH_NAMES[month]} {year} — skipping.")
        return

    month_label = f"Form C  |  {register.substance}  |  {MONTH_NAMES[month]} {year}"
    pdf = _FormCPDF(profile.client_name, profile.urn, register.substance, month_label)
    pdf.add_page()

    # --- Title block ---
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "FORM-C", ln=True, align="C")
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, "[See sub-clause (2) of clause 4]", ln=True, align="C")
    pdf.cell(0, 5,
             "Register of manufacture, possession and consumption of controlled substances in Schedule-A",
             ln=True, align="C")
    pdf.ln(2)

    # Registration and substance on separate lines to avoid overlap
    notes_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_font("Helvetica", "", 8)
    pdf.set_x(pdf.l_margin)
    pdf.cell(notes_w, 5,
             f"Registration number under sub-clause (1) of clause 4: {profile.urn}",
             ln=True)
    pdf.set_x(pdf.l_margin)
    pdf.cell(notes_w, 5,
             f"Name of controlled substance: {register.substance}",
             ln=True)
    pdf.cell(40, 5, f"Month: {MONTH_NAMES[month]} {year}", ln=True)
    pdf.ln(2)

    # ---------------------------------------------------------------------------
    # Fixed column widths (mm) — sum = 166 mm; variable pool = 115 mm
    # ---------------------------------------------------------------------------
    W_DATE   = 18
    W_OPEN   = 20
    W_R_SL   = 8
    W_R_QTY  = 18
    W_PROD   = 16    # qty manufactured
    W_D_SL   = 8
    W_D_QTY  = 16
    W_D_PURP = 8
    W_CONS   = 12    # qty consumed in manufacture
    W_LOSS   = 12
    W_CLOSE  = 18
    W_INIT   = 11
    _FC_FIXED_SUM = (W_DATE + W_OPEN + W_R_SL + W_R_QTY + W_PROD +
                     W_D_SL + W_D_QTY + W_D_PURP + W_CONS + W_LOSS + W_CLOSE + W_INIT)
    # = 18+20+8+18+16+8+16+8+12+12+18+11 = 165 mm  → pool = 116 mm

    W_R_FROM, W_R_NOTE, W_D_TO, W_D_NOTE = _alloc_variable_cols(
        pdf, entries, _FC_FIXED_SUM
    )

    RECV_TOTAL = W_R_SL + W_R_QTY + W_R_FROM + W_R_NOTE
    DISP_TOTAL = W_D_SL + W_D_QTY + W_D_TO + W_D_NOTE + W_D_PURP
    TOTAL_W = _FC_FIXED_SUM + W_R_FROM + W_R_NOTE + W_D_TO + W_D_NOTE

    # --- Header row 1 ---
    RH1 = 12
    pdf._th(W_DATE,     "Date",                                                    row_h=RH1)
    pdf._th(W_OPEN,     "Qty in hand\nat beginning\nof day (kg)",                  row_h=RH1)
    pdf._th(RECV_TOTAL, "Details of raw material received / imported",             row_h=RH1)
    pdf._th(W_PROD,     "Qty mfrd.\n/ produced\n(kg)",                            row_h=RH1)
    pdf._th(DISP_TOTAL, "Details of quantity dispatched / sold / exported",        row_h=RH1)
    pdf._th(W_CONS,     "Qty consumed\nin mfr. (kg)",                             row_h=RH1)
    pdf._th(W_LOSS,     "Handling\nloss (kg)",                                    row_h=RH1)
    pdf._th(W_CLOSE,    "Qty in hand\nat close of\nday (kg)",                     row_h=RH1)
    pdf._th(W_INIT,     "Initial\nof auth.\nperson",                              row_h=RH1)
    pdf.ln(RH1)

    # --- Header row 2 — sub-columns ---
    RH2 = 10
    pdf._th(W_DATE,   "",                                         border="LRB", row_h=RH2)
    pdf._th(W_OPEN,   "",                                         border="LRB", row_h=RH2)
    pdf._th(W_R_SL,   "Sl\nNo",                                                  row_h=RH2)
    pdf._th(W_R_QTY,  "Qty\n(kg)",                                               row_h=RH2)
    pdf._th(W_R_FROM, "From whom received\n(Reg. No, Name & Address)",            row_h=RH2)
    pdf._th(W_R_NOTE, "Consignment\nNote / Bill\nof Entry No.",                  row_h=RH2)
    pdf._th(W_PROD,   "",                                         border="LRB", row_h=RH2)
    pdf._th(W_D_SL,   "Sl\nNo",                                                  row_h=RH2)
    pdf._th(W_D_QTY,  "Qty\n(kg)",                                               row_h=RH2)
    pdf._th(W_D_TO,   "To whom sold/sent\n(Reg. No, Name & Address)",             row_h=RH2)
    pdf._th(W_D_NOTE, "Note/\nIssue Slip\nNo.",                                  row_h=RH2)
    pdf._th(W_D_PURP, "Purp.",                                                    row_h=RH2)
    pdf._th(W_CONS,   "",                                         border="LRB", row_h=RH2)
    pdf._th(W_LOSS,   "",                                         border="LRB", row_h=RH2)
    pdf._th(W_CLOSE,  "",                                         border="LRB", row_h=RH2)
    pdf._th(W_INIT,   "",                                         border="LRB", row_h=RH2)
    pdf.ln(RH2)

    # --- Column number row ---
    pdf.set_font("Helvetica", "I", 6)
    pdf.set_fill_color(*LIGHT_BLUE)
    for num, w in [
        ("1", W_DATE), ("2", W_OPEN),
        ("3", W_R_SL), ("4", W_R_QTY), ("5", W_R_FROM), ("6", W_R_NOTE),
        ("7", W_PROD),
        ("8", W_D_SL), ("9", W_D_QTY), ("10", W_D_TO), ("11", W_D_NOTE),
        ("12", W_D_PURP),
        ("13", W_CONS), ("14", W_LOSS), ("15", W_CLOSE), ("16", W_INIT),
    ]:
        pdf.cell(w, 4, num, border="1", align="C", fill=True)
    pdf.ln()

    # --- Data rows ---
    for entry in entries:
        fill_color = None
        if entry.closing_kg < Decimal("0"):
            fill_color = LIGHT_RED
        elif entry.requires_human_review:
            fill_color = LIGHT_YELLOW

        n_recv = max(len(entry.receipts), 1)
        n_prod = max(len(entry.productions), 1)
        n_disp = max(len(entry.dispatches), 1)
        n_rows = max(n_recv, max(n_prod, n_disp))

        # Per-sub-row heights
        sub_heights = []
        for i in range(n_rows):
            rt = entry.receipts[i]  if i < len(entry.receipts)  else None
            dt = entry.dispatches[i] if i < len(entry.dispatches) else None
            sub_heights.append(_sub_row_height(pdf, rt, dt, W_R_FROM, W_D_TO))
        total_h = sum(sub_heights)

        if pdf.get_y() + total_h > pdf.page_break_trigger:
            pdf.add_page()

        y0  = pdf.get_y()
        x0  = pdf.l_margin
        nil = "(Nil)" if entry.nil_transaction else ""

        def _span(w, txt, align="C", bold=False, color=None):
            kw = {"fill_color": color} if color else {}
            pdf._td(w, txt, border="LR", align=align, h=total_h, bold=bold, **kw)

        pdf.set_xy(x0, y0)
        _span(W_DATE,  entry.date.strftime("%d %b"))
        _span(W_OPEN,  f"{entry.opening_kg:.3f}", align="R", bold=True)

        recv_x = pdf.get_x()

        # Receipt sub-rows
        for i in range(n_rows):
            h_i = sub_heights[i]
            y_i = y0 + sum(sub_heights[:i])
            rt  = entry.receipts[i] if i < len(entry.receipts) else None

            pdf.set_xy(recv_x, y_i)
            pdf._td(W_R_SL, str(i + 1) if rt else "", border="LR", align="C", h=h_i,
                    fill_color=fill_color or (LIGHT_BLUE if not rt and not nil and i == 0 else None))
            pdf._td(W_R_QTY, f"{rt.quantity_kg:.3f}" if rt else (nil if i == 0 else ""),
                    border="LR", align="R", h=h_i)

            if rt:
                from_txt = f"{rt.counterparty or ''}\n{rt.counterparty_urn or ''}"
                n_ln = _count_lines(pdf, from_txt, W_R_FROM, _FONT_DATA)
                pdf.set_font("Helvetica", "", _FONT_DATA)
                xb = pdf.get_x()
                pdf.multi_cell(W_R_FROM, h_i / n_ln, from_txt, border="LR", align="L")
                pdf.set_xy(xb + W_R_FROM, y_i)
            else:
                pdf._td(W_R_FROM, nil if i == 0 else "", border="LR", align="L", h=h_i)

            pdf._td(W_R_NOTE, rt.form_g_no if rt else "", border="LR", align="C", h=h_i,
                    font_size=_FONT_NOTE)

        # Produced column (single spanning cell)
        prod_x = recv_x + W_R_SL + W_R_QTY + W_R_FROM + W_R_NOTE
        pdf.set_xy(prod_x, y0)
        prod_txt = f"{entry.total_produced_kg:.3f}" if entry.total_produced_kg else ""
        pdf._td(W_PROD, prod_txt, border="LR", align="R",
                h=total_h, bold=bool(prod_txt), fill_color=fill_color)

        # Dispatch sub-rows
        disp_x = prod_x + W_PROD
        for i in range(n_rows):
            h_i = sub_heights[i]
            y_i = y0 + sum(sub_heights[:i])
            dt  = entry.dispatches[i] if i < len(entry.dispatches) else None

            pdf.set_xy(disp_x, y_i)
            pdf._td(W_D_SL, str(i + 1) if dt else "", border="LR", align="C", h=h_i)
            pdf._td(W_D_QTY, f"{dt.quantity_kg:.3f}" if dt else (nil if i == 0 else ""),
                    border="LR", align="R", h=h_i)

            if dt:
                to_txt = f"{dt.counterparty or ''}\n{dt.counterparty_urn or ''}"
                n_ln = _count_lines(pdf, to_txt, W_D_TO, _FONT_DATA)
                pdf.set_font("Helvetica", "", _FONT_DATA)
                xb = pdf.get_x()
                pdf.multi_cell(W_D_TO, h_i / n_ln, to_txt, border="LR", align="L")
                pdf.set_xy(xb + W_D_TO, y_i)
            else:
                pdf._td(W_D_TO, nil if i == 0 else "", border="LR", align="L", h=h_i)

            pdf._td(W_D_NOTE, dt.form_g_no if dt else "", border="LR", align="C", h=h_i,
                    font_size=_FONT_NOTE)
            pdf._td(W_D_PURP, "", border="LR", h=h_i)

        # Trailing spanning cells
        trail_x = disp_x + W_D_SL + W_D_QTY + W_D_TO + W_D_NOTE + W_D_PURP
        pdf.set_xy(trail_x, y0)
        cons_txt = f"{entry.total_consumed_kg:.3f}" if entry.total_consumed_kg else ""
        pdf._td(W_CONS,  cons_txt, border="LR", align="R", h=total_h, fill_color=fill_color)
        pdf._td(W_LOSS,  f"{entry.handling_loss_kg:.3f}", border="LR", align="R", h=total_h)
        pdf._td(W_CLOSE, f"{entry.closing_kg:.3f}", border="LR", align="R",
                h=total_h, bold=True, fill_color=fill_color)
        pdf._td(W_INIT,  "_____", border="LR", align="C", h=total_h)

        # Row separator
        y_end = y0 + total_h
        pdf.set_draw_color(*NAVY)
        pdf.line(x0, y_end, x0 + TOTAL_W, y_end)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_xy(x0, y_end)

    # Bottom border
    pdf.set_draw_color(*NAVY)
    pdf.cell(TOTAL_W, 0, "", border="T")
    pdf.ln(4)

    # Notes section
    notes_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_xy(pdf.l_margin, pdf.get_y())
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(notes_w, 5, "Note:", ln=True)
    pdf.set_font("Helvetica", "", 7)
    for note in [
        "(1) The quantity shall be indicated in kilograms.",
        "(2) This record shall be maintained on day-to-day basis and entries shall be made for each day "
        "the establishment is open irrespective of whether there is any transaction or not. "
        "Entries shall be completed before the close of the day and initialled by the authorised person. "
        "Each page shall carry the running serial number.",
        "(3) If more than one controlled substance is manufactured, a separate register shall be maintained "
        "for each substance.",
        "(4) Column 7 records the net quantity produced/manufactured during the day.",
        "(5) Column 13 records the quantity of this substance consumed as raw material in the manufacture "
        "of another controlled or non-controlled substance.",
        "(6) In case of import / export, record the NOC number and date issued by the Narcotics Commissioner "
        "in place of the registration number.",
    ]:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(notes_w, 4, note)

    pdf.output(str(output_path))
    print(f"[Form C] Written: {output_path}")


class _FormCPDF(_NCBBase):
    pass


# ---------------------------------------------------------------------------
# Form D — Daily Register (Trader)
# ---------------------------------------------------------------------------

def generate_form_d(
    register: SubstanceRegister,
    profile: ClientProfile,
    year: int,
    month: int,
    output_path: Path,
) -> None:
    entries = register.entries_for_month(year, month)
    if not entries:
        print(f"[Form D] No entries for {register.substance} in {MONTH_NAMES[month]} {year} — skipping.")
        return

    month_label = f"Form D  |  {register.substance}  |  {MONTH_NAMES[month]} {year}"
    pdf = _FormDPDF(profile.client_name, profile.urn, register.substance, month_label)
    pdf.add_page()

    # --- Title block ---
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "FORM-D", ln=True, align="C")
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, "[See sub-clause (5) of clause 4]", ln=True, align="C")
    pdf.cell(0, 5,
             "Register of consumption, sale, import or export of controlled substance in Schedule-A",
             ln=True, align="C")
    pdf.ln(2)

    # Registration number and substance name on separate lines — no overlap
    notes_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_font("Helvetica", "", 8)
    pdf.set_x(pdf.l_margin)
    pdf.cell(notes_w, 5,
             f"Registration number issued under sub-clause (1) of clause 4: {profile.urn}",
             ln=True)
    pdf.set_x(pdf.l_margin)
    pdf.cell(notes_w, 5,
             f"Name of controlled substance: {register.substance}",
             ln=True)
    pdf.cell(40, 5, f"Month: {MONTH_NAMES[month]} {year}", ln=True)
    pdf.ln(2)

    # ---------------------------------------------------------------------------
    # Fixed column widths (mm) — sum = 153 mm; variable pool = 128 mm
    # ---------------------------------------------------------------------------
    W_DATE      = 18
    W_OPEN      = 20
    W_RECV_SL   = 8
    W_RECV_QTY  = 18
    W_DISP_SL   = 8
    W_DISP_QTY  = 18
    W_DISP_CONS = 10   # "Consumed" — always blank for traders
    W_DISP_PURP = 8    # "Purpose"  — always blank for traders
    W_LOSS      = 14
    W_CLOSE     = 20
    W_INIT      = 11
    _FD_FIXED_SUM = (W_DATE + W_OPEN + W_RECV_SL + W_RECV_QTY +
                     W_DISP_SL + W_DISP_QTY + W_DISP_CONS + W_DISP_PURP +
                     W_LOSS + W_CLOSE + W_INIT)
    # = 18+20+8+18+8+18+10+8+14+20+11 = 153 mm  → pool = 128 mm

    W_RECV_FROM, W_RECV_NOTE, W_DISP_TO, W_DISP_NOTE = _alloc_variable_cols(
        pdf, entries, _FD_FIXED_SUM
    )

    RECV_TOTAL = W_RECV_SL + W_RECV_QTY + W_RECV_FROM + W_RECV_NOTE
    DISP_TOTAL = W_DISP_SL + W_DISP_QTY + W_DISP_TO + W_DISP_NOTE + W_DISP_CONS + W_DISP_PURP
    TOTAL_W    = _FD_FIXED_SUM + W_RECV_FROM + W_RECV_NOTE + W_DISP_TO + W_DISP_NOTE

    # --- Header row 1 (group headers) ---
    RH1 = 12
    pdf._th(W_DATE,     "Date",                                                         row_h=RH1)
    pdf._th(W_OPEN,     "Qty in hand\nat beginning\nof day (kg)",                       row_h=RH1)
    pdf._th(RECV_TOTAL, "Details of quantity received / imported",                      row_h=RH1)
    pdf._th(DISP_TOTAL, "Details of quantity distributed / sold / exported / consumed", row_h=RH1)
    pdf._th(W_LOSS,     "Handling\nloss, if any\n(kg)",                                 row_h=RH1)
    pdf._th(W_CLOSE,    "Qty in hand\nat close of\nday (kg)",                           row_h=RH1)
    pdf._th(W_INIT,     "Initial\nof auth.\nperson",                                    row_h=RH1)
    pdf.ln(RH1)

    # --- Header row 2 (sub-column headers) ---
    RH2 = 10
    pdf._th(W_DATE,     "",                                          border="LRB", row_h=RH2)
    pdf._th(W_OPEN,     "",                                          border="LRB", row_h=RH2)
    pdf._th(W_RECV_SL,  "Sl\nNo",                                                  row_h=RH2)
    pdf._th(W_RECV_QTY, "Qty\n(kg)",                                               row_h=RH2)
    pdf._th(W_RECV_FROM,"From whom received\n(Reg. No, Name & Address)",            row_h=RH2)
    pdf._th(W_RECV_NOTE,"Consignment\nNote / Bill\nof Entry No.",                  row_h=RH2)
    pdf._th(W_DISP_SL,  "Sl\nNo",                                                  row_h=RH2)
    pdf._th(W_DISP_QTY, "Qty\n(kg)",                                               row_h=RH2)
    pdf._th(W_DISP_TO,  "To whom sold/sent\n(Reg. No, Name & Address)",             row_h=RH2)
    pdf._th(W_DISP_NOTE,"Note/\nIssue Slip\nNo.",                                  row_h=RH2)
    pdf._th(W_DISP_CONS,"Consd.\n(kg)",                                            row_h=RH2)
    pdf._th(W_DISP_PURP,"Purp.",                                                   row_h=RH2)
    pdf._th(W_LOSS,     "",                                          border="LRB", row_h=RH2)
    pdf._th(W_CLOSE,    "",                                          border="LRB", row_h=RH2)
    pdf._th(W_INIT,     "",                                          border="LRB", row_h=RH2)
    pdf.ln(RH2)

    # --- Column number row ---
    pdf.set_font("Helvetica", "I", 6)
    pdf.set_fill_color(*LIGHT_BLUE)
    for num, w in [
        ("1", W_DATE), ("2", W_OPEN),
        ("3", W_RECV_SL), ("4", W_RECV_QTY), ("5", W_RECV_FROM), ("6", W_RECV_NOTE),
        ("7", W_DISP_SL), ("8", W_DISP_QTY), ("9", W_DISP_TO),  ("10", W_DISP_NOTE),
        ("11", W_DISP_CONS), ("12", W_DISP_PURP),
        ("13", W_LOSS), ("14", W_CLOSE), ("15", W_INIT),
    ]:
        pdf.cell(w, 4, num, border="1", align="C", fill=True)
    pdf.ln()

    # --- Data rows ---
    for entry in entries:
        fill_color = None
        if entry.closing_kg < Decimal("0"):
            fill_color = LIGHT_RED
        elif entry.requires_human_review:
            fill_color = LIGHT_YELLOW

        n_recv = max(len(entry.receipts), 1)
        n_disp = max(len(entry.dispatches), 1)
        n_rows = max(n_recv, n_disp)

        # Compute per-sub-row heights from actual content
        sub_heights: list[float] = []
        for i in range(n_rows):
            rt = entry.receipts[i]   if i < len(entry.receipts)   else None
            dt = entry.dispatches[i] if i < len(entry.dispatches) else None
            sub_heights.append(_sub_row_height(pdf, rt, dt, W_RECV_FROM, W_DISP_TO))
        total_h = sum(sub_heights)

        if pdf.get_y() + total_h > pdf.page_break_trigger:
            pdf.add_page()

        y0  = pdf.get_y()
        x0  = pdf.l_margin
        nil = "(Nil)" if entry.nil_transaction else ""

        # Fixed spanning cells
        def _span(w, txt, align="C", bold=False, color=None):
            kw = {"fill_color": color} if color else {}
            pdf._td(w, txt, border="LR", align=align, h=total_h, bold=bold, **kw)

        pdf.set_xy(x0, y0)
        _span(W_DATE, entry.date.strftime("%d %b"))
        _span(W_OPEN, f"{entry.opening_kg:.3f}", align="R", bold=True)

        recv_x = pdf.get_x()

        # Receipt sub-rows
        for i in range(n_rows):
            h_i = sub_heights[i]
            y_i = y0 + sum(sub_heights[:i])
            rt  = entry.receipts[i] if i < len(entry.receipts) else None

            pdf.set_xy(recv_x, y_i)
            pdf._td(W_RECV_SL, str(i + 1) if rt else "", border="LR", align="C", h=h_i,
                    fill_color=fill_color or (LIGHT_BLUE if not rt and not nil and i == 0 else None))
            pdf._td(W_RECV_QTY,
                    f"{rt.quantity_kg:.3f}" if rt else (nil if i == 0 else ""),
                    border="LR", align="R", h=h_i)

            if rt:
                from_txt = f"{rt.counterparty or ''}\n{rt.counterparty_urn or ''}"
                n_ln = _count_lines(pdf, from_txt, W_RECV_FROM, _FONT_DATA)
                pdf.set_font("Helvetica", "", _FONT_DATA)
                xb = pdf.get_x()
                pdf.multi_cell(W_RECV_FROM, h_i / n_ln, from_txt, border="LR", align="L")
                pdf.set_xy(xb + W_RECV_FROM, y_i)
            else:
                pdf._td(W_RECV_FROM, nil if i == 0 else "", border="LR", align="L", h=h_i)

            pdf._td(W_RECV_NOTE, rt.form_g_no if rt else "",
                    border="LR", align="C", h=h_i, font_size=_FONT_NOTE)

        # Dispatch sub-rows
        disp_x = recv_x + W_RECV_SL + W_RECV_QTY + W_RECV_FROM + W_RECV_NOTE
        for i in range(n_rows):
            h_i = sub_heights[i]
            y_i = y0 + sum(sub_heights[:i])
            dt  = entry.dispatches[i] if i < len(entry.dispatches) else None

            pdf.set_xy(disp_x, y_i)
            pdf._td(W_DISP_SL, str(i + 1) if dt else "", border="LR", align="C", h=h_i)
            pdf._td(W_DISP_QTY,
                    f"{dt.quantity_kg:.3f}" if dt else (nil if i == 0 else ""),
                    border="LR", align="R", h=h_i)

            if dt:
                to_txt = f"{dt.counterparty or ''}\n{dt.counterparty_urn or ''}"
                n_ln = _count_lines(pdf, to_txt, W_DISP_TO, _FONT_DATA)
                pdf.set_font("Helvetica", "", _FONT_DATA)
                xb = pdf.get_x()
                pdf.multi_cell(W_DISP_TO, h_i / n_ln, to_txt, border="LR", align="L")
                pdf.set_xy(xb + W_DISP_TO, y_i)
            else:
                pdf._td(W_DISP_TO, nil if i == 0 else "", border="LR", align="L", h=h_i)

            pdf._td(W_DISP_NOTE, dt.form_g_no if dt else "",
                    border="LR", align="C", h=h_i, font_size=_FONT_NOTE)
            pdf._td(W_DISP_CONS, "", border="LR", h=h_i)
            pdf._td(W_DISP_PURP, "", border="LR", h=h_i)

        # Trailing spanning cells
        trail_x = disp_x + W_DISP_SL + W_DISP_QTY + W_DISP_TO + W_DISP_NOTE + W_DISP_CONS + W_DISP_PURP
        pdf.set_xy(trail_x, y0)
        pdf._td(W_LOSS,  f"{entry.handling_loss_kg:.3f}", border="LR", align="R",
                h=total_h, fill_color=fill_color)
        pdf._td(W_CLOSE, f"{entry.closing_kg:.3f}", border="LR", align="R",
                h=total_h, bold=True, fill_color=fill_color)
        pdf._td(W_INIT,  "_______", border="LR", align="C", h=total_h)

        # Row separator line
        y_end = y0 + total_h
        pdf.set_draw_color(*NAVY)
        pdf.line(x0, y_end, x0 + TOTAL_W, y_end)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_xy(x0, y_end)

    # Bottom border
    pdf.set_draw_color(*NAVY)
    pdf.cell(TOTAL_W, 0, "", border="T")
    pdf.ln(4)

    # Notes section
    notes_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_xy(pdf.l_margin, pdf.get_y())
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(notes_w, 5, "Note:", ln=True)
    pdf.set_font("Helvetica", "", 7)
    for note in [
        "(1) The quantity shall be indicated in kilograms.",
        "(2) This record shall be maintained on day-to-day basis and entries shall be made for each day "
        "the establishment opens for work irrespective of whether there is any transaction or not. "
        "Entries shall be completed for each day before the close of the day and the person authorised "
        "to maintain the accounts shall put his initial after the entries. "
        "Each page of the register shall contain the running serial number.",
        "(3) If more than one controlled substance is dealt with, separate register shall be maintained "
        "for each of such substances.",
        "(4) In case of import / export, in place of registration number, number and date of the No "
        "Objection Certificate issued by the Narcotics Commissioner shall be indicated.",
        "(5) Strike out whichever is not applicable.",
    ]:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(notes_w, 4, note)

    pdf.output(str(output_path))
    print(f"[Form D] Written: {output_path}")


class _FormDPDF(_NCBBase):
    pass


# ---------------------------------------------------------------------------
# Form G — Consignment Note Bundle
# ---------------------------------------------------------------------------

def generate_form_g_bundle(
    register: SubstanceRegister,
    profile: ClientProfile,
    year: int,
    month: int,
    output_path: Path,
) -> None:
    entries = register.entries_for_month(year, month)
    all_transactions: list[NCBTransaction] = []
    for entry in entries:
        all_transactions.extend(entry.all_transactions)

    if not all_transactions:
        print(f"[Form G] No transactions for {register.substance} in {MONTH_NAMES[month]} {year} — skipping.")
        return

    month_label = f"Form G Bundle  |  {register.substance}  |  {MONTH_NAMES[month]} {year}"
    pdf = _FormGPDF(profile.client_name, profile.urn, register.substance, month_label)

    for txn in all_transactions:
        _add_form_g_page(pdf, txn, profile)

    pdf.output(str(output_path))
    print(f"[Form G] Written: {output_path}  ({len(all_transactions)} consignment notes)")


class _FormGPDF(_NCBBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_orientation = "P"

    def add_page(self, *args, **kwargs):
        super().add_page(orientation="P")


def _add_form_g_page(pdf: _FormGPDF, txn: NCBTransaction, profile: ClientProfile) -> None:
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "FORM-G", ln=True, align="C")
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, "[See sub-clause (1) of clause 7]", ln=True, align="C")
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Consignment Note", ln=True, align="C")
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 5, "(To accompany a consignment of controlled substance)", ln=True, align="C")
    pdf.ln(4)

    is_sale = txn.txn_type == "SALE"
    consignor_name = profile.client_name if is_sale else txn.counterparty
    consignor_urn  = profile.urn         if is_sale else txn.counterparty_urn
    consignee_name = txn.counterparty    if is_sale else profile.client_name
    consignee_urn  = txn.counterparty_urn if is_sale else profile.urn

    row_w = pdf.w - pdf.l_margin - pdf.r_margin
    w_sl, w_label, w_val = 8, 60, row_w - 8 - 60

    def _row(sl: str, label: str, value: str) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(w_sl, 7, sl + ".", border="LTB")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(w_label, 7, label + ":", border="TB")
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(w_val, 7, value, border="TRB")

    _row("1", "Registration Number of the consignor (URN)", consignor_urn or "")
    _row("2", "Name and address of the consignor", consignor_name or "")
    _row("3", "Name and address of the consignee", consignee_name or "")
    _row("4", "Registration Number of the consignee (URN)", consignee_urn or "")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 5, "5.   Description and quantity of the consignment:", ln=True)
    pdf.ln(1)

    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(80, 6, "Particulars of Controlled Substance", border="1", align="C", fill=True)
    pdf.cell(40, 6, "No. of Packages",   border="1", align="C", fill=True)
    pdf.cell(35, 6, "Gross Weight (kg)", border="1", align="C", fill=True)
    pdf.cell(35, 6, "Net Weight (kg)",   border="1", align="C", fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(80, 7, txn.substance, border="1")
    pdf.cell(40, 7, "1", border="1", align="C")
    pdf.cell(35, 7, f"{txn.quantity_kg:.3f}", border="1", align="R")
    pdf.cell(35, 7, f"{txn.quantity_kg:.3f}", border="1", align="R")
    pdf.ln(10)

    _row("6", "Mode of transport (Transporter, Vehicle/RR/LR No.)", "______________________________")
    _row("7", "Total number of tamper-proof seals affixed", "______________________________")
    pdf.ln(6)

    pdf.set_font("Helvetica", "", 8)
    pdf.cell(95, 6, "Signature of the consignor with date:", border="1")
    pdf.cell(95, 6, "(Name in capital letters)", border="1", ln=True)
    pdf.cell(95, 14, "", border="1")
    pdf.cell(95, 14, "", border="1", ln=True)
    pdf.ln(4)

    # Item 8 (was erroneously numbered 7 in original)
    _row("8", "Date and time of receipt by the consignee and his remarks", "______________________________")
    pdf.ln(4)

    pdf.cell(95, 6, "Signature of the consignee:", border="1")
    pdf.cell(95, 6, "(Name in capital letters)", border="1", ln=True)
    pdf.cell(95, 14, "", border="1")
    pdf.cell(95, 14, "", border="1", ln=True)
    pdf.ln(6)

    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*MID_GREY)
    pdf.cell(0, 5,
             f"Voucher: {txn.voucher_no}  |  Form-G Ref: {txn.form_g_no}  |  "
             f"Date: {txn.date.strftime('%d %b %Y')}  |  "
             f"Substance: {txn.substance}  |  Qty: {txn.quantity_kg:.3f} kg  |  "
             f"Counterparty URN: {txn.counterparty_urn or 'MISSING'}",
             align="C", ln=True)
    pdf.set_text_color(0, 0, 0)

    g_notes_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.ln(2)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(g_notes_w, 4, "Notes:", ln=True)
    pdf.set_font("Helvetica", "", 7)
    for note in [
        "(1) The consignment Note should be serially numbered on annual basis.",
        "(2) The consignor should record a certificate on the cover page of each book containing "
        "consignment Notes indicating the number of pages contained in the consignment Note-Book.",
        "(3) The books containing consignment Notes used or currently under use shall be produced to "
        "the authorised officer whenever called upon.",
    ]:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(g_notes_w, 4, note)
