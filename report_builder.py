"""Builds a downloadable PDF signoff report from a completed agent run's
result dict (the same dict tab_run.py parses from RESULT_JSON)."""

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                 Table, TableStyle, HRFlowable)
from reportlab.lib.enums import TA_LEFT
import io


def build_report_pdf(result: dict) -> bytes:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('TitleX', parent=styles['Title'], fontSize=18,
                                  spaceAfter=4, textColor=colors.HexColor("#1a1a2e"))
    h2 = ParagraphStyle('H2X', parent=styles['Heading2'], fontSize=12,
                         spaceBefore=12, spaceAfter=6,
                         textColor=colors.HexColor("#2a2a4e"))
    body = ParagraphStyle('BodyX', parent=styles['Normal'], fontSize=9.5,
                           leading=13, spaceAfter=4, alignment=TA_LEFT)
    cell_style = ParagraphStyle('CellX', parent=styles['Normal'], fontSize=6.8,
                                 leading=8.5, alignment=TA_LEFT)
    cell_style_center = ParagraphStyle('CellCenterX', parent=cell_style,
                                        alignment=1)
    header_cell_style = ParagraphStyle('HeaderCellX', parent=cell_style,
                                        textColor=colors.white,
                                        fontName='Helvetica-Bold')
    header_cell_style_center = ParagraphStyle('HeaderCellCenterX', parent=header_cell_style,
                                               alignment=1)

    def P(text, style=cell_style):
        return Paragraph(str(text), style)

    story = []
    story.append(Paragraph("STA Timing-Closure Agent — Signoff Report", title_style))
    story.append(Paragraph(f"Design variant: {result.get('variant', 'n/a')}", body))
    story.append(HRFlowable(width="100%", thickness=0.6,
                             color=colors.HexColor("#dcdce6"),
                             spaceBefore=6, spaceAfter=10))

    timing_closed = (result.get("wns_final") is not None and result.get("tns_final") is not None
                      and result["wns_final"] >= 0 and result["tns_final"] >= 0)
    status_color = colors.HexColor("#1a7a3a") if timing_closed else colors.HexColor("#a0342a")
    status_style = ParagraphStyle('Status', parent=h2, textColor=status_color, fontSize=14)
    story.append(Paragraph("TIMING CLOSED" if timing_closed else "TIMING NOT CLOSED", status_style))
    story.append(Spacer(1, 8))

    def fmt(v, suffix=""):
        return "n/a" if v is None else f"{v:+.4f}{suffix}" if isinstance(v, float) else str(v)

    summary_rows = [
        ["Metric", "Baseline", "Final"],
        ["Setup WNS (ns)", fmt(result.get("wns_base")), fmt(result.get("wns_final"))],
        ["Setup TNS (ns)", fmt(result.get("tns_base")), fmt(result.get("tns_final"))],
        ["Hold WNS (ns)", fmt(result.get("hold_wns_before_run")), fmt(result.get("hold_wns_after_run"))],
    ]
    t = Table(summary_rows, colWidths=[2.0*inch, 2.0*inch, 2.0*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1a1a2e")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor("#dcdce6")),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#f7f7fb")]),
        ('LEFTPADDING', (0,0), (-1,-1), 6), ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))

    story.append(Paragraph(
        f"Area added: +{result.get('area_delta', 'n/a')} µm² "
        f"({result.get('area_growth_pct', 'n/a')}% of baseline)", body))
    story.append(Paragraph(
        f"Moves attempted: {result.get('moves_attempted', 'n/a')} — "
        f"kept: {result.get('moves_kept', 'n/a')}, "
        f"reverted: {result.get('moves_reverted', 'n/a')}", body))
    if result.get("moves_reverted_for_hold") is not None:
        story.append(Paragraph(
            f"Moves rejected specifically for hold: {result['moves_reverted_for_hold']}", body))
    extra_modes = result.get("extra_modes") or []
    if extra_modes:
        story.append(Paragraph(
            f"Additional modes checked: {len(extra_modes)} "
            f"({', '.join(extra_modes)}) — a move was rejected if it "
            f"regressed any of these, not just the primary mode.", body))
    story.append(Paragraph(
        f"LLM API calls used: {result.get('llm_turns_used', 'n/a')}", body))
    if result.get("plateaued"):
        story.append(Paragraph(
            "Run stopped early: WNS plateaued for several consecutive moves.", body))

    history = result.get("history") or []
    if history:
        story.append(Paragraph(f"Move History ({len(history)} attempts)", h2))
        rows = [[P("Instance", header_cell_style), P("From", header_cell_style),
                 P("To", header_cell_style), P("ΔWNS", header_cell_style_center),
                 P("ΔTNS", header_cell_style_center), P("Area Δ", header_cell_style_center),
                 P("Kept", header_cell_style_center), P("Note", header_cell_style)]]
        for h in history:
            note = ""
            if h.get("regressed_modes"):
                note = f"mode: {', '.join(h['regressed_modes'])}"
            elif h.get("reverted_for_hold"):
                note = "hold"
            elif not h.get("kept"):
                note = "no improvement"
            rows.append([
                P(h.get("instance", "")),
                P(h.get("from", "")),
                P(h.get("to", "")),
                P(f"{h.get('delta_wns', 0):+.3f}", cell_style_center),
                P(f"{h.get('delta_tns', 0):+.3f}", cell_style_center),
                P(f"{h.get('area_delta', 0):+.1f}", cell_style_center),
                P("Yes" if h.get("kept") else "No", cell_style_center),
                P(note),
            ])
        t2 = Table(rows, colWidths=[0.95*inch, 1.25*inch, 1.25*inch, 0.55*inch,
                                     0.55*inch, 0.5*inch, 0.4*inch, 1.15*inch],
                   repeatRows=1)
        t2.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1a1a2e")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 7.5),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor("#dcdce6")),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#f7f7fb")]),
            ('LEFTPADDING', (0,0), (-1,-1), 3), ('RIGHTPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]))
        story.append(t2)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            topMargin=0.6*inch, bottomMargin=0.6*inch,
                            leftMargin=0.6*inch, rightMargin=0.6*inch)
    doc.build(story)
    return buf.getvalue()
