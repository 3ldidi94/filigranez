#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# filigranez — PDF watermarking tool
# Copyright (C) 2026 @3lDiDi
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# https://www.gnu.org/licenses/gpl-3.0.txt

import argparse
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageColor, ImageDraw, ImageFont
    import pdf2image
    import img2pdf
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install Pillow pdf2image img2pdf")
    sys.exit(1)

try:
    import colorama
    colorama.init(autoreset=True)
except ImportError:
    pass  # colorama is optional — ANSI codes work natively on Linux/Mac

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False  # progress bars are optional; falls back to per-page lines


class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    BLUE   = "\033[34m"
    CYAN   = "\033[36m"
    WHITE  = "\033[97m"
    RED    = "\033[31m"

def _out(text: str = "") -> None:
    # Route through tqdm.write when bars are active so they aren't clobbered.
    if HAS_TQDM:
        tqdm.write(text)
    else:
        print(text)

def info(msg: str)  -> None: _out(f"{C.GREEN}{C.BOLD}[+]{C.RESET} {msg}")
def warn(msg: str)  -> None: _out(f"{C.YELLOW}{C.BOLD}[!]{C.RESET} {C.YELLOW}{msg}{C.RESET}")
def error(msg: str) -> None: _out(f"{C.RED}{C.BOLD}[✗]{C.RESET} {C.RED}{msg}{C.RESET}")
def step(msg: str)  -> None: _out(f"    {C.CYAN}›{C.RESET} {msg}")
def sep()           -> None: _out(f"{C.DIM}{'=' * 50}{C.RESET}")


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
    warn("No TrueType font found, falling back to default")
    try:
        # Pillow >= 10 honours size for the built-in font; older versions don't.
        return ImageFont.load_default(size=size)
    except TypeError:
        warn("Pillow < 10: default font ignores size, text may appear small")
        return ImageFont.load_default()


def make_watermark_layer(
    width: int, height: int, text: str, opacity: float, rotation: float,
    font_size: int, color: tuple,
) -> Image.Image:
    font = load_font(font_size)
    alpha = int(255 * opacity)
    fill = (*color, alpha)

    # Canvas just large enough to cover the page after any rotation: the
    # diagonal is the minimum size, plus a small margin so tiling reaches the
    # corners. Avoids the previous ×2 blow-up (4× memory, PIL bomb errors).
    diag = int(math.sqrt(width ** 2 + height ** 2)) + 2 * font_size
    canvas = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    spacing_x = tw + max(40, tw // 2)
    spacing_y = th + max(50, th * 3)

    for base_y in range(-spacing_y, diag + spacing_y, spacing_y):
        for base_x in range(-spacing_x, diag + spacing_x, spacing_x):
            draw.text((base_x, base_y), text, font=font, fill=fill)

    rotated = canvas.rotate(rotation, expand=False, resample=Image.BICUBIC)
    cx, cy = (diag - width) // 2, (diag - height) // 2
    return rotated.crop((cx, cy, cx + width, cy + height))


def watermark_page(
    page_image: Image.Image, text: str, opacity: float, rotation: float,
    font_size: int, color: tuple,
) -> Image.Image:
    page = page_image.convert("RGBA")
    layer = make_watermark_layer(
        page.width, page.height, text, opacity, rotation, font_size, color)
    return Image.alpha_composite(page, layer).convert("RGB")


def watermark_pdf(
    input_path: Path,
    text: str,
    output_path: str,
    opacity: float,
    rotation: float,
    dpi: int,
    font_size: Optional[int],
    color: tuple,
    quality: int,
    poppler_path: Optional[str] = None,
) -> None:
    info(f"Loading  : {C.WHITE}{C.BOLD}{input_path}{C.RESET}")

    kwargs = {"dpi": dpi}
    if poppler_path:
        kwargs["poppler_path"] = poppler_path

    try:
        pages = pdf2image.convert_from_path(str(input_path), **kwargs)
    except pdf2image.exceptions.PDFInfoNotInstalledError:
        raise RuntimeError(
            "poppler not found. Install it (e.g. 'apt install poppler-utils') "
            "or pass --poppler-path to its bin directory."
        )
    except (pdf2image.exceptions.PDFPageCountError,
            pdf2image.exceptions.PDFSyntaxError) as e:
        raise RuntimeError(f"cannot read PDF ({e})")
    info(f"Pages    : {C.WHITE}{C.BOLD}{len(pages)}{C.RESET}")
    info(f"Opacity  : {C.WHITE}{C.BOLD}{int(opacity * 100)}%{C.RESET}  │  "
         f"Rotation: {C.WHITE}{C.BOLD}{rotation}°{C.RESET}  │  "
         f"DPI: {C.WHITE}{C.BOLD}{dpi}{C.RESET}")

    watermarked = []
    page_iter = enumerate(pages, 1)
    if HAS_TQDM:
        page_iter = tqdm(page_iter, total=len(pages), desc="  Pages",
                         unit="p", leave=False, position=1)
    for i, page in page_iter:
        auto_font = font_size or max(24, page.width // 18)
        if not HAS_TQDM:
            step(f"Page {C.WHITE}{C.BOLD}{i}/{len(pages)}{C.RESET} "
                 f"{C.DIM}({page.width}x{page.height}px){C.RESET}")
        watermarked.append(
            watermark_page(page, text, opacity, rotation, auto_font, color))

    with tempfile.TemporaryDirectory() as tmpdir:
        img_paths = []
        for i, page in enumerate(watermarked):
            path = os.path.join(tmpdir, f"page_{i:04d}.jpg")
            # Embed the DPI so img2pdf sizes the PDF page correctly. Without it
            # img2pdf assumes 96 DPI and pages come out physically oversized.
            page.save(path, "JPEG", quality=quality, dpi=(dpi, dpi))
            img_paths.append(path)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(img2pdf.convert(img_paths))

    info(f"Output   : {C.BLUE}{C.BOLD}{output_path}{C.RESET}")


def build_output_path(input_path: Path, output_arg: Optional[str], suffix: str) -> str:
    if output_arg:
        return output_arg
    return str(input_path.parent / f"{input_path.stem}_{suffix}.pdf")


def collect_pdfs(directory: Path) -> list:
    """Collect .pdf files recursively, deduplicating by resolved path (handles
    symlinks and, on case-insensitive filesystems, case-variant duplicates)."""
    seen = set()
    pdfs = []
    for p in sorted(directory.rglob("*")):
        if p.is_file() and p.suffix.lower() == ".pdf" and p.resolve() not in seen:
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
    parser.add_argument("--color",        "-c", type=str,   default="#DC1414",
                        metavar="COLOR",  help="Text color: #RRGGBB or name (default: #DC1414)")
    parser.add_argument("--quality",      "-q", type=int,   default=95,
                        metavar="INT",    help="JPEG quality 1–95 (default: 95)")
    parser.add_argument("--suffix-name",  "-s", type=str,   default="watermark",
                        metavar="SUFFIX", help="Suffix appended to filename (default: watermark)")
    parser.add_argument("--poppler-path", "-p", type=str,   default=None,
                        metavar="PATH",   help="Path to poppler bin directory (Windows)")

    args = parser.parse_args()

    if not 0.0 <= args.opacity <= 1.0:
        error("--opacity must be between 0.0 and 1.0")
        sys.exit(1)
    if args.dpi <= 0:
        error("--dpi must be a positive integer")
        sys.exit(1)
    if args.font_size is not None and args.font_size <= 0:
        error("--font-size must be a positive integer")
        sys.exit(1)
    if not args.text.strip():
        error("watermark text must not be empty")
        sys.exit(1)
    if not 1 <= args.quality <= 95:
        error("--quality must be between 1 and 95")
        sys.exit(1)
    try:
        color = ImageColor.getrgb(args.color)[:3]
    except ValueError:
        error(f"invalid --color '{args.color}' (use #RRGGBB or a color name)")
        sys.exit(1)

    input_path = Path(args.input)

    if input_path.is_dir():
        if args.output:
            warn("output argument is ignored in directory mode")
        pdfs = collect_pdfs(input_path)
        if not pdfs:
            error(f"No PDF files found in {input_path}")
            sys.exit(1)
        output_dir = input_path.parent / f"{input_path.name}_{args.suffix_name}"
        output_dir.mkdir(parents=True, exist_ok=True)
        info(f"Output dir : {C.BLUE}{C.BOLD}{output_dir}{C.RESET}")
        info(f"Files found: {C.WHITE}{C.BOLD}{len(pdfs)}{C.RESET}")
        failures = 0
        pdf_iter = tqdm(pdfs, desc="Files", unit="f", position=0) if HAS_TQDM else pdfs
        for pdf in pdf_iter:
            _out()
            sep()
            relative = pdf.relative_to(input_path)
            out = output_dir / relative.parent / f"{pdf.stem}_{args.suffix_name}.pdf"
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                watermark_pdf(pdf, args.text, str(out), args.opacity, args.rotation,
                              args.dpi, args.font_size, color, args.quality,
                              args.poppler_path)
            except RuntimeError as e:
                error(f"Skipping {pdf}: {e}")
                failures += 1
        if failures:
            _out()
            warn(f"{failures}/{len(pdfs)} file(s) failed")
            sys.exit(1)

    elif input_path.is_file():
        out = build_output_path(input_path, args.output, args.suffix_name)
        if Path(out).resolve() == input_path.resolve():
            error("output path is the same as the input; refusing to overwrite")
            sys.exit(1)
        try:
            watermark_pdf(input_path, args.text, out, args.opacity, args.rotation,
                          args.dpi, args.font_size, color, args.quality,
                          args.poppler_path)
        except RuntimeError as e:
            error(str(e))
            sys.exit(1)

    else:
        error(f"'{args.input}' is not a valid file or directory")
        sys.exit(1)


if __name__ == "__main__":
    main()
