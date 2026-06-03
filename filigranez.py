#!/usr/bin/env python3
# -*- coding: utf-8 -*-
## Author : @3lDiDi

import argparse
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageFont
    import pdf2image
    import img2pdf
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install Pillow pdf2image img2pdf")
    sys.exit(1)


FONT_PATHS = [
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    # Windows
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/verdana.ttf",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    print(f"[!] Warning: no TrueType font found, falling back to default (text may appear small)")
    return ImageFont.load_default()


def make_watermark_layer(
    width: int, height: int, text: str, opacity: float, rotation: float, font_size: int
) -> Image.Image:
    """Return a tiled, rotated watermark as an RGBA layer matching the page size."""
    font = load_font(font_size)
    alpha = int(255 * opacity)

    # Large canvas to avoid cropping after rotation
    diag = int(math.sqrt(width ** 2 + height ** 2)) * 2
    canvas = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    spacing_x = tw + max(40, tw // 2)
    spacing_y = th + max(50, th * 3)

    for y in range(0, diag * 2, spacing_y):
        for x in range(0, diag * 2, spacing_x):
            draw.text((x - diag // 2, y - diag // 2), text, font=font, fill=(220, 20, 20, alpha))

    rotated = canvas.rotate(rotation, expand=False)

    cx = (diag - width) // 2
    cy = (diag - height) // 2
    return rotated.crop((cx, cy, cx + width, cy + height))


def watermark_page(
    page_image: Image.Image, text: str, opacity: float, rotation: float, font_size: int
) -> Image.Image:
    page = page_image.convert("RGBA")
    layer = make_watermark_layer(page.width, page.height, text, opacity, rotation, font_size)
    return Image.alpha_composite(page, layer).convert("RGB")


def watermark_pdf(
    input_path: Path,
    text: str,
    output_path: str,
    opacity: float,
    rotation: float,
    dpi: int,
    font_size: Optional[int],
    poppler_path: Optional[str] = None,
) -> None:
    print(f"[+] Loading  : {input_path}")

    kwargs = {"dpi": dpi}
    if poppler_path:
        kwargs["poppler_path"] = poppler_path

    pages = pdf2image.convert_from_path(str(input_path), **kwargs)
    print(f"[+] Pages    : {len(pages)}")
    print(f"[+] Opacity  : {int(opacity * 100)}%  |  Rotation: {rotation}°  |  DPI: {dpi}")

    watermarked = []
    for i, page in enumerate(pages, 1):
        auto_font = font_size or max(24, page.width // 18)
        print(f"    Page {i}/{len(pages)} ({page.width}x{page.height}px)")
        watermarked.append(watermark_page(page, text, opacity, rotation, auto_font))

    with tempfile.TemporaryDirectory() as tmpdir:
        img_paths = []
        for i, page in enumerate(watermarked):
            path = os.path.join(tmpdir, f"page_{i:04d}.jpg")
            page.save(path, "JPEG", quality=95)
            img_paths.append(path)

        with open(output_path, "wb") as f:
            f.write(img2pdf.convert(img_paths))

    print(f"[+] Output   : {output_path}")


def build_output_path(input_path: Path, output_arg: Optional[str], suffix: str) -> str:
    if output_arg:
        return output_arg
    return str(input_path.parent / f"{input_path.stem}_{suffix}.pdf")


def collect_pdfs(directory: Path) -> list:
    """Collect PDF files deduplicating case-insensitive matches (Windows NTFS safety)."""
    seen = set()
    pdfs = []
    for p in sorted(directory.rglob("*")):
        if p.suffix.lower() == ".pdf" and p.resolve() not in seen:
            seen.add(p.resolve())
            pdfs.append(p)
    return pdfs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Watermark PDF files — watermark baked into pixels, non-removable.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  filigranez.py doc.pdf "CONFIDENTIEL"
  filigranez.py doc.pdf "CONFIDENTIEL" out.pdf --opacity 0.3 --rotation 30
  filigranez.py ./docs/ "DRAFT" --dpi 300
  filigranez.py doc.pdf "DRAFT" --poppler-path "C:/poppler/bin"
        """,
    )
    parser.add_argument("input",  help="Input PDF file or directory")
    parser.add_argument("text",   help="Watermark text")
    parser.add_argument("output", nargs="?", help="Output PDF (single file mode only)")
    parser.add_argument("--opacity",      "-o", type=float, default=0.5,
                        metavar="FLOAT",  help="Opacity 0.0–1.0 (default: 0.5)")
    parser.add_argument("--rotation",     "-r", type=float, default=45,
                        metavar="DEG",    help="Rotation in degrees (default: 45)")
    parser.add_argument("--dpi",          "-d", type=int,   default=200,
                        metavar="INT",    help="Rendering DPI (default: 200)")
    parser.add_argument("--font-size",    "-f", type=int,   default=None,
                        metavar="INT",    help="Font size in pixels (default: auto)")
    parser.add_argument("--suffix-name",  "-s", type=str,   default="watermark",
                        metavar="SUFFIX", help="Suffix appended to filename (default: watermark)")
    parser.add_argument("--poppler-path", "-p", type=str,   default=None,
                        metavar="PATH",   help="Path to poppler bin directory (Windows)")

    args = parser.parse_args()

    if not 0.0 <= args.opacity <= 1.0:
        print("Error: --opacity must be between 0.0 and 1.0")
        sys.exit(1)

    input_path = Path(args.input)

    if input_path.is_dir():
        pdfs = collect_pdfs(input_path)
        if not pdfs:
            print(f"No PDF files found in {input_path}")
            sys.exit(1)
        output_dir = input_path.parent / f"{input_path.name}_{args.suffix_name}"
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[+] Output dir : {output_dir}")
        print(f"[+] Files found: {len(pdfs)}")
        for pdf in pdfs:
            print(f"\n{'='*50}")
            relative = pdf.relative_to(input_path)
            out = output_dir / relative.parent / f"{pdf.stem}_{args.suffix_name}.pdf"
            out.parent.mkdir(parents=True, exist_ok=True)
            watermark_pdf(pdf, args.text, str(out), args.opacity, args.rotation,
                          args.dpi, args.font_size, args.poppler_path)

    elif input_path.is_file():
        out = build_output_path(input_path, args.output, args.suffix_name)
        watermark_pdf(input_path, args.text, out, args.opacity, args.rotation,
                      args.dpi, args.font_size, args.poppler_path)

    else:
        print(f"Error: '{args.input}' is not a valid file or directory")
        sys.exit(1)


if __name__ == "__main__":
    main()
