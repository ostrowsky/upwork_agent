"""Render a case study into a polished one-pager attachment (PDF + optional PNG).

Used for synthetic, job-tailored cases that get attached to the Upwork proposal.
The PDF is built with reportlab (platypus); the PNG infographic with Pillow.
Layout data is a plain dict so rendering stays unit-testable without an LLM.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

_PUNCT = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "•": "-", "…": "...", " ": " ", "×": "x",
}


def clean_text(s: str | None) -> str:
    """Normalize unicode to glyphs a base TTF/PDF font can render (no tofu boxes)."""
    s = s or ""
    for k, v in _PUNCT.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s)
    return s.encode("latin-1", "ignore").decode("latin-1")

DATA_DIR = Path(__file__).resolve().parent / "data"
ARTIFACT_DIR = DATA_DIR / "case_artifacts"

# Brand-ish palette.
_NAVY = "#0f2a43"
_ACCENT = "#14a06b"
_GREY = "#5b6b7b"


def _derive_metrics(results: list[str]) -> list[dict]:
    """Pull KPI chips (a number + short unit) out of result bullet text."""
    import re

    out = []
    for r in results:
        m = re.search(r"(\$?\d[\d.,]*\s*(?:[KkMmxX%]|млн|млрд)?)", r or "")
        if m:
            val = m.group(1).strip()
            label = re.sub(r"[^a-zA-Zа-яА-Я ]", " ", (r or "")).split()
            out.append({"label": " ".join(label[:2])[:16] or "KPI", "value": val[:14]})
        if len(out) >= 4:
            break
    return out


def _layout_from_case(case) -> dict:
    """Normalize a CaseStudy row (or dict) into the renderer's layout dict (cleaned)."""
    get = (lambda k, d=None: case.get(k, d)) if isinstance(case, dict) else (lambda k, d=None: getattr(case, k, d))
    results = [clean_text(x) for x in (get("results_list") or ([get("result")] if get("result") else [])) if x]
    metrics = [{"label": clean_text(m.get("label", "")), "value": clean_text(str(m.get("value", "")))}
               for m in (get("metrics_list") or []) if isinstance(m, dict)]
    if not metrics:
        metrics = _derive_metrics(results) or [{"label": "Result", "value": "Delivered"}]
    return {
        "title": clean_text((get("title") or "Case Study").strip()),
        "subtitle": clean_text(" · ".join([x for x in [get("niche"), get("budget_range"), get("duration")] if x])),
        "role": clean_text(get("role") or ""),
        "stack": clean_text(get("stack") or ""),
        "summary": clean_text(get("description") or ""),
        "approach": [clean_text(x) for x in (get("approach_list") or []) if x],
        "results": results,
        "metrics": metrics[:4],
    }


def render_case_pdf(case, out_path: str | Path | None = None) -> str:
    """Render a case one-pager to PDF. Returns the file path."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable, ListItem,
    )

    d = _layout_from_case(case)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    cid = (case.get("id") if isinstance(case, dict) else getattr(case, "id", None)) or "tmp"
    out_path = Path(out_path) if out_path else ARTIFACT_DIR / f"case_{cid}.pdf"

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=colors.HexColor(_NAVY), fontSize=22, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], textColor=colors.HexColor(_GREY), fontSize=10, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=colors.HexColor(_ACCENT), fontSize=13, spaceBefore=8, spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10.5, leading=15)

    story = [Paragraph(d["title"], h1)]
    if d["subtitle"]:
        story.append(Paragraph(d["subtitle"], sub))

    # KPI band.
    if d["metrics"]:
        cells = [[Paragraph(f"<b>{m.get('value','')}</b>", body) for m in d["metrics"]],
                 [Paragraph(f"<font color='{_GREY}' size=8>{m.get('label','')}</font>", body) for m in d["metrics"]]]
        t = Table(cells, colWidths=[ (170 / max(len(d['metrics']),1)) * mm / 1 ] * len(d["metrics"]))
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef6f1")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(_ACCENT)),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cfe6da")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story += [Spacer(1, 4), t, Spacer(1, 6)]

    if d["role"] or d["stack"]:
        meta = " &nbsp;|&nbsp; ".join([x for x in [f"<b>Role:</b> {d['role']}" if d['role'] else "",
                                                   f"<b>Stack:</b> {d['stack']}" if d['stack'] else ""] if x])
        story.append(Paragraph(meta, body))

    if d["summary"]:
        story += [Paragraph("Overview", h2), Paragraph(d["summary"], body)]
    if d["approach"]:
        story += [Paragraph("Approach", h2),
                  ListFlowable([ListItem(Paragraph(x, body)) for x in d["approach"]], bulletType="bullet")]
    if d["results"]:
        story += [Paragraph("Results", h2),
                  ListFlowable([ListItem(Paragraph(x, body)) for x in d["results"] if x], bulletType="bullet")]

    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            topMargin=18 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm)
    doc.build(story)
    return str(out_path)


def render_case_png(case, out_path: str | Path | None = None) -> str:
    """Render a compact KPI infographic to PNG (Pillow). Returns the file path."""
    from PIL import Image, ImageDraw, ImageFont

    def _font(size, bold=False):
        for name in (("arialbd.ttf", "arial.ttf") if bold else ("arial.ttf",)) + ("DejaVuSans.ttf",):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _ascii(s):  # default-safe: replace glyphs a base font may lack
        return (s or "").replace("–", "-").replace("—", "-").replace("•", "-").replace("’", "'")

    d = _layout_from_case(case)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    cid = (case.get("id") if isinstance(case, dict) else getattr(case, "id", None)) or "tmp"
    out_path = Path(out_path) if out_path else ARTIFACT_DIR / f"case_{cid}.png"

    W, H = 1200, 628
    f_title, f_sub, f_val, f_lbl, f_body = _font(34, True), _font(20), _font(40, True), _font(18), _font(20)
    img = Image.new("RGB", (W, H), "#ffffff")
    dr = ImageDraw.Draw(img)
    dr.rectangle([0, 0, W, 120], fill=_NAVY)
    dr.text((40, 40), _ascii(d["title"])[:54], fill="#ffffff", font=f_title)
    dr.text((40, 140), _ascii(d["subtitle"])[:96], fill=_GREY, font=f_sub)

    metrics = d["metrics"][:4] or []
    if metrics:
        n = len(metrics)
        cw = (W - 80) // n
        for i, m in enumerate(metrics):
            x = 40 + i * cw
            dr.rectangle([x + 8, 200, x + cw - 8, 360], outline=_ACCENT, width=3)
            dr.text((x + 28, 248), _ascii(str(m.get("value", "")))[:14], fill=_NAVY, font=f_val)
            dr.text((x + 28, 312), _ascii(str(m.get("label", "")))[:18], fill=_GREY, font=f_lbl)
    y = 405
    for r in (d["results"] or [])[:3]:
        if r:
            dr.text((40, y), "- " + _ascii(str(r))[:88], fill="#222222", font=f_body)
            y += 40
    img.save(out_path)
    return str(out_path)


if __name__ == "__main__":  # smoke render
    sample = {
        "id": 0, "title": "Real-time Multiplayer Arena (Unity + Photon)",
        "niche": "Unity multiplayer", "budget_range": "$12,000–$18,000", "duration": "3 months",
        "role": "Lead Unity Developer", "stack": "Unity, Photon Fusion, C#, PlayFab",
        "description": "Built a cross-platform 4-player arena shooter with authoritative netcode.",
        "approach_list": ["Prototyped netcode in week 1", "Set up CI for iOS/Android builds"],
        "results_list": ["60 FPS on mid-range Android", "Sub-80ms perceived latency", "Shipped in 12 weeks"],
        "metrics_list": [{"label": "Latency", "value": "<80ms"}, {"label": "FPS", "value": "60"},
                         {"label": "Players", "value": "4 PvP"}, {"label": "Shipped", "value": "12 wks"}],
    }
    print(render_case_pdf(sample, ARTIFACT_DIR / "case_sample.pdf"))
    print(render_case_png(sample, ARTIFACT_DIR / "case_sample.png"))
