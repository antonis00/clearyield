"""Company logos, fetched once and kept on this computer, so the page only ever loads them from the app itself.

Sources, tried in order until one gives a real image:
1. The company's own site icon (through Google's icon service, from the website Yahoo lists), when it's at least
   SHARP_ICON pixels: companies draw it as their symbol alone for small sizes (BBVA's "^", ING's lion), so it stays
   sharp in a list, where a full logo with its name shrinks into a blur.
2. Financial Modeling Prep's public logo images, by ticker (Yahoo's suffixes work: AZN.L, QNBK.QA, NBK.KW; funds get
   their issuer's logo, e.g. Vanguard for VWCE.DE; coins too). It answers 404 for a ticker it doesn't know.
3. Parqet's logos, by ticker (strong on Europe).
4. A smaller site icon, from Google or else DuckDuckGo, down to MIN_ICON pixels.
Each is tidied once, when saved (prepare(): margins trimmed, a white logo on a dark tile, a square PNG; never SVG, which
can carry scripts), so lists show it as sharply as its source allows. Nothing found means the page shows the first letter
instead; that's remembered for MISS_DAYS before trying again. A logo is kept LOGO_DAYS.
Demo mode never fetches. The background scoring fetches the list's logos (warm()), so pages rarely wait for them.
"""
import hashlib
import io
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import data, db

log = logging.getLogger("clearyield.logos")

LOGO_DAYS = 30
MISS_DAYS = 7
MAX_BYTES = 300_000
MIN_ICON = 32  # a site icon smaller than this looks blurred next to the others
SHARP_ICON = 48  # a site icon this size or more is preferred to the full logo
TIMEOUT = 8
WORKERS = 8
KEY = "logo:v3:"  # v2: the site icon first; v3: tidied into a square PNG
SIZE = 128
MARGIN = 0.1  # the even margin put back around a trimmed mark, as a share of its size
MAX_ASPECT = 4  # a logo more than this many times wider than tall (after trimming) is no use in a square tile
MAX_PIXELS = 4096 * 4096  # a bigger image is no logo (and could be a decompression bomb)
DARK_TILE = (31, 41, 55, 255)  # behind a logo drawn in white
SOURCES = {
    "google": "https://www.google.com/s2/favicons?domain={}&sz=256",
    "fmp": "https://financialmodelingprep.com/image-stock/{}.png",
    "parqet": "https://assets.parqet.com/logos/symbol/{}?format=png",
    "duckduckgo": "https://icons.duckduckgo.com/ip3/{}.ico",
}
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")  # indexes (^GSPC) and currencies (EURUSD=X) have no logo


def folder():
    return Path(db.DB_PATH).parent / "logos"


def has_logo(ticker):
    return bool(_TICKER.match(ticker or ""))


def kind(blob):
    """The image's format from its first bytes, or None for anything else (an error page, an SVG)."""
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if blob.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp"
    if blob[:4] == b"\x00\x00\x01\x00":
        return "ico"
    return None


def prepare(blob, source, least=MIN_ICON):
    """A real logo made ready to show, as a square SIZE-pixel PNG, or None. Each one is tidied the same way:
    - only PNG, JPEG, GIF, WebP or ICO (never SVG), and a site icon at least `least` pixels across;
    - an empty margin (transparent or white) is trimmed and a thin even one put back, so a small mark fills its tile;
    - a logo drawn in white on a transparent background goes on a dark tile (on white it would vanish), anything else
      with transparency on white; a logo with its own background colour keeps it, edge to edge;
    - an image with nothing visible is refused."""
    if not blob or len(blob) < 40 or len(blob) > MAX_BYTES or not kind(blob):
        return None
    from PIL import Image, ImageChops
    try:
        with Image.open(io.BytesIO(blob)) as im:  # an ICO opens at its largest size
            w, h = im.size
            if w * h > MAX_PIXELS or (source in ("google", "duckduckgo") and min(w, h) < least):
                return None
            im = im.convert("RGBA")
    except Exception:  # not really an image, or a broken one
        return None
    r, g, b, a = im.split()
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)
    seen = a.point(lambda v: 255 if v > 40 else 0)
    corners = [im.getpixel(xy) for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    clear = sum(c[3] < 20 for c in corners) >= 3
    white = not clear and sum(c[3] > 200 and min(c[:3]) > 235 for c in corners) >= 3
    if clear:
        mark = seen
    elif white:
        mark = ImageChops.multiply(seen, darkest.point(lambda v: 255 if v < 225 else 0))
    else:
        mark = None  # its own background: kept whole
    if mark is not None:
        box = mark.getbbox()
        if not box:
            return None  # nothing on it
        im = im.crop(box)
    if max(im.size) > MAX_ASPECT * min(im.size):
        return None  # a long wordmark: in a square tile its letters shrink into a smear, so the next source is tried
    if clear:
        visible = seen.histogram()[255] or 1
        light = ImageChops.multiply(seen, darkest.point(lambda v: 255 if v > 215 else 0)).histogram()[255]
        ground = DARK_TILE if light / visible > 0.8 else (255, 255, 255, 255)
    elif white:
        ground = (255, 255, 255, 255)
    else:
        ground = tuple(int(sum(c[i] for c in corners) / 4) for i in range(3)) + (255,)
    cw, ch = im.size
    side = max(cw, ch)
    pad = round(side * MARGIN) if mark is not None else 0
    tile = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), ground)
    tile.alpha_composite(im, ((tile.width - cw) // 2, (tile.height - ch) // 2))
    tile = tile.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
    out = io.BytesIO()
    tile.save(out, "PNG", optimize=True)
    return out.getvalue()


def domain(ticker):
    """The company's website domain, from Yahoo's company details (which the app keeps anyway), or None."""
    try:
        site = data.info(ticker).get("website")
    except Exception as e:
        log.info("no website for %s: %s", ticker, e)
        return None
    m = re.match(r"^(?:https?://)?(?:www\.)?([^/:?#\s]+)", site or "")
    return m.group(1).lower() if m else None


def _fetch(url):
    """The bytes at `url`, or None. Kept apart so the tests can stand in for the network."""
    import httpx
    try:
        # Read as it arrives and stop past MAX_BYTES, so a misbehaving source can't fill the memory; a few redirects only
        with httpx.Client(follow_redirects=True, max_redirects=3, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0 ClearYield"}) as client:
            with client.stream("GET", url) as r:
                if r.status_code != 200:
                    return None
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        return None
                return body
    except Exception as e:
        log.info("logo fetch failed %s: %s", url, e)
        return None


def _file(ticker):
    return folder() / f"{hashlib.sha1(ticker.encode()).hexdigest()[:16]}.png"


def _save(ticker, png, source):
    folder().mkdir(parents=True, exist_ok=True)
    path = _file(ticker)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(png)
    tmp.replace(path)
    rec = {"source": source}
    db.cache_set(KEY + ticker, rec)
    return rec


def find(ticker):
    """Try the sources in turn (see the top of this file); save and record the first real logo.
    {"fmt", "source"} or {"none": True}."""
    site = domain(ticker)
    icon = _fetch(SOURCES["google"].format(site)) if site else None
    png = prepare(icon, "google", SHARP_ICON)
    if png:
        return _save(ticker, png, "google")
    for name in ("fmp", "parqet"):
        png = prepare(_fetch(SOURCES[name].format(ticker)), name)
        if png:
            return _save(ticker, png, name)
    png = prepare(icon, "google")
    if png:
        return _save(ticker, png, "google")
    png = prepare(_fetch(SOURCES["duckduckgo"].format(site)), "duckduckgo") if site else None
    if png:
        return _save(ticker, png, "duckduckgo")
    rec = {"none": True}
    db.cache_set(KEY + ticker, rec, ts=time.time() - (LOGO_DAYS - MISS_DAYS) * 86400)  # expires after MISS_DAYS
    return rec


def get(ticker, fetch=True):
    """(PNG bytes, media type) of the ticker's logo, or None. Fetches it when not yet known and `fetch` is set."""
    ticker = (ticker or "").strip().upper()
    if data.DEMO or not has_logo(ticker):
        return None
    rec = db.cache_get(KEY + ticker, LOGO_DAYS * 86400)
    if rec is None:
        # Out of date (or the cache was cleared) but saved: show it now, and let the background refresh it
        old = db.cache_get_stale(KEY + ticker)
        if old and not old.get("none") and _file(ticker).exists():
            rec = old
        elif fetch:
            rec = find(ticker)
        else:
            return None
    if rec.get("none"):
        return None
    path = _file(ticker)
    if not path.exists():  # the folder was emptied
        if not fetch:
            return None
        rec = find(ticker)
        if rec.get("none"):
            return None
    return path.read_bytes(), "image/png"


def warm(tickers):
    """Fetch the logos not yet known, a few at a time. Returns how many were looked up."""
    if data.DEMO:
        return 0
    todo = [t for t in dict.fromkeys(tickers) if has_logo(t) and db.cache_get(KEY + t, LOGO_DAYS * 86400) is None]
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(_safe_find, todo))
    return len(todo)


def _safe_find(t):
    try:
        find(t)
    except Exception as e:  # one bad logo never stops the rest
        log.info("logo for %s failed: %s", t, e)
