import base64
import struct
import zlib
from datetime import datetime, timedelta, timezone

from usage_dashboard import status_icon as si

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def limit(kind, pct, left_min=None, window=None):
    resets = NOW + timedelta(minutes=left_min) if left_min is not None else None
    return {"kind": kind, "pct": pct, "resets_at": resets, "window_min": window,
            "key": kind, "label": kind, "order": 0}


def decode(png):
    """Tiny PNG reader for our own 8-bit RGBA, filter-0 files: returns (w, h, rows)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(png):
        n, tag = struct.unpack(">I4s", png[pos:pos + 8])
        data = png[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(tag + data) & 0xFFFFFFFF
        if tag == b"IHDR":
            w, h = struct.unpack(">II", data[:8])
        elif tag == b"IDAT":
            idat += data
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = w * 4 + 1
    rows = [raw[y * stride + 1:(y + 1) * stride] for y in range(h)]
    return w, h, rows


def px(rows, x, y):
    return tuple(rows[y][x * 4:x * 4 + 4])


def test_quiet_when_on_track():
    st = si.summarize([limit("session", 20, 200, 300), limit("weekly_all", 12, 8000, 10080)], NOW)
    assert [r["level"] for r in st["rows"]] == ["ok", "ok"]
    assert st["progress_state"] == "ok" and st["progress_pct"] == 20


def test_amber_when_getting_warm():
    st = si.summarize([limit("session", 72, 20, 300), limit("weekly_all", 5, 9000, 10080)], NOW)
    assert st["rows"][0]["level"] in ("warn", "hot")
    assert st["rows"][1]["level"] == "ok"


def test_urgent_at_90_percent_or_when_on_pace_to_run_out():
    at90 = si.summarize([limit("session", 91, 250, 300), limit("weekly_all", 5, 9000, 10080)], NOW)
    assert at90["rows"][0]["level"] == "hot"
    # 50% used one hour into a five-hour window: far ahead of pace, so it runs out early
    pace = si.summarize([limit("session", 50, 240, 300), limit("weekly_all", 5, 9000, 10080)], NOW)
    assert pace["rows"][0]["level"] == "hot"
    assert pace["progress_state"] == "hot"


def test_progress_follows_the_more_urgent_limit():
    st = si.summarize([limit("session", 30, 200, 300), limit("weekly_all", 95, 9000, 10080)], NOW)
    assert st["progress_pct"] == 95 and st["progress_state"] == "hot"


def test_missing_limits_and_no_data():
    assert si.summarize([], NOW) is None
    st = si.summarize([limit("session", 40, 150, 300)], NOW)
    assert st["rows"][1] is None
    si.icon_png(st, 16)   # draws with an empty weekly bar


def test_key_changes_with_the_picture_not_with_noise():
    a = si.summarize([limit("session", 20.0, 200, 300), limit("weekly_all", 12, 8000, 10080)], NOW)
    b = si.summarize([limit("session", 21.0, 200, 300), limit("weekly_all", 12, 8000, 10080)], NOW)
    c = si.summarize([limit("session", 60.0, 200, 300), limit("weekly_all", 12, 8000, 10080)], NOW)
    assert a["key"] == b["key"] != c["key"]


def test_png_is_valid_and_bars_have_the_right_colours():
    st = si.summarize([limit("session", 40, 150, 300), limit("weekly_all", 5, 9000, 10080)], NOW)
    for size in (16, 32, 48):
        w, h, rows = decode(si.icon_png(st, size))
        assert (w, h) == (size, size)
        assert px(rows, 0, 0)[3] == 0          # rounded corner is transparent
    w, h, rows = decode(si.icon_png(st, 48))
    top_fill = px(rows, 11, 17)                 # inside the session bar's fill
    track = px(rows, 35, 17)                    # the empty part of the same bar
    assert top_fill[:3] == si.COLORS["ok"] and top_fill[3] == 255
    assert track[:3] == si.TRACK


def test_urgent_bar_gets_a_spark():
    calm = si.summarize([limit("session", 40, 150, 300), limit("weekly_all", 5, 9000, 10080)], NOW)
    hot = si.summarize([limit("session", 96, 150, 300), limit("weekly_all", 5, 9000, 10080)], NOW)
    assert hot["rows"][0]["level"] == "hot"
    assert si.icon_png(calm, 32) != si.icon_png(hot, 32)
    w, h, rows = decode(si.icon_png(hot, 48))
    # near the tip of the red bar there is the pale spark colour
    found = any(px(rows, x, 17)[:3][0] > 240 and px(rows, x, 17)[:3][1] > 180 for x in range(25, 40))
    assert found


def test_base64_is_a_png():
    st = si.summarize([limit("session", 50, 150, 300)], NOW)
    assert base64.b64decode(si.icon_base64(st, 16))[:4] == b"\x89PNG"
