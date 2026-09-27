# filigranez - web interface

A thin Flask front-end over the CLI. Same engine, same defaults; the CLI is
unaffected and remains fully usable on its own.

## Run with Docker (recommended)

```bash
cd web
docker compose up -d          # -> http://localhost:8010
docker compose logs -f        # follow
docker compose down           # stop
```

The image bundles poppler and the Liberation fonts, so it runs identically on
any machine with Docker - nothing else to install.

## Run without Docker (dev)

```bash
pip install -r ../requirements.txt -r requirements.txt
python webapp.py              # -> http://localhost:8010
```

Needs `poppler-utils` on the host (`apt install poppler-utils`).

## Notes

- **Drag & drop multiple PDFs** - one file returns a PDF, several return a ZIP.
- **Progress bar with ETA** when several files are processed (handled one by one, the ZIP is assembled in the browser).
- **Cancel button** to stop a running batch, with a **per-file status** (queued, processing, done, failed) in the list.
- **Settings are remembered** in the browser (text, style, opacity, rotation, colour, DPI, quality, font size, page size, suffix).
- Server error messages are **shown in French** when the interface is in French.
- File list shows the **total size**, a **Clear all** button, and flags any file over the 100 MB per-file limit.
- **EN/FR** interface and **three themes** (Black (OLED) default, grey, white), remembered per browser.
- **Live preview of the real document**: the first page of the selected file is rendered with pdf.js (vendored locally in `static/vendor/`, no CDN) and the watermark is drawn on top, updating live with the options. With several files, click one in the list to preview it; with none, a mock page is shown.
- Every CLI option is exposed (text, gouv, opacity, rotation, colour, DPI, quality, font size, page size, suffix).
- Nothing is stored server-side: original and watermarked output live only in a temp dir wiped as soon as the download starts.
- Every output also carries an **invisible tracing layer** (a faint, recoverable copy of the watermark text) so a leak can be traced even if the visible mark is removed. Read it back with the CLI `--reveal`.

### Limits & hardening

The web is the untrusted surface; the CLI stays unbounded.

- Upload cap: **100 MB total** per request, **50 files** max.
- **DPI <= 600**, **font size <= 2000 px** - bounds the raster to avoid OOM / decompression-bomb DoS.
- The filename **suffix is sanitised** (no path separators or control chars): safe in ZIP entry names and the `Content-Disposition` header.
- Validation errors return JSON; unexpected errors return a generic message (no internals leaked).
- **No authentication** - for local or trusted-network use. Put it behind a reverse proxy with auth before exposing it publicly.


## Port

The container listens on **8010** by default (8000 is a common conflict - Portainer's edge tunnel uses it). Override with the `PORT` env var: `environment: [PORT=9xxx]`, and match it in the reverse-proxy `proxy_pass` and healthcheck.

## Behind a reverse proxy (sub-path)

The app honours `X-Forwarded-Prefix`/`X-Forwarded-Proto` (via werkzeug's
ProxyFix), so it can be served under a sub-path such as `location /filigranez/`
and still generate correct links. Have the proxy strip the prefix and forward
the header; with no header it behaves exactly as at the root. Example nginx
block (inside the existing TLS `server` for the domain):

```nginx
location = /filigranez { return 301 /filigranez/; }

location /filigranez/ {
    proxy_pass http://127.0.0.1:8010/;      # trailing slash strips the prefix
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /filigranez;

    client_max_body_size 100m;              # match the app's upload limit
    proxy_read_timeout   300s;              # big PDFs at high DPI
    proxy_request_buffering off;            # stream the upload
}
```
