"""Create a self-contained campaign PDF used to test document upload."""

from pathlib import Path
from shutil import copy2

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "sample_data"
SOURCE_IMAGE = Path(
    r"C:\Users\Bhavik\.codex\generated_images\01a16df1-b815-7a21-aa4d-48fc5741e4a3"
    r"\exec-6ff8062b-ee10-4310-a054-1b9142099ade.png"
)
PRODUCT_IMAGE = SAMPLE_DIR / "fresh_start_product_mockup.png"
OUTPUT = SAMPLE_DIR / "campaign_pdf_with_image_and_link.pdf"
PURCHASE_URL = "https://www.example.com/products/fresh-start"


def draw_wrapped_text(pdf, text, x, y, max_width, font="Helvetica", size=10, leading=14):
    pdf.setFont(font, size)
    words = text.split()
    line = ""
    for word in words:
        proposal = f"{line} {word}".strip()
        if stringWidth(proposal, font, size) <= max_width:
            line = proposal
        else:
            pdf.drawString(x, y, line)
            y -= leading
            line = word
    if line:
        pdf.drawString(x, y, line)
        y -= leading
    return y


def main():
    SAMPLE_DIR.mkdir(exist_ok=True)
    if not SOURCE_IMAGE.exists():
        raise FileNotFoundError(f"Generated product image was not found: {SOURCE_IMAGE}")
    copy2(SOURCE_IMAGE, PRODUCT_IMAGE)

    pdf = canvas.Canvas(str(OUTPUT), pagesize=A4)
    width, height = A4
    margin = 46

    pdf.setFillColor(colors.HexColor("#123245"))
    pdf.rect(0, height - 112, width, 112, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(margin, height - 58, "Fresh Start oral care campaign")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(margin, height - 80, "Sample source document for Marketing Campaign Studio")

    image_width = width - (2 * margin)
    image_height = 245
    pdf.drawImage(
        ImageReader(str(PRODUCT_IMAGE)), margin, height - 385,
        width=image_width, height=image_height, preserveAspectRatio=True,
        anchor="c", mask="auto",
    )

    y = height - 416
    pdf.setFillColor(colors.HexColor("#123245"))
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin, y, "Campaign brief")
    y -= 22
    pdf.setFillColor(colors.HexColor("#173042"))
    y = draw_wrapped_text(
        pdf,
        "Fresh Start toothpaste is available in fluoride and sensitive-care variants. "
        "The campaign invites adults to explore an option that suits their everyday oral-care routine. "
        "Use a helpful, clear tone and avoid clinical claims, discounts, guarantees, or invented product facts.",
        margin, y, width - (2 * margin),
    )

    y -= 8
    pdf.setFillColor(colors.HexColor("#123245"))
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin, y, "Purchase link")
    y -= 23
    link_label = "Explore the Fresh Start range"
    pdf.setFillColor(colors.HexColor("#156F6A"))
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(margin, y, link_label)
    label_width = stringWidth(link_label, "Helvetica-Bold", 12)
    pdf.line(margin, y - 2, margin + label_width, y - 2)
    pdf.linkURL(PURCHASE_URL, (margin, y - 5, margin + label_width, y + 14), relative=0)
    y -= 20
    pdf.setFillColor(colors.HexColor("#52616B"))
    pdf.setFont("Helvetica-Oblique", 8.5)
    pdf.drawString(margin, y, f"Test URL: {PURCHASE_URL}")

    pdf.setFillColor(colors.HexColor("#52616B"))
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(width - margin, 28, "Sample only - example.com is not a live store")
    pdf.save()
    print(OUTPUT)


if __name__ == "__main__":
    main()
