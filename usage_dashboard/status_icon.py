"""The live taskbar icon: two gauge bars (session on top, weekly below).

Standard library only: shapes are rasterised here (with smoothing) and encoded as a PNG
that tkinter can show. Each bar is blue when you're fine, amber when it's getting warm
and red when it's urgent; an urgent bar also gets a small spark at its tip.
"""
import base64
import math
import struct
import zlib

from .core import project

OK, WARN, HOT = "ok", "warn", "hot"
RANK = {OK: 0, WARN: 1, HOT: 2}
COLORS = {OK: (55, 138, 221), WARN: (239, 159, 39), HOT: (226, 75, 74)}
TRACK, TILE, EDGE = (44, 44, 42), (28, 28, 27), (58, 58, 56)
SPARK, SPARK_GLOW, WHITE = (250, 199, 117), (239, 159, 39), (255, 255, 255)

WARN_PCT = 70   # a bar turns amber at this percent...
HOT_PCT = 90    # ...and red (with a spark) at this percent, or when on pace to run out


def level_for(item, now):
    """ok / warn / hot for one limit: by how full it is and by where it is heading."""
    p = project(item, now)
    if item["pct"] >= HOT_PCT or p["state"] in ("over", "full"):
        return HOT
    if item["pct"] >= WARN_PCT or p["state"] == "warn":
        return WARN
    return OK


def summarize(items, now):
    """What the icon and the taskbar progress bar should show, or None without data.

    Returns a dict: rows (session, weekly; each {pct, level} or None), key (changes only
    when the picture would), progress_pct and progress_state (the more urgent limit).
    """
    pick = {}
    for it in items:
        if it["kind"] == "session":
            pick.setdefault("session", it)
        elif it["kind"] == "weekly_all":
            pick.setdefault("weekly", it)
    rows = []
    for name in ("session", "weekly"):
        it = pick.get(name)
        rows.append(None if it is None else
                    {"pct": min(100.0, max(0.0, it["pct"])), "level": level_for(it, now)})
    present = [r for r in rows if r]
    if not present:
        return None
    top = max(present, key=lambda r: (RANK[r["level"]], r["pct"]))
    key = tuple(None if r is None else (int(r["pct"] // 4), r["level"]) for r in rows)
    return {"rows": rows, "key": key, "progress_pct": int(round(top["pct"])),
            "progress_state": top["level"]}


# ---- drawing (coordinates are on a 256 x 256 canvas) ----

def _shapes(rows):
    s = [("rrect", 128, 128, 240, 240, 52, EDGE, 1.0),
         ("rrect", 128, 128, 236, 236, 50, TILE, 1.0)]
    x0, width, height = 40, 176, 50
    for row, y in zip(rows, (66, 140)):
        cy = y + height / 2
        s.append(("rrect", x0 + width / 2, cy, width, height, height / 2, TRACK, 1.0))
        if row is None:
            continue
        fw = max(14.0, width * row["pct"] / 100)
        s.append(("rrect", x0 + fw / 2, cy, fw, height, min(height / 2, fw / 2),
                  COLORS[row["level"]], 1.0))
        if row["level"] == HOT:
            sx = x0 + fw - 4
            s.append(("circle", sx, cy, 34, 0, 0, SPARK_GLOW, 0.30))
            s.append(("star", sx, cy, 30, 0, 0, SPARK, 1.0))
            s.append(("circle", sx, cy, 6, 0, 0, WHITE, 1.0))
    return s


def _covers(shape, x, y):
    kind, cx, cy, a, b, c, _col, _al = shape
    if kind == "circle":
        return (x - cx) ** 2 + (y - cy) ** 2 <= a * a
    if kind == "star":  # four-point sparkle
        return (abs(x - cx) / a) ** (2 / 3) + (abs(y - cy) / a) ** (2 / 3) <= 1
    dx = max(abs(x - cx) - (a / 2 - c), 0.0)   # rounded rectangle
    dy = max(abs(y - cy) - (b / 2 - c), 0.0)
    return dx * dx + dy * dy <= c * c


def _bounds(shape):
    kind, cx, cy, a, b, _c, _col, _al = shape
    if kind == "rrect":
        return cx - a / 2, cy - b / 2, cx + a / 2, cy + b / 2
    return cx - a, cy - a, cx + a, cy + a


def render_rgba(rows, size):
    """Premultiplied float canvas -> straight RGBA bytes."""
    ss = 4 if size <= 32 else 3
    scale = 256.0 / size
    buf = [[0.0, 0.0, 0.0, 0.0] for _ in range(size * size)]  # r, g, b premultiplied; a
    for shape in _shapes(rows):
        col, alpha = shape[6], shape[7]
        bx0, by0, bx1, by1 = _bounds(shape)
        px0, py0 = max(0, int(bx0 / scale) - 1), max(0, int(by0 / scale) - 1)
        px1, py1 = min(size, int(bx1 / scale) + 2), min(size, int(by1 / scale) + 2)
        for py in range(py0, py1):
            for px in range(px0, px1):
                hit = 0
                for sy in range(ss):
                    y = (py + (sy + 0.5) / ss) * scale
                    for sx in range(ss):
                        if _covers(shape, (px + (sx + 0.5) / ss) * scale, y):
                            hit += 1
                if not hit:
                    continue
                a = alpha * hit / (ss * ss)
                d = buf[py * size + px]
                d[0] = col[0] * a + d[0] * (1 - a)
                d[1] = col[1] * a + d[1] * (1 - a)
                d[2] = col[2] * a + d[2] * (1 - a)
                d[3] = a + d[3] * (1 - a)
    out = bytearray()
    for r, g, b, a in buf:
        if a <= 0:
            out += b"\x00\x00\x00\x00"
        else:
            out += bytes((min(255, round(r / a)), min(255, round(g / a)),
                          min(255, round(b / a)), min(255, round(a * 255))))
    return bytes(out)


def encode_png(width, height, rgba):
    raw = b"".join(b"\x00" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def icon_png(status, size):
    return encode_png(size, size, render_rgba(status["rows"], size))


def icon_base64(status, size):
    """The PNG as base64 text, which is what tkinter's PhotoImage(data=...) takes."""
    return base64.b64encode(icon_png(status, size)).decode("ascii")
