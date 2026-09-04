"""Builds a downloadable PDF signoff report for a Power/IR-drop agent run."""

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                 Table, TableStyle, HRFlowable)
from reportlab.lib.enums import TA_LEFT
import io


def build_power_report_pdf(result: dict) -> bytes:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('TitleX', parent=styles['Title'], fontSize=18,
                                  spaceAfter=4, textColor=colors.HexColor("#1a1a2e"))
    h2 = ParagraphStyle('H2X', parent=styles['Heading2'], fontSize=12,
                         spaceBefore=12, spaceAfter=6,
                         textColor=colors.HexColor("#2a2a4e"))
    body = ParagraphStyle('BodyX', parent=styles['Normal'], fontSize=9.5,
                           leading=13, spaceAfter=4, alignment=TA_LEFT)
    cell_style = ParagraphStyle('CellX', parent=styles['Normal'], fontSize=7.5,
                                 leading=9, alignment=TA_LEFT)
    cell_center = ParagraphStyle('CellCenterX', parent=cell_style, alignment=1)
    header_style = ParagraphStyle('HeaderX', parent=cell_style,
                                   textColor=colors.white, fontName='Helvetica-Bold')
    header_center = ParagraphStyle('HeaderCenterX', parent=header_style, alignment=1)

    def P(text, style=cell_style):
        return Paragraph(str(text), style)

    story = []
    story.append(Paragraph("PhyFlow Power Agent — Signoff Report", title_style))
    story.append(Paragraph(f"Design: {result.get('design', 'n/a')}", body))
    story.append(HRFlowable(width="100%", thickness=0.6,
                             color=colors.HexColor("#dcdce6"),
                             spaceBefore=6, spaceAfter=10))

    base = result.get("baseline", {})
    final = result.get("final", {})

    def fmt_mv(v):
        return "n/a" if v is None else f"{v*1000:.2f} mV"

    def fmt_pct(v):
        return "n/a" if v is None else f"{v:.2f}%"

    worst_ok = True
    for net in ("VDD", "VSS"):
        pct = final.get(net, {}).get("pct_drop")
        if pct is not None and pct >= 2.0:
            worst_ok = False

    status_color = colors.HexColor("#1a7a3a") if worst_ok else colors.HexColor("#a0342a")
    status_style = ParagraphStyle('Status', parent=h2, textColor=status_color, fontSize=14)
    story.append(Paragraph(
        "WORST-CASE DROP UNDER 2%" if worst_ok else "WORST-CASE DROP ABOVE 2%",
        status_style))
    story.append(Spacer(1, 8))

    rows = [["Net", "Avg (before)", "Avg (final)", "Worst (before)",
             "Worst (final)", "% (final)"]]
    for net in ("VDD", "VSS"):
        b, f = base.get(net, {}), final.get(net, {})
        rows.append([net, fmt_mv(b.get("avg_drop_v")), fmt_mv(f.get("avg_drop_v")),
                     fmt_mv(b.get("worst_drop_v")), fmt_mv(f.get("worst_drop_v")),
                     fmt_pct(f.get("pct_drop"))])
    t = Table(rows, colWidths=[0.7*inch, 1.1*inch, 1.1*inch, 1.1*inch, 1.1*inch, 0.9*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1a1a2e")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor("#dcdce6")),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#f7f7fb")]),
        ('LEFTPADDING', (0,0), (-1,-1), 6), ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))

    history = result.get("history") or []
    story.append(Paragraph(f"Move History ({len(history)} attempts)", h2))
    rows2 = [[P("Move", header_style), P("Detail", header_style),
              P("Kept", header_center), P("Note", header_style)]]
    for h in history:
        move = h.get("move", "")
        if move == "try_decap":
            detail = (f"target_cap={h.get('target_cap')}, marginal effect="
                      f"{h.get('decap_marginal_effect')}")
        elif move == "try_via_repair":
            detail = "no_op" if h.get("no_op") else "vias repaired"
        elif move == "try_wider_straps":
            detail = (f"width={h.get('strap_width_um')}um, "
                      f"{h.get('elapsed_s')}s, worst {h.get('worst_drop_before')}"
                      f"→{h.get('worst_drop_after')}")
        else:
            detail = str(h)
        rows2.append([P(move), P(detail), P("Yes" if h.get("kept") else "No", cell_center),
                      P(h.get("note", ""))])
    t2 = Table(rows2, colWidths=[1.1*inch, 3.4*inch, 0.5*inch, 1.9*inch], repeatRows=1)
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1a1a2e")),
        ('FONTSIZE', (0,0), (-1,-1), 7.5),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor("#dcdce6")),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#f7f7fb")]),
        ('LEFTPADDING', (0,0), (-1,-1), 4), ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t2)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            topMargin=0.6*inch, bottomMargin=0.6*inch,
                            leftMargin=0.6*inch, rightMargin=0.6*inch)
    doc.build(story)
    return buf.getvalue()
