import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import concurrent.futures
from scraper import LiveTVScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ACESTREAM_IP = os.getenv("ACESTREAM_IP", "192.168.1.58")
ACESTREAM_PORT = os.getenv("ACESTREAM_PORT", "6878")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.3"))
OUTPUT_PATH = os.getenv("OUTPUT_PATH", "playlist.m3u")
STATS_PATH = os.getenv("STATS_PATH", "last_run.json")


def sanitize_m3u_attr(value):
    return value.replace('"', "'").replace(',', ' ').strip()


def extract_time(time_str):
    m = re.search(r'\b(\d{1,2}:\d{2})\b', time_str)
    return m.group(1) if m else time_str


def parse_time_to_minutes(time_str):
    m = re.search(r'\b(\d{1,2}):(\d{2})\b', time_str)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return 9999


def generate_m3u(output_path=OUTPUT_PATH):
    scraper = LiveTVScraper()
    matches = scraper.get_matches()

    m3u_content = ["#EXTM3U"]

    def fetch_match_streams(match):
        time.sleep(REQUEST_DELAY)
        streams = scraper.get_acestream_links(match['detail_url'])
        return match, streams

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(fetch_match_streams, matches))

    match_data = []
    seen_acestream_ids = set()

    for match, streams in results:
        unique_streams = []
        for stream_url in streams:
            if stream_url.startswith("acestream://"):
                ace_id = stream_url.replace("acestream://", "")
                if ace_id in seen_acestream_ids:
                    continue
                seen_acestream_ids.add(ace_id)
                final_url = f"http://{ACESTREAM_IP}:{ACESTREAM_PORT}/ace/getstream?id={ace_id}"
            else:
                if "cdn.live" in stream_url or "http://:" in stream_url:
                    continue
                final_url = stream_url
            unique_streams.append(final_url)

        if unique_streams:
            match_data.append({
                "teams": match['teams'],
                "competition": match['competition'],
                "time": extract_time(match['time']),
                "streams": unique_streams,
                "sort_key": parse_time_to_minutes(match['time']),
            })

    scraper.stats["streams_valid"] = len(seen_acestream_ids)

    match_data.sort(key=lambda m: m['sort_key'])

    for md in match_data:
        teams = md['teams']
        comp = md['competition']
        time_str = md['time']
        unique_streams = md['streams']

        group = f"Football" if not comp else f"Football / {comp}"
        safe_teams = sanitize_m3u_attr(teams)
        safe_comp = sanitize_m3u_attr(comp)
        safe_group = sanitize_m3u_attr(group)

        for i, final_url in enumerate(unique_streams):
            display_name = sanitize_m3u_attr(f"{teams} ({comp}) - {time_str}")
            if len(unique_streams) > 1:
                display_name += f" - Link {i+1}"

            m3u_content.append(
                f'#EXTINF:-1 tvg-name="{safe_teams}" tvg-id="{safe_teams}-{time_str}-{i}" group-title="{safe_group}",{display_name}'
            )
            m3u_content.append(final_url)

    if len(m3u_content) > 1:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(m3u_content))
        logger.info(f"Generated {output_path} with {(len(m3u_content)-1)//2} entries")
    else:
        logger.warning("No streams found -- playlist will be empty")

    run_summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stats": scraper.stats,
        "channels": (len(m3u_content) - 1) // 2 if len(m3u_content) > 1 else 0,
        "config": {
            "acestream_ip": ACESTREAM_IP,
            "acestream_port": ACESTREAM_PORT,
            "max_workers": MAX_WORKERS,
            "request_delay": REQUEST_DELAY,
        }
    }
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(run_summary, f, indent=2)
    logger.info("Run summary written to %s", STATS_PATH)

    scraper.session.close()


if __name__ == "__main__":
    generate_m3u()
