import concurrent.futures
import io
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape as xml_escape

try:
    from PIL import Image
except ImportError:
    Image = None

from scraper import LiveTVScraper

try:
    from zoneinfo import ZoneInfo
    DISPLAY_TZ = ZoneInfo(os.getenv("DISPLAY_TZ", "Europe/Rome"))
except Exception:
    DISPLAY_TZ = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

import socket

def _detect_local_ip(default="192.168.8.102"):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except Exception:
        return default

ACESTREAM_IP = os.getenv("ACESTREAM_IP") or _detect_local_ip()
ACESTREAM_PORT = os.getenv("ACESTREAM_PORT", "6878")
HTTP_PORT = os.getenv("HTTP_PORT", "8080")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.3"))
OUTPUT_PATH = os.getenv("OUTPUT_PATH", "playlist.m3u")
OUTPUT_HLS_PATH = os.getenv("OUTPUT_HLS_PATH", "")
OUTPUT_EPG_PATH = os.getenv("OUTPUT_EPG_PATH", "")
STATS_PATH = os.getenv("STATS_PATH", "last_run.json")

EPG_SEGMENTS = [
    (-30, 0, "🏟️ Pre-partita"),
    (0, 50, "⚽ 1° Tempo"),
    (50, 63, "☕ Intervallo"),
    (63, 115, "⚽ 2° Tempo"),
    (115, 150, "🏁 Post-partita / Suppl."),
]


def sanitize_m3u_attr(value):
    return (value or "").replace('"', "'").replace(",", " ").strip()


def atomic_write(path, content):
    """Write via temp file + rename so clients never read a half-written file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=os.path.splitext(path)[1])
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _fit_badge(img, box_size=88):
    img = img.convert("RGBA")
    img.thumbnail((box_size, box_size), Image.LANCZOS)
    cell = Image.new("RGBA", (box_size, box_size), (0, 0, 0, 0))
    cell.paste(img, ((box_size - img.width) // 2, (box_size - img.height) // 2), img)
    return cell


def build_combined_logo(session, event_id, team_logos, logos_dir):
    """Download the two team crests, place them side by side on a 192x96 PNG, and return its local HTTP URL."""
    if not team_logos:
        return ""
    if len(team_logos) == 1 or Image is None:
        return team_logos[0]

    os.makedirs(logos_dir, exist_ok=True)
    filename = f"{event_id}.png"
    dest_path = os.path.join(logos_dir, filename)
    local_url = f"http://{ACESTREAM_IP}:{HTTP_PORT}/logos/{filename}"

    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
        return local_url

    try:
        imgs = []
        for url in team_logos[:2]:
            resp = session.get(url, timeout=10)
            resp.raise_for_status()
            imgs.append(Image.open(io.BytesIO(resp.content)))

        left = _fit_badge(imgs[0], 88)
        right = _fit_badge(imgs[1], 88)
        canvas = Image.new("RGBA", (192, 96), (0, 0, 0, 0))
        canvas.paste(left, (4, 4), left)
        canvas.paste(right, (100, 4), right)

        fd, tmp = tempfile.mkstemp(dir=logos_dir, prefix=".tmp_", suffix=".png")
        os.close(fd)
        canvas.save(tmp, format="PNG", optimize=True)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest_path)
        return local_url
    except Exception as e:
        logger.warning("Could not build combined logo for %s (%s), falling back to single logo", event_id, e)
        return team_logos[0]


def cleanup_old_logos(logos_dir, max_age_hours=48):
    if not os.path.isdir(logos_dir):
        return
    cutoff = time.time() - max_age_hours * 3600
    for name in os.listdir(logos_dir):
        if not name.endswith(".png"):
            continue
        p = os.path.join(logos_dir, name)
        try:
            if os.path.getmtime(p) < cutoff:
                os.unlink(p)
        except OSError:
            pass


def _build_m3u_lines(entries, endpoint="getstream", epg_url=""):
    header = f'#EXTM3U url-tvg="{epg_url}" x-tvg-url="{epg_url}"' if epg_url else "#EXTM3U"
    lines = [header]
    for match, streams in entries:
        ko = match.get("kickoff")
        if ko and DISPLAY_TZ:
            ko = ko.astimezone(DISPLAY_TZ)
        time_str = ko.strftime("%H:%M") if ko else "LIVE"
        comp = match["competition"]
        safe_teams = sanitize_m3u_attr(match["teams"])
        group = sanitize_m3u_attr(f"Calcio / {comp}" if comp else "Calcio")
        logo_url = sanitize_m3u_attr(match.get("logo_url") or match.get("icon_url", ""))
        logo_attr = f' tvg-logo="{logo_url}"' if logo_url else ""

        for i, s_obj in enumerate(streams):
            s = s_obj["url"]
            if s.startswith("acestream://"):
                ace_id = s[len("acestream://"):]
                url = f"http://{ACESTREAM_IP}:{ACESTREAM_PORT}/ace/{endpoint}?id={ace_id}"
            else:
                url = s
            tvg_id = f"{match['event_id']}-{i}"
            # Clean channel name: just time + teams (+ Link N when multiple links exist)
            name = f"{time_str} {match['teams']}"
            if len(streams) > 1:
                name += f" ({i + 1})"
            lines.append(
                f'#EXTINF:-1 tvg-name="{safe_teams}" tvg-id="{tvg_id}"'
                f'{logo_attr} group-title="{group}",{sanitize_m3u_attr(name)}'
            )
            lines.append(url)
    return lines


def _fmt_xmltv_dt(dt):
    if DISPLAY_TZ:
        dt = dt.astimezone(DISPLAY_TZ)
    return dt.strftime("%Y%m%d%H%M%S %z")


def _build_epg_xml(entries):
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tv generator-info-name="ace-scraper">',
    ]
    now = datetime.now(timezone.utc)

    for match, streams in entries:
        ko = match.get("kickoff") or now
        if DISPLAY_TZ:
            ko_local = ko.astimezone(DISPLAY_TZ)
        else:
            ko_local = ko
        ko_str = ko_local.strftime("%H:%M")
        comp = match.get("competition") or "Calcio"
        teams = match["teams"]
        logo_url = match.get("logo_url") or match.get("icon_url", "")

        for i, s_obj in enumerate(streams):
            ch_id = f"{match['event_id']}-{i}"
            ch_name = f"{ko_str} {teams}" + (f" ({i + 1})" if len(streams) > 1 else "")
            out.append(f'  <channel id="{xml_escape(ch_id)}">')
            out.append(f'    <display-name>{xml_escape(ch_name)}</display-name>')
            if logo_url:
                out.append(f'    <icon src="{xml_escape(logo_url)}"/>')
            out.append("  </channel>")

            qual_parts = [f"Competizione: {comp}", f"Fischio d'inizio: {ko_str}"]
            if s_obj.get("lang"):
                qual_parts.append(f"Lingua: {s_obj['lang']}")
            if s_obj.get("bitrate"):
                qual_parts.append(f"Qualità: {s_obj['bitrate']} kbps")
            if len(streams) > 1:
                qual_parts.append(f"Stream: Link {i + 1} di {len(streams)}")
            desc_text = " · ".join(qual_parts)

            for start_m, end_m, phase_label in EPG_SEGMENTS:
                seg_start = ko_local + timedelta(minutes=start_m)
                seg_end = ko_local + timedelta(minutes=end_m)
                title = f"{phase_label}: {teams}"
                out.append(
                    f'  <programme start="{_fmt_xmltv_dt(seg_start)}" '
                    f'stop="{_fmt_xmltv_dt(seg_end)}" channel="{xml_escape(ch_id)}">'
                )
                out.append(f'    <title lang="it">{xml_escape(title)}</title>')
                out.append(f'    <sub-title lang="it">{xml_escape(comp)}</sub-title>')
                out.append(f'    <desc lang="it">{xml_escape(desc_text)}</desc>')
                out.append('    <category lang="it">Sport</category>')
                out.append("  </programme>")

    out.append("</tv>")
    return "\n".join(out) + "\n"


def generate_m3u(output_path=OUTPUT_PATH, output_hls_path=OUTPUT_HLS_PATH, output_epg_path=OUTPUT_EPG_PATH):
    if not output_hls_path:
        base, ext = os.path.splitext(output_path)
        output_hls_path = f"{base}_hls{ext or '.m3u'}"
    if not output_epg_path:
        output_epg_path = os.path.join(os.path.dirname(os.path.abspath(output_path)), "epg.xml")

    epg_http_url = f"http://{ACESTREAM_IP}:{HTTP_PORT}/epg.xml"
    logos_dir = os.path.join(os.path.dirname(os.path.abspath(output_path)), "logos")
    cleanup_old_logos(logos_dir)

    scraper = LiveTVScraper()
    matches = scraper.get_matches()
    list_page_ok = scraper.stats["http_errors"] == 0

    def fetch_match_streams(match):
        time.sleep(REQUEST_DELAY)
        details = scraper.get_event_details(match["detail_url"])
        combined = build_combined_logo(scraper.session, match["event_id"], details["team_logos"], logos_dir)
        match["logo_url"] = combined or match.get("icon_url", "")
        return match, details["streams"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(fetch_match_streams, matches))

    seen_ace_ids = set()
    entries = []
    for match, streams in results:
        kept = []
        for s_obj in streams:
            s = s_obj["url"]
            if s.startswith("acestream://"):
                ace_id = s[len("acestream://"):]
                if ace_id in seen_ace_ids:
                    continue
                seen_ace_ids.add(ace_id)
            kept.append(s_obj)
        if kept:
            entries.append((match, kept))

    scraper.stats["streams_valid"] = len(seen_ace_ids)

    def sort_key(item):
        ko = item[0].get("kickoff")
        return (ko is None, ko.timestamp() if ko else 0, item[0]["teams"])

    entries.sort(key=sort_key)

    lines_ts = _build_m3u_lines(entries, endpoint="getstream", epg_url=epg_http_url)
    lines_hls = _build_m3u_lines(entries, endpoint="manifest.m3u8", epg_url=epg_http_url)
    epg_xml = _build_epg_xml(entries)
    channels = (len(lines_ts) - 1) // 2

    if channels > 0:
        atomic_write(output_path, "\n".join(lines_ts) + "\n")
        atomic_write(output_hls_path, "\n".join(lines_hls) + "\n")
        atomic_write(output_epg_path, epg_xml)
        logger.info("Generated %s, %s and %s with %d entries", output_path, output_hls_path, output_epg_path, channels)
    elif list_page_ok:
        empty_m3u = f'#EXTM3U url-tvg="{epg_http_url}" x-tvg-url="{epg_http_url}"\n'
        atomic_write(output_path, empty_m3u)
        atomic_write(output_hls_path, empty_m3u)
        atomic_write(output_epg_path, epg_xml)
        logger.warning("No streams found -- wrote empty playlists and EPG")
    else:
        logger.error("livetv unreachable -- keeping previous playlists")

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stats": scraper.stats,
        "channels": channels,
        "config": {
            "acestream_ip": ACESTREAM_IP,
            "acestream_port": ACESTREAM_PORT,
            "max_workers": MAX_WORKERS,
            "request_delay": REQUEST_DELAY,
        },
    }
    atomic_write(STATS_PATH, json.dumps(summary, indent=2))
    scraper.session.close()
    return 0 if list_page_ok else 1


if __name__ == "__main__":
    raise SystemExit(generate_m3u())
