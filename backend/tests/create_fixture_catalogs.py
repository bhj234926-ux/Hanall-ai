from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


def _draw_texture(pdf: canvas.Canvas, x: float, y: float, width: float, height: float, color: str, seed: int) -> None:
    base = HexColor(color)
    pdf.setFillColor(base)
    pdf.rect(x, y, width, height, fill=1, stroke=0)
    pdf.saveState()
    pdf.setStrokeColor(Color(max(base.red - .08, 0), max(base.green - .08, 0), max(base.blue - .08, 0), alpha=.6))
    pdf.setLineWidth(.55)
    gap = 8 + seed % 5
    for offset in range(-int(height), int(width + height), gap):
        pdf.line(x + offset, y, x + offset + height, y + height)
    pdf.restoreState()
    pdf.setStrokeColor(HexColor("#D9D7D0"))
    pdf.setLineWidth(.8)
    pdf.rect(x, y, width, height, fill=0, stroke=1)


def build_catalog(path: Path, brand: str, collection: str, pages: list[list[tuple[str, str]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=A4)
    page_width, page_height = A4
    for page_number, products in enumerate(pages, start=1):
        pdf.setFillColor(HexColor("#23352A"))
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(48, page_height - 52, brand)
        pdf.setFillColor(HexColor("#647269"))
        pdf.setFont("Helvetica", 10)
        pdf.drawString(48, page_height - 70, f"{collection} / MATERIAL SAMPLE GRID")
        cols = 3
        cell_w = 150
        sample_h = 138
        gap_x = 18
        start_x = 48
        start_y = page_height - 250
        row_gap = 208
        for index, (code, color) in enumerate(products):
            col = index % cols
            row = index // cols
            x = start_x + col * (cell_w + gap_x)
            y = start_y - row * row_gap
            _draw_texture(pdf, x, y, cell_w, sample_h, color, index + page_number)
            pdf.setFillColor(HexColor("#19261F"))
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawString(x, y - 19, code)
            pdf.setFillColor(HexColor("#77817A"))
            pdf.setFont("Helvetica", 7)
            pdf.drawString(x, y - 31, "WALLCOVERING / TEST SAMPLE")
        pdf.setFillColor(HexColor("#9AA19C"))
        pdf.setFont("Helvetica", 8)
        pdf.drawRightString(page_width - 45, 30, str(page_number))
        pdf.showPage()
    pdf.save()


def create_fixtures(output_dir: Path) -> list[Path]:
    first = output_dir / "gaenari_artbook.pdf"
    second = output_dir / "gaenari_story.pdf"
    build_catalog(
        first,
        "GAENARI WALLPAPER",
        "ARTBOOK",
        [
            [("57231-1", "#E7E1D7"), ("57231-2", "#CFC8BC"), ("57231-3", "#A9ADB0")],
            [("57232-1", "#EFECE4"), ("57232-2", "#C4B4A1")],
        ],
    )
    build_catalog(
        second,
        "GAENARI WALLPAPER",
        "STORY",
        [[("57231-1", "#E7E1D7"), ("25120-1", "#DDD2C1"), ("25120-2", "#B6AAA0")]],
    )
    return [first, second]


if __name__ == "__main__":
    create_fixtures(Path(__file__).resolve().parent / "fixtures")

