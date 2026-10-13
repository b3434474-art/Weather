#!/usr/bin/env python3
"""Weather Central test-mode renderer. No YouTube upload functionality exists here."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from email.utils import parsedate_to_datetime

import requests
from PIL import Image, ImageDraw, ImageFont, ImageSequence

NWS_ALERTS = "https://api.weather.gov/alerts/active"
RADAR_GIF = "https://radar.weather.gov/ridge/standard/CONUS_loop.gif"
USER_AGENT = "WeatherCentral/0.1 (educational weather video; contact: repository owner)"
MAX_ALERT_AGE_HOURS = 8
WIDTH, HEIGHT = 1280, 720
NAVY = (5, 17, 34)
PANEL = (9, 30, 53)
CYAN = (80, 220, 255)
WHITE = (241, 248, 255)
MUTED = (155, 184, 207)
RED = (255, 76, 87)
AMBER = (255, 184, 75)


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def iso(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def fetch_json(url):
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
        timeout=(10, 25),
    )
    response.raise_for_status()
    date_header = response.headers.get("Date")
    server_time = parsedate_to_datetime(date_header).astimezone(dt.timezone.utc) if date_header else None
    return response.json(), response, server_time


def validate_alerts(payload, fetched_at):
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise RuntimeError("NWS alerts response has an unexpected structure.")
    generated = iso(payload.get("updated"))
    # The active-alert endpoint may not return an updated timestamp; the HTTP Date
    # header is only a transport freshness check, not a substitute for alert timestamps.
    if generated and abs((fetched_at - generated).total_seconds()) > MAX_ALERT_AGE_HOURS * 3600:
        raise RuntimeError("NWS alert dataset timestamp is stale; refusing to render.")
    alerts = []
    for feature in payload["features"]:
        props = feature.get("properties") or {}
        status = props.get("status")
        if status and status != "Actual":
            continue
        if props.get("messageType") in ("Cancel", "Ack"):
            continue
        effective = iso(props.get("effective") or props.get("onset"))
        expires = iso(props.get("expires"))
        # Only use currently active alerts with a known, non-expired validity window.
        if not effective or not expires or effective > fetched_at or expires <= fetched_at:
            continue
        headline = (props.get("headline") or props.get("event") or "").strip()
        area = (props.get("areaDesc") or "").strip()
        if not headline or not area:
            continue
        alerts.append({
            "event": str(props.get("event") or "Weather Alert"),
            "headline": headline,
            "area": area,
            "severity": str(props.get("severity") or "Unknown"),
            "expires": expires.isoformat(),
            "url": str(props.get("uri") or ""),
        })
    # Prioritize severity for display; do not synthesize warning types.
    rank = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3, "Unknown": 4}
    alerts.sort(key=lambda a: (rank.get(a["severity"], 4), a["expires"]))
    return alerts, generated.isoformat() if generated else None


def load_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width, max_lines=3):
    words = str(text).split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and words and " ".join(lines) != str(text):
        last = lines[-1]
        while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def get_radar_frames():
    response = requests.get(RADAR_GIF, headers={"User-Agent": USER_AGENT}, timeout=(10, 40))
    response.raise_for_status()
    if len(response.content) < 10_000:
        raise RuntimeError("Radar response is unexpectedly small; refusing to render.")
    with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as f:
        f.write(response.content)
        gif_path = f.name
    try:
        with Image.open(gif_path) as gif:
            frames = []
            for frame in ImageSequence.Iterator(gif):
                frames.append(frame.convert("RGB").copy())
                if len(frames) >= 18:
                    break
            if not frames:
                raise RuntimeError("No radar frames found.")
            return frames
    finally:
        Path(gif_path).unlink(missing_ok=True)


def render_frame(radar, alerts, fetched_at, frame_index, total_frames):
    canvas = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    d = ImageDraw.Draw(canvas)
    f_title = load_font(28, True)
    f_label = load_font(15, True)
    f_body = load_font(16)
    f_small = load_font(12)
    f_alert = load_font(14, True)

    # Header / station identity.
    d.rectangle((0, 0, WIDTH, 68), fill=(7, 28, 51))
    d.text((28, 17), "WEATHER CENTRAL", font=f_title, fill=WHITE)
    d.text((WIDTH - 300, 25), "NATIONAL RADAR DESK", font=f_label, fill=CYAN)
    d.line((0, 68, WIDTH, 68), fill=CYAN, width=2)

    # Main radar panel: preserve the official radar image without recoloring it.
    radar_box = (22, 88, 880, 585)
    d.rounded_rectangle(radar_box, radius=8, fill=(3, 12, 24), outline=(34, 76, 105), width=2)
    inner = (radar_box[0] + 8, radar_box[1] + 8, radar_box[2] - 8, radar_box[3] - 8)
    rw, rh = inner[2] - inner[0], inner[3] - inner[1]
    radar.thumbnail((rw, rh), Image.Resampling.LANCZOS)
    x = inner[0] + (rw - radar.width) // 2
    y = inner[1] + (rh - radar.height) // 2
    canvas.paste(radar, (x, y))
    d = ImageDraw.Draw(canvas)
    d.rectangle((inner[0], inner[1], inner[0] + 154, inner[1] + 28), fill=(5, 22, 40))
    d.text((inner[0] + 9, inner[1] + 6), "CONUS RADAR • NWS", font=f_small, fill=CYAN)

    # Side data panels.
    sx, sw = 882, 376
    d.rounded_rectangle((sx, 88, sx + sw, 585), radius=8, fill=PANEL, outline=(34, 76, 105), width=2)
    d.text((sx + 18, 105), "ACTIVE OFFICIAL ALERTS", font=f_label, fill=CYAN)
    d.line((sx + 18, 132, sx + sw - 18, 132), fill=(34, 76, 105), width=1)
    if not alerts:
        d.ellipse((sx + 18, 154, sx + 30, 166), fill=(88, 218, 151))
        d.text((sx + 42, 150), "No active alerts returned", font=f_body, fill=WHITE)
        d.text((sx + 18, 180), "for the national NWS feed at fetch time.", font=f_small, fill=MUTED)
    else:
        y = 149
        for alert in alerts[:5]:
            color = RED if alert["severity"] in ("Extreme", "Severe") else AMBER
            d.rounded_rectangle((sx + 15, y, sx + sw - 15, y + 70), radius=5, fill=(15, 43, 68), outline=color, width=1)
            title_lines = wrap_text(d, alert["event"].upper(), f_alert, sw - 52, 1)
            d.text((sx + 26, y + 8), title_lines[0], font=f_alert, fill=color)
            area_lines = wrap_text(d, alert["area"], f_small, sw - 52, 2)
            for j, line in enumerate(area_lines):
                d.text((sx + 26, y + 30 + j * 15), line, font=f_small, fill=WHITE)
            y += 78
            if y > 490:
                break
    d.text((sx + 18, 535), "Alerts may change; verify locally.", font=f_small, fill=MUTED)

    # Lower ticker band; this is factual metadata, not an invented forecast.
    d.rectangle((22, 600, WIDTH - 22, 658), fill=(10, 37, 62), outline=(34, 76, 105))
    if alerts:
        lead = f"{len(alerts)} ACTIVE NWS ALERT(S) • "
        lead += " | ".join(f"{a['event']} — {a['area'][:90]}" for a in alerts[:3])
    else:
        lead = "NWS ACTIVE ALERT FEED: NO CURRENT ACTIVE ALERT FEATURES RETURNED"
    lead = lead.replace("\n", " ")
    d.text((38, 619), lead[:150], font=f_small, fill=WHITE)

    stamp = fetched_at.strftime("%Y-%m-%d %H:%M UTC")
    d.text((26, 675), "SOURCE: weather.gov • radar.weather.gov", font=f_small, fill=MUTED)
    d.text((WIDTH - 315, 675), f"DATA FETCHED {stamp}", font=f_small, fill=CYAN)
    return canvas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="output/weather-central.mp4")
    parser.add_argument("--duration", type=int, default=12)
    args = parser.parse_args()
    if not 8 <= args.duration <= 30:
        raise RuntimeError("Duration must be between 8 and 30 seconds.")

    fetched_at = utcnow()
    alerts_payload, alerts_response, server_time = fetch_json(NWS_ALERTS)
    alerts, dataset_updated = validate_alerts(alerts_payload, fetched_at)
    # Require a valid HTTP date and reject significant local/server clock disagreement.
    if server_time and abs((fetched_at - server_time).total_seconds()) > 15 * 60:
        raise RuntimeError("Local clock and NWS server time differ by more than 15 minutes.")
    radar_frames = get_radar_frames()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fps = 10
    frame_count = args.duration * fps
    manifest = {
        "mode": "test",
        "created_at_utc": fetched_at.isoformat(),
        "sources": {
            "active_alerts": NWS_ALERTS,
            "radar": RADAR_GIF,
            "alerts_dataset_updated": dataset_updated,
            "alerts_http_date": alerts_response.headers.get("Date"),
            "radar_retrieved_at_utc": fetched_at.isoformat(),
        },
        "active_alert_count": len(alerts),
        "alerts": alerts,
        "upload_performed": False,
        "dedupe_key": f"weather-central-national-{fetched_at.strftime('%Y-%m-%d')}",
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()
        for i in range(frame_count):
            # Sample the official radar loop to create motion; it is explicitly
            # a recent radar loop, not a forecast or a promise of future storm motion.
            radar = radar_frames[int(i * len(radar_frames) / frame_count) % len(radar_frames)]
            image = render_frame(radar, alerts, fetched_at, i, frame_count)
            image.save(frames_dir / f"frame-{i:05d}.png", optimize=True)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", str(fps), "-i", str(frames_dir / "frame-%05d.png"),
            "-t", str(args.duration), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(output),
        ]
        subprocess.run(cmd, check=True, timeout=180)
        if not output.exists() or output.stat().st_size < 100_000:
            raise RuntimeError("Rendered MP4 is missing or unexpectedly small.")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
             "-of", "json", str(output)],
            check=True, capture_output=True, text=True, timeout=20,
        )
        info = json.loads(probe.stdout).get("format", {})
        duration = float(info.get("duration", 0))
        if duration < args.duration - 0.5 or duration > args.duration + 1:
            raise RuntimeError(f"Rendered duration outside expected bounds: {duration}")
        manifest["output"] = {"path": str(output), "bytes": output.stat().st_size, "duration_seconds": duration}
    (output.parent / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS", "mode": "test", "output": str(output),
                      "duration_seconds": manifest["output"]["duration_seconds"],
                      "active_alerts": len(alerts), "upload_performed": False}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"WEATHER CENTRAL VALIDATION FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
