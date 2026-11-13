import concurrent.futures
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone

from scraper import LiveTVScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

ACESTREAM_IP = os.getenv("ACESTREAM_IP", "192.168.1.58")
ACESTREAM_PORT = os.getenv("ACESTREAM_PORT", "6878")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.3"))
OUTPUT_PATH = os.getenv("OUTPUT_PATH", "playlist.m3u")
STATS_PATH = os.getenv("STATS_PATH", "last_run.json")


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


def generate_m3u(output_path=OUTPUT_PATH):
    scraper = LiveTVScraper()
    matches = scraper.get_matches()
    list_page_ok = scraper.stats["http_errors"] == 0

    def fetch_match_streams(match):
        time.sleep(REQUEST_DELAY)
        return match, scraper.get_acestream_links(match["detail_url"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(fetch_match_streams, matches))

    seen_ace_ids = set()
    entries = []
    for match, streams in results:
        urls = []
        for s in streams:
            if s.startswith("acestream://"):
                ace_id = s[len("acestream://"):]
                if ace_id in seen_ace_ids:
                    continue
                seen_ace_ids.add(ace_id)
                urls.append(f"http://{ACESTREAM_IP}:{ACESTREAM_PORT}/ace/getstream?id={ace_id}")
            else:
                urls.append(s)
        if urls:
            entries.append((match, urls))

    scraper.stats["streams_valid"] = len(seen_ace_ids)

    def sort_key(item):
        ko = item[0].get("kickoff")
        return (ko is None, ko.timestamp() if ko else 0, item[0]["teams"])

    entries.sort(key=sort_key)

    lines = ["#EXTM3U"]
    for match, urls in entries:
        ko = match.get("kickoff")
        time_str = ko.strftime("%H:%M") if ko else "LIVE"
        comp = match["competition"]
        safe_teams = sanitize_m3u_attr(match["teams"])
        group = sanitize_m3u_attr(f"Football / {comp}" if comp else "Football")
        for i, url in enumerate(urls):
            name = f"{time_str} {match['teams']}" + (f" ({comp})" if comp else "")
            if len(urls) > 1:
                name += f" - Link {i + 1}"
            lines.append(
                f'#EXTINF:-1 tvg-name="{safe_teams}" tvg-id="{match["event_id"]}-{i}" '
                f'group-title="{group}",{sanitize_m3u_attr(name)}'
            )
            lines.append(url)

    channels = (len(lines) - 1) // 2
    if channels > 0:
        atomic_write(output_path, "\n".join(lines) + "\n")
        logger.info("Generated %s with %d entries", output_path, channels)
    elif list_page_ok:
        # livetv reachable but nothing on: publish an empty playlist instead of stale dead links.
        atomic_write(output_path, "#EXTM3U\n")
        logger.warning("No streams found -- wrote empty playlist")
    else:
        logger.error("livetv unreachable -- keeping previous playlist")

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
