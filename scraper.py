import requests
from bs4 import BeautifulSoup
import re
import logging
import time
import urllib3

# Disable insecure request warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LiveTVScraper:
    def __init__(self, base_url="https://livetv.sx/enx/allupcomingsports/1/"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.base_origin = self._get_base_origin(base_url)

    def _get_base_origin(self, url):
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def get_matches(self):
        """Scrapes the main page for upcoming matches."""
        logger.info(f"Fetching matches from {self.base_url}")
        try:
            # Disable SSL verification to bypass CERTIFICATE_VERIFY_FAILED
            response = self.session.get(self.base_url, timeout=30, verify=False)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Replicating Scraper.kt link discovery
            detail_links = soup.select("a[href*='/enx/event/']")
            if not detail_links:
                detail_links = soup.select("a[href*='/event/']")
            
            matches = []
            for link in detail_links:
                href = link.get('href', '')
                if not href:
                    continue
                
                detail_url = href if href.startswith('http') else f"{self.base_origin}{href}"
                
                # Find the row/container
                row = link.find_parent('tr') or link.parent
                
                # Extract metadata
                time_text = row.select_one(".time, td.time, [class*='time']").get_text(strip=True) if row.select_one(".time, td.time, [class*='time']") else ""
                teams = row.select_one(".evdesc, .event-title, .event-desc").get_text(strip=True) if row.select_one(".evdesc, .event-title, .event-desc") else link.get_text(strip=True)
                competition = row.select_one(".league, .competition, td.league > a").get_text(strip=True) if row.select_one(".league, .competition, td.league > a") else ""
                
                if len(teams) > 3:
                    matches.append({
                        "teams": teams,
                        "time": time_text,
                        "competition": competition,
                        "detail_url": detail_url
                    })
            
            # Dedup by detail_url
            seen = set()
            unique_matches = []
            for m in matches:
                if m['detail_url'] not in seen:
                    unique_matches.append(m)
                    seen.add(m['detail_url'])
                    
            logger.info(f"Found {len(unique_matches)} unique matches")
            if len(unique_matches) == 0:
                logger.debug(f"HTML snippet: {response.text[:1000]}")
            return unique_matches
            
        except Exception as e:
            logger.error(f"Error fetching matches: {e}")
            return []

    def get_acestream_links(self, detail_url):
        """Scrapes a detail page for Acestream links."""
        logger.info(f"Fetching streams from {detail_url}")
        try:
            # Disable SSL verification
            response = self.session.get(detail_url, timeout=30, verify=False)
            response.raise_for_status()
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
            
            links = set()
            
            # 1. DOM Search
            acestream_tags = soup.select("a[href*='acestream://']")
            for tag in acestream_tags:
                links.add(tag['href'])
            
            # 2. Regex fallbacks
            acestream_regex = re.compile(r"acestream://[a-zA-Z0-9]+")
            
            # Search in body text
            body_text = soup.body.get_text() if soup.body else ""
            links.update(acestream_regex.findall(body_text))
            
            # Search in raw HTML
            links.update(acestream_regex.findall(html))
            
            logger.info(f"Found {len(links)} Acestream links for {detail_url}")
            return sorted(list(links))
            
        except Exception as e:
            logger.error(f"Error fetching streams for {detail_url}: {e}")
            return []

if __name__ == "__main__":
    scraper = LiveTVScraper()
    matches = scraper.get_matches()
    if matches:
        # Test with the first match
        test_match = matches[0]
        print(f"Testing match: {test_match['teams']}")
        streams = scraper.get_acestream_links(test_match['detail_url'])
        print(f"Streams found: {streams}")
