#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# filigranez - web front-end (thin layer over the CLI core)
# Copyright (C) 2026 @3lDiDi - GPL v3, see ../LICENSE
"""A single-page Flask front-end for filigranez.

It imports the CLI module and reuses resolve_params()/watermark_pdf() unchanged,
so the two share one source of truth and never drift. Uploads are processed in a
throwaway temp dir and streamed back; nothing is kept on disk."""

import io
import math
import os
import re
import sys
import tempfile
import zipfile

import pdf2image
from pathlib import Path

from flask import (Flask, jsonify, render_template, request,
                   send_file)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

# The core lives one level up, at the repo root, and stays there (it is the
# product; the web layer is the add-on). Works both in Docker, where
# filigranez.py sits next to /app/web, and when run straight from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import filigranez as fz  # noqa: E402

MAX_MB = 100
MAX_FILES = 50            # per request
MAX_DPI = 600             # raster size guard (DoS / decompression bomb)
MAX_FONT_SIZE = 2000
MAX_PAGES = 200           # per file (DoS: huge page counts tie up a worker)
MAX_SIDE_PX = 6000        # cap raster side (DoS: crafted giant MediaBox)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

# Honour a reverse proxy: X-Forwarded-Prefix lets the app live under a
# sub-path (e.g. nginx `location /filigranez/`) and still build correct
# links via url_for; X-Forwarded-Proto keeps redirects on https. No effect
# when the headers are absent, so running at the root or as the dev server
# behaves exactly as before.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1,
                        x_prefix=1)


@app.after_request
def _security_headers(resp):
    # Defence in depth (works even without the reverse proxy). No 'unsafe-eval'
    # in the CSP, which also blocks the pdf.js eval-based attack path.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "worker-src 'self' blob:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'")
    return resp

# Defaults surfaced to the form, read from the module so they can never fall out
# of step with the CLI.
CLASSIC = {"opacity": fz.CLASSIC_OPACITY, "rotation": fz.CLASSIC_ROTATION,
           "color": fz.CLASSIC_COLOR}
GOUV = {"opacity": fz.GOUV_OPACITY, "rotation": fz.GOUV_ROTATION,
        "color": fz.GOUV_COLOR}
DEFAULTS = {"dpi": 200, "quality": 95, "page_size": "keep",
            "suffix": "watermark"}


def _ctx(**over):
    base = {"classic": CLASSIC, "gouv": GOUV, "d": DEFAULTS,
            "max_mb": MAX_MB, "form": {}, "error": None}
    base.update(over)
    return base


def _safe_suffix(raw: str) -> str:
    """Sanitise the filename suffix. It ends up in ZIP entry names and the
    Content-Disposition header, so strip anything enabling Zip Slip (path
    separators) or header/control-char injection; keep it short."""
    s = re.sub(r"[/\\\x00-\x1f]", "_", raw or "").strip().strip(".")
    return s[:60] or "watermark"


def _bounded_dpi(src, dpi):
    """Read the PDF's own geometry and clamp work to safe bounds: reject huge
    page counts, and lower the effective DPI so a crafted giant MediaBox cannot
    blow up the raster (OOM / decompression bomb)."""
    try:
        info = pdf2image.pdfinfo_from_path(str(src))
    except Exception:
        raise RuntimeError("unreadable or corrupt PDF")
    try:
        pages = int(info.get("Pages", 0))
    except (TypeError, ValueError):
        pages = 0
    if pages > MAX_PAGES:
        raise ValueError(f"too many pages (max {MAX_PAGES})")
    eff = dpi
    m = re.match(r"\s*([\d.]+)\s*x\s*([\d.]+)", str(info.get("Page size", "")))
    if m:
        longest_in = max(float(m.group(1)), float(m.group(2))) / 72.0
        if longest_in > 0:
            cap = int(MAX_SIDE_PX / longest_in)
            if cap < eff:
                eff = max(1, cap)
    return eff


def _num(name, cast, default=None):
    raw = (request.form.get(name) or "").strip()
    if raw == "":
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ValueError(f"invalid value for {name}")


@app.get("/")
def index():
    return render_template("index.html", **_ctx())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/watermark")
def watermark():
    """Watermark one or several uploaded PDFs. One file comes back as a PDF,
    several come back as a ZIP. Validation errors return JSON so the front-end
    can show them inline. Everything runs in a temp dir wiped on return."""
    try:
        uploads = [u for u in request.files.getlist("pdf") if u and u.filename]
        if not uploads:
            raise ValueError("please choose at least one PDF file")
        for u in uploads:
            if not u.filename.lower().endswith(".pdf"):
                raise ValueError(f"not a PDF: {u.filename}")

        text = request.form.get("text", "")
        gouv = request.form.get("gouv") == "on"
        opacity = _num("opacity", float)
        rotation = _num("rotation", float)
        color = (request.form.get("color") or "").strip() or None
        dpi = _num("dpi", int, 200)
        font_size = _num("font_size", int)          # None => auto
        quality = _num("quality", int, 95)
        page_size = request.form.get("page_size", "keep")
        suffix = _safe_suffix(request.form.get("suffix", ""))
        if page_size not in ("keep", "a4"):
            raise ValueError("page size must be 'keep' or 'a4'")
        if len(uploads) > MAX_FILES:
            raise ValueError(f"too many files (max {MAX_FILES} per request)")
        if dpi > MAX_DPI:
            raise ValueError(f"--dpi must be {MAX_DPI} or less")
        if font_size is not None and font_size > MAX_FONT_SIZE:
            raise ValueError(f"--font-size must be {MAX_FONT_SIZE} or less")
        if opacity is not None and not math.isfinite(opacity):
            raise ValueError("--opacity must be a finite number")
        if rotation is not None and not math.isfinite(rotation):
            raise ValueError("--rotation must be a finite number")

        # Same defaults + validation as the CLI, same error messages.
        style, opacity, rotation, color, rgb = fz.resolve_params(
            text, opacity, rotation, color, dpi, font_size, quality, gouv)

        results = []            # (download_name, bytes)
        used = set()
        with tempfile.TemporaryDirectory() as tmp:
            for i, upload in enumerate(uploads):
                src = Path(tmp) / f"in_{i}.pdf"
                out = Path(tmp) / f"out_{i}.pdf"
                upload.save(src)
                eff_dpi = _bounded_dpi(src, dpi)
                fz.watermark_pdf(src, text, str(out), opacity, rotation, eff_dpi,
                                 font_size, rgb, quality, None, style, page_size)
                stem = Path(secure_filename(upload.filename)).stem or "document"
                name = f"{stem}_{suffix}.pdf"
                n = 1
                while name in used:                 # avoid clashes in the zip
                    name = f"{stem}_{suffix}_{n}.pdf"
                    n += 1
                used.add(name)
                results.append((name, out.read_bytes()))

        if len(results) == 1:
            name, data = results[0]
            return send_file(io.BytesIO(data), mimetype="application/pdf",
                             as_attachment=True, download_name=name)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in results:
                zf.writestr(name, data)
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"filigranez_{suffix}.zip")

    except ValueError as e:                # validation / bad input
        return jsonify(error=str(e)), 400
    except RuntimeError as e:              # bad/corrupt PDF, or poppler missing
        app.logger.warning("watermark runtime error: %s", e)
        return jsonify(error="unreadable or corrupt PDF"), 400
    except Exception:                      # never leak internals to the client
        app.logger.exception("watermark failed")
        return jsonify(error="could not process the document"), 500


@app.errorhandler(RequestEntityTooLarge)
def _too_large(_e):
    return jsonify(error=f"upload too large (limit {MAX_MB} MB total)"), 413


if __name__ == "__main__":
    # Dev server only; production runs under gunicorn (see Dockerfile).
    app.run(host=os.environ.get("BIND", "127.0.0.1"),
            port=int(os.environ.get("PORT", 8010)), debug=False)
