import logging
import re
from scraper import LiveTVScraper
import concurrent.futures

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ACESTREAM_IP = "192.168.1.58"
ACESTREAM_PORT = "6878"

def extract_time(time_str):
    """Extract clean HH:MM from strings like '22:30 (Brazil. Serie A)' or '14 March at 22:30(Brazil. Serie A)'"""
    m = re.search(r'\b(\d{1,2}:\d{2})\b', time_str)
    return m.group(1) if m else time_str

def generate_m3u(output_path="playlist.m3u"):
    scraper = LiveTVScraper()
    matches = scraper.get_matches()
    
    m3u_content = ["#EXTM3U"]
    
    def fetch_match_streams(match):
        streams = scraper.get_acestream_links(match['detail_url'])
        return match, streams
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(fetch_match_streams, matches))
    
    # Global dedup: track which acestream IDs have already been written
    seen_acestream_ids = set()
    link_counters = {}  # track per-match link numbering after dedup
    
    for match, streams in results:
        teams = match['teams']
        comp = match['competition']
        time_str = extract_time(match['time'])
        
        # Collect unique, valid URLs for this match (dedup acestream IDs globally)
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
        
        for i, final_url in enumerate(unique_streams):
            display_name = f"{teams} ({comp}) - {time_str}"
            if len(unique_streams) > 1:
                display_name += f" - Link {i+1}"
                
            m3u_content.append(f'#EXTINF:-1 tvg-name="{teams}" group-title="Sports",{display_name}')
            m3u_content.append(final_url)

    if len(m3u_content) > 1:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(m3u_content))
        logger.info(f"Successfully generated {output_path} with {(len(m3u_content)-1)//2} entries")
    else:
        logger.warning("No streams found to populate the M3U file")

if __name__ == "__main__":
    generate_m3u()
