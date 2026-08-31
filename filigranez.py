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
import random
import sys
import tempfile
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:
    from PIL import (Image, ImageChops, ImageColor, ImageDraw, ImageFilter,
                     ImageFont)
    import pdf2image
    import img2pdf
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install Pillow pdf2image img2pdf")
    sys.exit(1)

try:
    import colorama
    colorama.init(autoreset=True)
    HAS_COLORAMA = True
except ImportError:
    HAS_COLORAMA = False  # optional — ANSI works natively on Linux/Mac

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False  # progress bars are optional; falls back to per-page lines


def _isatty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:
        return False  # a stream that cannot answer is treated as a file


def _encodable(text: str) -> bool:
    """Whether the console can actually render `text`. A Windows console left
    on a legacy code page, or any terminal under LC_ALL=C, raises on box
    drawing and block characters instead of printing them."""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):          # no-color.org convention
        return False
    if not _isatty(sys.stdout):             # keep escape codes out of pipes
        return False
    if os.name == "nt" and not HAS_COLORAMA:
        # Without colorama, ANSI only works on a VT-capable Windows console.
        return bool(os.environ.get("WT_SESSION") or os.environ.get("ANSICON")
                    or os.environ.get("TERM"))
    return True


UNICODE_OK = _encodable("│›✗")
BARS_OK = HAS_TQDM and _isatty(sys.stderr)
LAYER_SHARE = 0.85   # of a page's progress, spent building the watermark layer

# Plain substitutes so the layout survives a console that cannot encode these.
SEP_CHAR  = "│" if UNICODE_OK else "|"
DEG_CHAR  = "°" if _encodable("°") else " deg"
STEP_CHAR = "›" if UNICODE_OK else ">"
FAIL_CHAR = "✗" if UNICODE_OK else "x"


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


if not _color_enabled():
    for _name in [n for n in vars(C) if not n.startswith("_")]:
        setattr(C, _name, "")

def _out(text: str = "") -> None:
    # Route through tqdm.write while a bar is live so it isn't clobbered.
    if BARS_OK:
        tqdm.write(text)
    else:
        print(text)

def info(msg: str)  -> None: _out(f"{C.GREEN}{C.BOLD}[+]{C.RESET} {msg}")
def warn(msg: str)  -> None: _out(f"{C.YELLOW}{C.BOLD}[!]{C.RESET} {C.YELLOW}{msg}{C.RESET}")
def error(msg: str) -> None: _out(f"{C.RED}{C.BOLD}[{FAIL_CHAR}]{C.RESET} {C.RED}{msg}{C.RESET}")
def step(msg: str)  -> None: _out(f"    {C.CYAN}{STEP_CHAR}{C.RESET} {msg}")
def sep()           -> None: _out(f"{C.DIM}{'=' * 50}{C.RESET}")


def make_bar(iterable=None, desc: str = "", unit: str = "it",
             total: Optional[int] = None, nested: bool = False,
             leave: bool = False, counter: bool = True):
    """A progress bar, or None (or the bare iterable) when bars would be
    wrong: no tqdm, or output redirected to a file or pipe.

    ascii= falls back to '#' when the console cannot encode block characters,
    and dynamic_ncols lets the bar follow a terminal that gets resized.
    position is only set for a bar nested under another one: on close tqdm
    emits the carriage return ending its blanking spaces only for position 0,
    so claiming a position with nothing above strands the cursor past them.
    counter=False drops the n/total field, for a bar advanced by fractions of
    a unit where a rounded count would read as done before it is."""
    if not BARS_OK:
        return iterable
    count = " {n_fmt}/{total_fmt}" if counter else ""
    opts = {"desc": desc, "unit": unit, "leave": leave, "dynamic_ncols": True,
            "ascii": not _encodable("█"),
            "bar_format": "  {desc} {percentage:3.0f}%|{bar}|" + count +
                          " [{elapsed}<{remaining}]"}
    if total is not None:
        opts["total"] = total
    if nested:
        opts["position"] = 1
    return tqdm(iterable, **opts) if iterable is not None else tqdm(**opts)


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


# Presets for --gouv mode, matched against filigrane.beta.gouv.fr output:
# staggered brick tiling, ~25° slope, translucent grey with a few tiles in a
# muted red, wide vertical spacing, small horizontal gap.
GOUV_ROTATION     = 25.0
GOUV_OPACITY      = 0.65
GOUV_COLOR        = "#8D8D8D"       # the light grey of the four-ink palette
# Each ink is fitted so that a row drawn with it composites onto white at the
# value measured on the reference's rows: dark 110, light grey 169, navy
# (123,122,141), red (205,138,140). Fitted through a JPEG round-trip, because
# chroma subsampling washes colour out — a red picked on the raw layer comes
# out about half as saturated once encoded.
GOUV_ACCENT_RED   = (231, 77, 81)
GOUV_ACCENT_NAVY  = (71, 71, 119)
GOUV_INK_DARK     = (56, 56, 56)
# Four inks, one per row, dealt so that every four consecutive rows carry each
# of them exactly once in a random order. The reference's greys are bimodal —
# its rows land near 100 or near 170, never in between — so the light and dark
# greys are two inks rather than one ink at two opacities. None is the
# requested --color, which is the light grey by default.
GOUV_ROW_COLORS   = (None, GOUV_INK_DARK, GOUV_ACCENT_NAVY, GOUV_ACCENT_RED)
# Geometry read off a blank-page sample at 148 dpi (page 1224px wide): rows
# 263px apart, repetitions 478px long on a 504px pitch — so the gap between
# repetitions is one font size, and the font itself is page width / 45.
GOUV_LINE_FACTOR  = 9.74            # vertical period as a multiple of font size
# Each row rides a sine rather than running straight. Fitting the reference's
# baselines (glyph bias removed) gives one cycle per repetition, explaining
# 94-97% of their shape, with the amplitude redrawn for every row.
GOUV_WAVE_AMP_MIN = 0.08            # per-row amplitude, as a fraction of font size
GOUV_WAVE_AMP_MAX = 0.38
GOUV_WAVE_STEPS   = 96              # mesh columns across a row
# Rows still vary in weight, but only slightly: the light/dark split is carried
# by the inks above, and a wide opacity spread would blur one into the other.
GOUV_ROW_ALPHA_MIN = 0.90
GOUV_ROW_ALPHA_MAX = 1.10
# The reference's glyphs are visibly grainy while its halo is smooth, which is
# what applying the speckle to the ink *before* the blur produces.
GOUV_GRAIN        = 0.16            # relative spread of the per-pixel ink alpha
# The reference does not print crisp text: each repetition sits in a soft halo
# that roughly doubles its visual weight. Not a JPEG artifact — re-encoding
# clean text down to the reference's 148 dpi / 3.5% ratio gets nowhere near it.
# Radius as a fraction of the font size, fitted so the halo covers the same
# area relative to the ink as the reference does (6.1x).
GOUV_GLOW_RADIUS  = 0.48
GOUV_FONT_DIV     = 45              # auto font size: page width / 45
CLASSIC_FONT_DIV  = 18


# The reference sets its watermark in an Arial-metric face, not in DejaVu:
# at the size that matches its 478px repetition, Liberation Sans lands within
# 3px while DejaVu needs a size small enough to shrink the glyphs. Preferred
# for --gouv only; the classic style keeps its original font order untouched.
GOUV_FONT_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


@lru_cache(maxsize=8)
def load_font(size: int, style: str = "classic") -> ImageFont.FreeTypeFont:
    paths = GOUV_FONT_PATHS + FONT_PATHS if style == "gouv" else FONT_PATHS
    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    warn("No TrueType font found, falling back to default")
    try:
        # Pillow >= 10 honours size for the built-in font; older versions don't.
        return ImageFont.load_default(size=size)
    except TypeError:
        warn("Pillow < 10: default font ignores size, text may appear small")
        return ImageFont.load_default()


A4_POINTS = (595.276, 841.89)


def _fit_a4(page: Image.Image, dpi: int) -> Image.Image:
    """Centre the page on an A4 sheet of the same orientation, scaled to fit,
    with white margins. Nothing is cropped and the aspect ratio is kept, so a
    Letter page comes out as A4 with a band top and bottom rather than
    stretched. Applied before the watermark, so the margins get covered too."""
    w_pt, h_pt = A4_POINTS
    if page.width > page.height:
        w_pt, h_pt = h_pt, w_pt
    sheet_w = max(1, round(w_pt / 72 * dpi))
    sheet_h = max(1, round(h_pt / 72 * dpi))
    # A page already A4 is left alone: rounding the sheet to whole pixels can
    # differ from the render by a pixel, and resampling over that would soften
    # the page for nothing.
    if (abs(page.width - sheet_w) <= 2 and abs(page.height - sheet_h) <= 2):
        return page
    scale = min(sheet_w / page.width, sheet_h / page.height)
    new_w = max(1, round(page.width * scale))
    new_h = max(1, round(page.height * scale))
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (255, 255, 255, 255))
    sheet.paste(page.resize((new_w, new_h), Image.LANCZOS),
                ((sheet_w - new_w) // 2, (sheet_h - new_h) // 2))
    return sheet


def _grain(strip: Image.Image, amount: float) -> Image.Image:
    """Speckle the ink by jittering its alpha per pixel. Multiplying rather
    than adding keeps the transparent background untouched, so only drawn
    pixels break up. The mean is pulled down by `amount`, which the calibrated
    row opacities already account for."""
    noise = Image.effect_noise(strip.size, 48)      # gaussian, mean 128, sd 48
    scale = amount * 255 / 48
    base = 255 * (1 - amount)
    mult = noise.point(
        lambda v: max(0, min(255, int(base + (v - 128) * scale))))
    strip.putalpha(ImageChops.multiply(strip.getchannel("A"), mult))
    return strip


def _wave_strip(strip: Image.Image, amp: float, period: float, phase: float,
                x0: int) -> Image.Image:
    """Displace the strip's columns along a sine of the given amplitude and
    period. `x0` is the strip's own offset on the canvas, so the wave stays a
    function of canvas position and neighbouring rows never share a phase.

    A mesh warp keeps this to one C-level call. The wave is shallow enough
    (its steepest slope is a few degrees) that shifting columns reads as a
    curve without having to rotate each glyph onto the tangent."""
    w, h = strip.size
    k = 2 * math.pi / period
    mesh = []
    for i in range(GOUV_WAVE_STEPS):
        xa, xb = w * i // GOUV_WAVE_STEPS, w * (i + 1) // GOUV_WAVE_STEPS
        if xb <= xa:
            continue
        da = amp * math.sin(k * (xa + x0) + phase)
        db = amp * math.sin(k * (xb + x0) + phase)
        mesh.append(((xa, 0, xb, h),
                     (xa, -da, xa, h - da, xb, h - db, xb, -db)))
    if not mesh:
        return strip
    return strip.transform((w, h), Image.MESH, mesh, resample=Image.BILINEAR)


def _composite_at(canvas: Image.Image, tile: Image.Image, x: int, y: int) -> None:
    """Alpha-composite a tile at (x, y), clipping whatever falls outside.
    Image.alpha_composite rejects negative destinations, so edge tiles are
    cropped rather than skipped — otherwise the margins would stay bare."""
    sx, sy = max(0, -x), max(0, -y)
    dx, dy = max(0, x), max(0, y)
    w = min(tile.width - sx, canvas.width - dx)
    h = min(tile.height - sy, canvas.height - dy)
    if w <= 0 or h <= 0:
        return
    if (sx, sy, w, h) != (0, 0, tile.width, tile.height):
        tile = tile.crop((sx, sy, sx + w, sy + h))
    canvas.alpha_composite(tile, (dx, dy))


def make_watermark_layer(
    width: int, height: int, text: str, opacity: float, rotation: float,
    font_size: int, color: tuple, style: str = "classic",
    on_row=None,
) -> Image.Image:
    """`on_row(done, total)`, if given, is called after each row of the tiling.
    Building this layer is the bulk of the work on a page, so it is what a
    progress bar has to follow to move at all on a one-page document."""
    font = load_font(font_size, style)
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

    if style == "gouv":
        spacing_x = tw + font_size
        spacing_y = max(th + 50, int(font_size * GOUV_LINE_FACTOR))
        stagger = spacing_x // 2
        # Seeded from the text so the colour order, row weights and waves
        # differ between one watermark and the next, yet a given text always
        # renders identically. crc32 rather than hash(), which is salted
        # per process and would change the output from one run to the next.
        rng = random.Random(zlib.crc32(text.encode("utf-8")))
    else:
        spacing_x = tw + max(40, tw // 2)
        spacing_y = th + max(50, th * 3)
        stagger = 0
        rng = None

    rows = range(-spacing_y, diag + spacing_y, spacing_y)
    n_rows = len(rows)

    if rng is None:
        for done, base_y in enumerate(rows, 1):
            for base_x in range(-spacing_x, diag + spacing_x, spacing_x):
                draw.text((base_x, base_y), text, font=font, fill=fill)
            if on_row:
                on_row(done, n_rows)
    else:
        # A row is drawn whole, then bent along one continuous sine running the
        # length of the canvas. Because the wave never restarts between
        # repetitions, a cut, splice or patch anywhere in the page breaks the
        # curve and shows up as a step in an otherwise smooth line.
        glow = font_size * GOUV_GLOW_RADIUS
        gpad = int(glow * 3) + 1 if glow else 0
        text_w, text_h = bbox[2] + 1, bbox[3] + 1
        # One random permutation of the four inks, then cycled. Reshuffling at
        # each cycle would let a colour repeat across the seam and leave
        # another out of a four-row window; keeping every such window complete
        # forces the order to repeat with a period of four, so the order is
        # what gets drawn, once.
        order = list(GOUV_ROW_COLORS)
        rng.shuffle(order)
        for row, base_y in enumerate(rows):
            x_start = -spacing_x - (stagger if row % 2 else 0)
            x_stop = diag + spacing_x
            amp = rng.uniform(GOUV_WAVE_AMP_MIN, GOUV_WAVE_AMP_MAX) * font_size
            phase = rng.uniform(0, 2 * math.pi)
            apad = int(math.ceil(amp)) + 1
            # every row prints at its own weight, never so faint as to vanish
            row_alpha = min(255, int(alpha * rng.uniform(GOUV_ROW_ALPHA_MIN,
                                                         GOUV_ROW_ALPHA_MAX)))
            # Colour is a property of the whole row: in the reference every
            # repetition on a line shares it exactly, and lines are never mixed.
            row_fill = (*(order[row % len(order)] or color), row_alpha)

            strip = Image.new("RGBA",
                              (x_stop - x_start + 2 * gpad + text_w,
                               text_h + 2 * gpad + 2 * apad), (0, 0, 0, 0))
            sdraw = ImageDraw.Draw(strip)
            for base_x in range(x_start, x_stop, spacing_x):
                sdraw.text((base_x - x_start + gpad, gpad + apad),
                           text, font=font, fill=row_fill)
            if GOUV_GRAIN:
                strip = _grain(strip, GOUV_GRAIN)
            if glow:
                strip = Image.alpha_composite(
                    strip.filter(ImageFilter.GaussianBlur(glow)), strip)
            # one cycle per repetition, as measured, not per repetition pitch
            strip = _wave_strip(strip, amp, text_w, phase, x_start - gpad)
            if on_row:
                on_row(row + 1, n_rows)
            _composite_at(canvas, strip,
                          x_start - gpad, base_y - gpad - apad)

    rotated = canvas.rotate(rotation, expand=False, resample=Image.BICUBIC)
    cx, cy = (diag - width) // 2, (diag - height) // 2
    return rotated.crop((cx, cy, cx + width, cy + height))


def watermark_page(
    page_image: Image.Image, text: str, opacity: float, rotation: float,
    font_size: int, color: tuple, style: str = "classic",
) -> Image.Image:
    page = page_image.convert("RGBA")
    layer = make_watermark_layer(
        page.width, page.height, text, opacity, rotation, font_size, color, style)
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
    style: str = "classic",
    page_size: str = "keep",
    nested: bool = False,
) -> None:
    info(f"Loading  : {C.WHITE}{C.BOLD}{input_path}{C.RESET}")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Render pages to files and open them one at a time: memory stays at
        # ~one page instead of the whole document.
        kwargs = {"dpi": dpi, "output_folder": tmpdir,
                  "paths_only": True, "fmt": "ppm"}
        if poppler_path:
            kwargs["poppler_path"] = poppler_path

        try:
            page_paths = pdf2image.convert_from_path(str(input_path), **kwargs)
        except pdf2image.exceptions.PDFInfoNotInstalledError:
            raise RuntimeError(
                "poppler not found. Install it (e.g. 'apt install poppler-utils') "
                "or pass --poppler-path to its bin directory."
            )
        except (pdf2image.exceptions.PDFPageCountError,
                pdf2image.exceptions.PDFSyntaxError) as e:
            raise RuntimeError(f"cannot read PDF ({e})")
        info(f"Pages    : {C.WHITE}{C.BOLD}{len(page_paths)}{C.RESET}")
        info(f"Opacity  : {C.WHITE}{C.BOLD}{int(opacity * 100)}%{C.RESET}"
             f"  {SEP_CHAR}  Rotation: {C.WHITE}{C.BOLD}{rotation}{DEG_CHAR}{C.RESET}"
             f"  {SEP_CHAR}  DPI: {C.WHITE}{C.BOLD}{dpi}{C.RESET}")

        font_div = GOUV_FONT_DIV if style == "gouv" else CLASSIC_FONT_DIV
        layer_cache = {}
        img_paths = []
        # A page is worth 1 on the bar, most of it spent drawing the watermark
        # layer, so that share is handed out row by row while it is built.
        # Without that a one-page document sits at 0 until it is simply done.
        n_pages = len(page_paths)
        bar = make_bar(desc=f"Page 1/{n_pages}", unit="pg", total=n_pages,
                       nested=nested, counter=False)
        for i, page_path in enumerate(page_paths, 1):
            if bar is not None:
                bar.set_description_str(f"Page {i}/{n_pages}")
            with Image.open(page_path) as rendered:
                page = rendered.convert("RGBA")
            if page_size == "a4":
                page = _fit_a4(page, dpi)
            auto_font = font_size or max(24, page.width // font_div)
            if bar is None:   # no bar to watch, so log each page instead
                step(f"Page {C.WHITE}{C.BOLD}{i}/{n_pages}{C.RESET} "
                     f"{C.DIM}({page.width}x{page.height}px){C.RESET}")
            # Pages of identical size reuse the same layer instead of
            # re-drawing and re-rotating it for every page.
            key = (page.width, page.height, auto_font)
            layer = layer_cache.get(key)
            if layer is None:
                def tick(done: int, total: int) -> None:
                    if bar is not None and total:
                        bar.update(LAYER_SHARE / total)
                layer = make_watermark_layer(
                    page.width, page.height, text, opacity, rotation,
                    auto_font, color, style, on_row=tick)
                layer_cache[key] = layer
            elif bar is not None:
                bar.update(LAYER_SHARE)   # reused layer: that work is free
            result = Image.alpha_composite(page, layer).convert("RGB")

            out_jpg = os.path.join(tmpdir, f"page_{i:04d}.jpg")
            # Embed the DPI so img2pdf sizes the PDF page correctly. Without it
            # img2pdf assumes 96 DPI and pages come out physically oversized.
            result.save(out_jpg, "JPEG", quality=quality, dpi=(dpi, dpi))
            img_paths.append(out_jpg)
            os.remove(page_path)  # free the uncompressed render early
            if bar is not None:
                bar.update(1.0 - LAYER_SHARE)

        if bar is not None:
            # rounding on the fractional updates can leave it a hair short
            bar.n = bar.total
            bar.refresh()
            bar.close()

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
  filigranez.py doc.pdf "Document destiné à la location" --gouv
  filigranez.py doc.pdf "DRAFT" --poppler-path "C:/poppler/bin"
        """,
    )
    parser.add_argument("input",  help="Input PDF file or directory")
    parser.add_argument("text",   help="Watermark text")
    parser.add_argument("output", nargs="?", help="Output PDF (single file mode only)")
    parser.add_argument("--gouv",         "-g", action="store_true",
                        help="filigrane.beta.gouv.fr style: dense staggered grey "
                             "tiling at 25° with a few pale-red repetitions "
                             "(--opacity/--rotation/--color/--font-size still override)")
    parser.add_argument("--opacity",      "-o", type=float, default=None,
                        metavar="FLOAT",  help="Opacity 0.0–1.0 (default: 0.5)")
    parser.add_argument("--rotation",     "-r", type=float, default=None,
                        metavar="DEG",    help="Rotation in degrees (default: 45, gouv: 25)")
    parser.add_argument("--dpi",          "-d", type=int,   default=200,
                        metavar="INT",    help="Rendering DPI (default: 200)")
    parser.add_argument("--font-size",    "-f", type=int,   default=None,
                        metavar="INT",    help="Font size in pixels (default: auto)")
    parser.add_argument("--color",        "-c", type=str,   default=None,
                        metavar="COLOR",  help="Text color: #RRGGBB or name "
                                              "(default: #DC1414, gouv: #505050)")
    parser.add_argument("--page-size",    "-P", choices=("keep", "a4"), default="keep",
                        help="Output page size: 'keep' preserves the input page size, "
                             "'a4' normalises every page to A4 (scaled to fit, "
                             "centred, never stretched or cropped) (default: keep)")
    parser.add_argument("--quality",      "-q", type=int,   default=95,
                        metavar="INT",    help="JPEG quality 1–95 (default: 95)")
    parser.add_argument("--suffix-name",  "-s", type=str,   default="watermark",
                        metavar="SUFFIX", help="Suffix appended to filename (default: watermark)")
    parser.add_argument("--poppler-path", "-p", type=str,   default=None,
                        metavar="PATH",   help="Path to poppler bin directory (Windows)")

    args = parser.parse_args()

    # Per-style defaults; explicit flags always win, in either style.
    style = "gouv" if args.gouv else "classic"
    if args.opacity is None:
        args.opacity = GOUV_OPACITY if args.gouv else 0.5
    if args.rotation is None:
        args.rotation = GOUV_ROTATION if args.gouv else 45.0
    if args.color is None:
        args.color = GOUV_COLOR if args.gouv else "#DC1414"

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
        pdf_iter = make_bar(pdfs, desc="Files", unit="f", total=len(pdfs),
                            leave=True)
        for pdf in pdf_iter:
            _out()
            sep()
            relative = pdf.relative_to(input_path)
            out = output_dir / relative.parent / f"{pdf.stem}_{args.suffix_name}.pdf"
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                watermark_pdf(pdf, args.text, str(out), args.opacity, args.rotation,
                              args.dpi, args.font_size, color, args.quality,
                              args.poppler_path, style, args.page_size,
                              nested=True)
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
                          args.poppler_path, style, args.page_size)
        except RuntimeError as e:
            error(str(e))
            sys.exit(1)

    else:
        error(f"'{args.input}' is not a valid file or directory")
        sys.exit(1)


if __name__ == "__main__":
    main()
