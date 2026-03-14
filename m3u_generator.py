import logging
from scraper import LiveTVScraper

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ACESTREAM_IP = "192.168.1.58"
ACESTREAM_PORT = "6878"

def generate_m3u(output_path="playlist.m3u"):
    scraper = LiveTVScraper()
    matches = scraper.get_matches()
    
    m3u_content = ["#EXTM3U"]
    
    for match in matches:
        teams = match['teams']
        comp = match['competition']
        time_str = match['time']
        
        # We only want matches that actually have stream links
        streams = scraper.get_acestream_links(match['detail_url'])
        
        for i, stream_url in enumerate(streams):
            # Extract ID from acestream://ID
            acestream_id = stream_url.replace("acestream://", "")
            
            # Format proxy URL
            proxy_url = f"http://{ACESTREAM_IP}:{ACESTREAM_PORT}/ace/getstream?id={acestream_id}"
            
            # Create M3U entry
            # i+1 is used to differentiate multiple streams for the same match
            display_name = f"{teams} ({comp}) - {time_str}"
            if len(streams) > 1:
                display_name += f" - Link {i+1}"
                
            m3u_content.append(f'#EXTINF:-1 tvg-name="{teams}" group-title="Sports",{display_name}')
            m3u_content.append(proxy_url)

    if len(m3u_content) > 1:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(m3u_content))
        logger.info(f"Successfully generated {output_path} with {len(m3u_content)//2} entries")
    else:
        logger.warning("No streams found to populate the M3U file")

if __name__ == "__main__":
    generate_m3u()
