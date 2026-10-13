#!/usr/bin/env python3
"""Weather Central animated national radar bulletin. Test mode only; never uploads to YouTube."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import subprocess
import sys
import tempfile
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageSequence

NWS_ALERTS = "https://api.weather.gov/alerts/active"
RADAR_GIF = "https://radar.weather.gov/ridge/standard/CONUS_loop.gif"
USER_AGENT = "WeatherCentral/0.2 (educational weather video; see https://github.com/b3434474-art/Weather)"
WIDTH, HEIGHT = 1280, 720
FPS = 12
NAVY = (4, 14, 29)
HEADER = (7, 26, 48)
PANEL = (9, 29, 51)
PANEL_ALT = (13, 40, 65)
CYAN = (76, 220, 255)
WHITE = (242, 248, 255)
MUTED = (159, 184, 205)
RED = (255, 76, 88)
ORANGE = (255, 171, 65)
YELLOW = (255, 218, 87)
GREEN = (89, 220, 153)
BORDER = (38, 82, 112)
MAX_DATA_AGE_HOURS = 8


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value):
    if not value:
        return None
    try:
        result = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=dt.timezone.utc)
        return result.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def fetch_json(url):
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
        timeout=(10, 30),
    )
    response.raise_for_status()
    server_date = response.headers.get("Date")
    server_time = parsedate_to_datetime(server_date).astimezone(dt.timezone.utc) if server_date else None
    return response.json(), response, server_time


def fetch_alerts(fetched_at):
    payload, response, server_time = fetch_json(NWS_ALERTS)
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise RuntimeError("NWS active-alert response has an unexpected structure.")
    updated = parse_time(payload.get("updated"))
    if updated and abs((fetched_at - updated).total_seconds()) > MAX_DATA_AGE_HOURS * 3600:
        raise RuntimeError("NWS alert dataset timestamp is too old; refusing to render.")
    if server_time and abs((fetched_at - server_time).total_seconds()) > 15 * 60:
        raise RuntimeError("Local clock differs from the NWS server clock by more than 15 minutes.")

    alerts = []
    for feature in payload["features"]:
        props = feature.get("properties") or {}
        if props.get("status") not in (None, "Actual"):
            continue
        if props.get("messageType") in ("Cancel", "Ack"):
            continue
        effective = parse_time(props.get("effective") or props.get("onset"))
        expires = parse_time(props.get("expires"))
        if not effective or not expires or effective > fetched_at or expires <= fetched_at:
            continue
        event = str(props.get("event") or "Weather Alert").strip()
        headline = str(props.get("headline") or event).strip()
        area = str(props.get("areaDesc") or "Area not specified").strip()
        severity = str(props.get("severity") or "Unknown").strip()
        alerts.append({
            "event": event, "headline": headline, "area": area, "severity": severity,
            "certainty": str(props.get("certainty") or "Unknown"),
            "urgency": str(props.get("urgency") or "Unknown"),
            "effective": effective.isoformat(), "expires": expires.isoformat(),
            "url": str(props.get("uri") or feature.get("id") or ""),
            "geometry": feature.get("geometry"),
        })
    rank = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3, "Unknown": 4}
    alerts.sort(key=lambda a: (rank.get(a["severity"], 4), a["expires"]))
    return alerts, response.headers.get("Date"), updated.isoformat() if updated else None, server_time


def load_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for name in candidates:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def text_width(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]


def wrap_text(draw, text, font, max_width, max_lines=2):
    words, lines, current = str(text).split(), [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and " ".join(lines) != str(text):
        last = lines[-1]
        while last and text_width(draw, last + "…", font) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def severity_color(alert):
    event = alert["event"].lower()
    if "tornado" in event or alert["severity"] in ("Extreme", "Severe"):
        return RED
    if any(word in event for word in ("warning", "flash flood")):
        return ORANGE
    return YELLOW


def fetch_radar_frames():
    response = requests.get(RADAR_GIF, headers={"User-Agent": USER_AGENT}, timeout=(10, 45))
    response.raise_for_status()
    if len(response.content) < 10_000:
        raise RuntimeError("NWS radar response is unexpectedly small; refusing to render.")
    with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as temp:
        temp.write(response.content)
        path = Path(temp.name)
    try:
        with Image.open(path) as gif:
            frames = []
            for frame in ImageSequence.Iterator(gif):
                frames.append(frame.convert("RGB").copy())
                if len(frames) >= 24:
                    break
        if not frames:
            raise RuntimeError("No usable frames were found in the official NWS radar loop.")
        return frames, len(response.content)
    finally:
        path.unlink(missing_ok=True)


def geometry_rings(geometry):
    """Return GeoJSON polygon rings; keep geometry separate from the radar raster."""
    if not isinstance(geometry, dict):
        return []
    kind, coords = geometry.get("type"), geometry.get("coordinates") or []
    if kind == "Polygon":
        return coords
    if kind == "MultiPolygon":
        return [ring for polygon in coords for ring in polygon]
    return []


def draw_alert_map(draw, box, alerts, fonts):
    """Geographic overview of alert footprints. Deliberately not overlaid on radar."""
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=8, fill=(5, 19, 35), outline=BORDER, width=1)
    draw.text((x1 + 12, y1 + 8), "ALERT FOOTPRINTS", font=fonts["label"], fill=CYAN)
    draw.text((x2 - 170, y1 + 10), "GEOGRAPHIC VIEW", font=fonts["tiny"], fill=MUTED)
    mx1, my1, mx2, my2 = x1 + 12, y1 + 34, x2 - 12, y2 - 12
    # Approximate CONUS bounds for the schematic inset. Alaska, Hawaii, and territories
    # are omitted from this inset rather than plotted in misleading positions.
    lon_min, lon_max, lat_min, lat_max = -125.0, -66.0, 24.0, 50.0
    def point(coord):
        lon, lat = float(coord[0]), float(coord[1])
        px = mx1 + (lon - lon_min) / (lon_max - lon_min) * (mx2 - mx1)
        py = my2 - (lat - lat_min) / (lat_max - lat_min) * (my2 - my1)
        return int(px), int(py)
    # Grid and border provide geographic context without implying radar alignment.
    for lon in (-120, -110, -100, -90, -80, -70):
        px, _ = point((lon, lat_min))
        draw.line((px, my1, px, my2), fill=(21, 47, 67), width=1)
    for lat in (25, 30, 35, 40, 45, 50):
        _, py = point((lon_min, lat))
        draw.line((mx1, py, mx2, py), fill=(21, 47, 67), width=1)
    draw.rectangle((mx1, my1, mx2, my2), outline=(46, 83, 107), width=1)
    shown = 0
    for alert in alerts:
        color = severity_color(alert)
        for ring in geometry_rings(alert.get("geometry")):
            coords = []
            for coord in ring:
                try:
                    lon, lat = float(coord[0]), float(coord[1])
                    if lon_min <= lon <= lon_max and lat_min <= lat <= lat_max:
                        coords.append(point((lon, lat)))
                except (TypeError, ValueError, IndexError):
                    continue
            if len(coords) >= 3:
                draw.polygon(coords, fill=(color[0] // 5, color[1] // 6, color[2] // 6))
                draw.line(coords + [coords[0]], fill=color, width=2)
                shown += 1
    if not shown:
        draw.text((mx1 + 12, my1 + 18), "No mappable CONUS alert polygons", font=fonts["small"], fill=MUTED)
    draw.text((mx1 + 5, my2 - 17), "AK / HI / territories not shown", font=fonts["tiny"], fill=MUTED)


def render_frame(radar_frame, alerts, fetched_at, frame_index, frame_count, duration):
    canvas = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    draw = ImageDraw.Draw(canvas)
    fonts = {
        "title": load_font(27, True), "label": load_font(15, True),
        "body": load_font(14), "small": load_font(12),
        "tiny": load_font(10), "alert": load_font(13, True),
        "ticker": load_font(14, True), "number": load_font(24, True),
    }
    elapsed = frame_index / FPS
    # Header and live data indicator.
    draw.rectangle((0, 0, WIDTH, 67), fill=HEADER)
    draw.text((25, 15), "WEATHER CENTRAL", font=fonts["title"], fill=WHITE)
    draw.text((335, 25), "NATIONAL RADAR • OFFICIAL ALERTS", font=fonts["label"], fill=CYAN)
    draw.ellipse((WIDTH - 196, 26, WIDTH - 184, 38), fill=RED if (frame_index // 6) % 2 == 0 else (110, 45, 55))
    draw.text((WIDTH - 176, 24), "DATA SNAPSHOT", font=fonts["label"], fill=WHITE)
    draw.line((0, 67, WIDTH, 67), fill=CYAN, width=2)

    # Radar panel, with fresh GIF frames sampled through the clip.
    radar_box = (22, 84, 856, 527)
    draw.rounded_rectangle(radar_box, radius=8, fill=(2, 9, 18), outline=BORDER, width=2)
    inner = (radar_box[0] + 8, radar_box[1] + 8, radar_box[2] - 8, radar_box[3] - 8)
    radar = radar_frame.copy()
    radar.thumbnail((inner[2] - inner[0], inner[3] - inner[1]), Image.Resampling.LANCZOS)
    px = inner[0] + (inner[2] - inner[0] - radar.width) // 2
    py = inner[1] + (inner[3] - inner[1] - radar.height) // 2
    canvas.paste(radar, (px, py))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((inner[0], inner[1], inner[0] + 196, inner[1] + 27), fill=(4, 21, 38))
    draw.text((inner[0] + 8, inner[1] + 5), "NWS CONUS RADAR LOOP", font=fonts["small"], fill=CYAN)
    draw.rectangle((inner[0], inner[3] - 24, inner[0] + 270, inner[3]), fill=(4, 21, 38))
    draw.text((inner[0] + 8, inner[3] - 19), "RADAR IMAGE • NOT A FORECAST", font=fonts["tiny"], fill=WHITE)

    # Right column: current highest-priority alerts.
    sx, sw = 873, 385
    draw.rounded_rectangle((sx, 84, sx + sw, 527), radius=8, fill=PANEL, outline=BORDER, width=2)
    draw.text((sx + 15, 98), "ACTIVE NWS ALERTS", font=fonts["label"], fill=CYAN)
    draw.text((sx + sw - 85, 99), f"{len(alerts):,} TOTAL", font=fonts["tiny"], fill=MUTED)
    draw.line((sx + 15, 125, sx + sw - 15, 125), fill=BORDER, width=1)
    if not alerts:
        draw.ellipse((sx + 18, 148, sx + 31, 161), fill=GREEN)
        draw.text((sx + 42, 145), "No current active alert features", font=fonts["body"], fill=WHITE)
        draw.text((sx + 18, 173), "returned by the NWS feed at fetch time.", font=fonts["small"], fill=MUTED)
    else:
        y = 137
        for alert in alerts[:4]:
            color = severity_color(alert)
            draw.rounded_rectangle((sx + 12, y, sx + sw - 12, y + 72), radius=5, fill=PANEL_ALT, outline=color, width=1)
            draw.rectangle((sx + 12, y, sx + 17, y + 72), fill=color)
            title = wrap_text(draw, alert["event"].upper(), fonts["alert"], sw - 52, 1)[0]
            draw.text((sx + 27, y + 7), title, font=fonts["alert"], fill=color)
            area_lines = wrap_text(draw, alert["area"], fonts["small"], sw - 52, 2)
            for j, line in enumerate(area_lines):
                draw.text((sx + 27, y + 29 + j * 14), line, font=fonts["small"], fill=WHITE)
            draw.text((sx + 27, y + 57), f"Severity: {alert['severity']}", font=fonts["tiny"], fill=MUTED)
            y += 78
    draw.text((sx + 15, 493), "Always follow local emergency instructions.", font=fonts["tiny"], fill=MUTED)

    # Independent footprint map: alert polygons are not falsely projected onto the radar.
    draw_alert_map(draw, (22, 539, 425, 652), alerts, fonts)

    # Headlines panel uses only official alert headlines, not fabricated news.
    hx1, hy1, hx2, hy2 = 439, 539, 1258, 652
    draw.rounded_rectangle((hx1, hy1, hx2, hy2), radius=8, fill=PANEL, outline=BORDER, width=1)
    draw.text((hx1 + 14, hy1 + 9), "WEATHER HEADLINES • OFFICIAL ALERT FEED", font=fonts["label"], fill=CYAN)
    if alerts:
        lead = alerts[frame_index // (FPS * 5) % len(alerts)]
        draw.text((hx1 + 14, hy1 + 36), lead["headline"][:100], font=fonts["alert"], fill=severity_color(lead))
        lines = wrap_text(draw, lead["area"], fonts["small"], hx2 - hx1 - 28, 2)
        for j, line in enumerate(lines):
            draw.text((hx1 + 14, hy1 + 59 + j * 14), line, font=fonts["small"], fill=WHITE)
    else:
        draw.text((hx1 + 14, hy1 + 43), "No active alert headlines were returned by the NWS feed.", font=fonts["body"], fill=WHITE)

    # Moving ticker. The message is assembled from official alert data and attribution.
    ticker_y1, ticker_y2 = 661, 700
    draw.rectangle((0, ticker_y1, WIDTH, ticker_y2), fill=(3, 10, 20))
    draw.rectangle((0, ticker_y1, 145, ticker_y2), fill=(12, 52, 80))
    draw.text((16, ticker_y1 + 11), "WEATHER UPDATE", font=fonts["small"], fill=CYAN)
    if alerts:
        items = [f"{a['event'].upper()} — {a['area']}" for a in alerts[:12]]
        ticker = "     •     ".join(items) + "     •     DATA FROM NATIONAL WEATHER SERVICE"
    else:
        ticker = "NO ACTIVE ALERT FEATURES RETURNED BY NWS • DATA FROM NATIONAL WEATHER SERVICE"
    width = text_width(draw, ticker, fonts["ticker"])
    available = WIDTH - 160
    travel = max(1, width + available)
    offset = int((elapsed * 115) % travel)
    draw.text((160 - offset, ticker_y1 + 9), ticker, font=fonts["ticker"], fill=WHITE)
    # Repeated copy avoids a blank gap when the crawl is longer than the frame.
    if width - offset < available:
        draw.text((160 - offset + width + 55, ticker_y1 + 9), ticker, font=fonts["ticker"], fill=WHITE)

    stamp = fetched_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    draw.rectangle((0, 701, WIDTH, HEIGHT), fill=(7, 26, 48))
    draw.text((22, 703), "SOURCE: weather.gov • radar.weather.gov", font=fonts["tiny"], fill=MUTED)
    stamp_text = f"FETCHED {stamp}  |  {frame_index + 1:04d}/{frame_count:04d}"
    draw.text((WIDTH - 335, 703), stamp_text, font=fonts["tiny"], fill=CYAN)
    return canvas


def main():
    parser = argparse.ArgumentParser(description="Render an animated national Weather Central test video.")
    parser.add_argument("--output", default="output/weather-central.mp4")
    parser.add_argument("--duration", type=int, default=30, help="Video length in seconds (15-60).")
    args = parser.parse_args()
    if not 15 <= args.duration <= 60:
        raise RuntimeError("Duration must be between 15 and 60 seconds.")
    fetched_at = utcnow()
    alerts, alerts_http_date, dataset_updated, server_time = fetch_alerts(fetched_at)
    radar_frames, radar_bytes = fetch_radar_frames()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame_count = args.duration * FPS
    manifest = {
        "mode": "test",
        "created_at_utc": fetched_at.isoformat(),
        "sources": {
            "active_alerts": NWS_ALERTS, "radar": RADAR_GIF,
            "alerts_http_date": alerts_http_date,
            "alerts_dataset_updated": dataset_updated,
            "nws_server_time": server_time.isoformat() if server_time else None,
            "radar_retrieved_at_utc": fetched_at.isoformat(),
            "radar_bytes": radar_bytes,
        },
        "active_alert_count": len(alerts),
        "alert_geometries_available": sum(bool(a.get("geometry")) for a in alerts),
        "alerts": [{k: v for k, v in a.items() if k != "geometry"} for a in alerts],
        "video": {"width": WIDTH, "height": HEIGHT, "fps": FPS, "requested_duration_seconds": args.duration},
        "upload_performed": False,
        "dedupe_key": f"weather-central-national-{fetched_at.strftime('%Y-%m-%d')}",
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        frames_dir = Path(temp_dir) / "frames"
        frames_dir.mkdir()
        for i in range(frame_count):
            radar_index = int(i * len(radar_frames) / frame_count) % len(radar_frames)
            frame = render_frame(radar_frames[radar_index], alerts, fetched_at, i, frame_count, args.duration)
            frame.save(frames_dir / f"frame-{i:05d}.png", optimize=True)
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", str(FPS), "-i", str(frames_dir / "frame-%05d.png"),
            "-t", str(args.duration), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ], check=True, timeout=300)
    if not output.exists() or output.stat().st_size < 100_000:
        raise RuntimeError("Rendered MP4 is missing or unexpectedly small.")
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration,size",
        "-of", "json", str(output),
    ], check=True, capture_output=True, text=True, timeout=20)
    info = json.loads(probe.stdout).get("format", {})
    duration = float(info.get("duration", 0))
    if duration < args.duration - 0.5 or duration > args.duration + 1:
        raise RuntimeError(f"Rendered duration outside expected bounds: {duration}")
    manifest["output"] = {"path": str(output), "bytes": output.stat().st_size, "duration_seconds": duration}
    (output.parent / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "mode": "test", "output": str(output),
        "duration_seconds": duration, "active_alerts": len(alerts),
        "alert_geometries_available": manifest["alert_geometries_available"],
        "upload_performed": False,
    }, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"WEATHER CENTRAL VALIDATION FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
