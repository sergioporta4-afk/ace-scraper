import requests
from bs4 import BeautifulSoup
import re
import logging
import time
import urllib3
from datetime import datetime, timedelta, timezone

# Disable insecure request warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

FOOTBALL_KEYWORDS = {
    "football", "soccer", "premier", "liga", "bundesliga", "serie a",
    "ligue", "champions league", "europa league", "uefa", "fifa", "world cup"
}

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

    def _parse_match_time(self, time_text):
        """Parse match time string to a UTC datetime for today.
        Handles formats: '22:30', '14 March at 22:30', '15 March at 0:30(Brazil...)'.
        Returns a datetime (UTC) or None if unparseable."""
        if not time_text:
            return None
        
        now_utc = datetime.now(timezone.utc)
        
        # Extract HH:MM
        time_match = re.search(r'(\d{1,2}):(\d{2})', time_text)
        if not time_match:
            return None
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        
        # Extract date if present (e.g. "15 March")
        date_match = re.search(
            r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)',
            time_text, re.IGNORECASE
        )
        if date_match:
            day = int(date_match.group(1))
            month_str = date_match.group(2).capitalize()
            month = datetime.strptime(month_str, '%B').month
            year = now_utc.year
            try:
                dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
                return dt
            except ValueError:
                pass
        
        # No date — assume it's today in UTC
        try:
            dt = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return dt
        except ValueError:
            return None

    def clean_team_names(self, teams, competition):
        cleaned = teams
        
        # Remove date patterns
        cleaned = re.sub(r'\d{1,2}\s+\w+\s+at\s*', '', cleaned)
        cleaned = re.sub(r'\w+\s+\d{1,2}\s+at\s*', '', cleaned)
        cleaned = re.sub(r'\d{1,2}\s+\w+\s+\d{4}\s+at\s*', '', cleaned)
        
        # Remove time patterns
        cleaned = re.sub(r'\d{1,2}:\d{2}', '', cleaned)
        cleaned = re.sub(r'at\s+\d{1,2}:\d{2}', '', cleaned)
        
        # Remove parentheses content
        cleaned = re.sub(r'\([^)]*\)', '', cleaned)
        
        if competition:
            # Re.escape is needed for competition string
            cleaned = re.sub(re.escape(competition), '', cleaned, flags=re.IGNORECASE)
            
        patterns = [
            r'live|today|tomorrow|now',
            r'GMT|UTC|CET|EST|PST',
            r'\s+0:\d+\s*$'
        ]
        for p in patterns:
            cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE)
            
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        cleaned = re.sub(r'^[|:,.;\s]+|[|:,.;\s]+$', '', cleaned)
        
        if len(cleaned) > 3 and any(sep in cleaned for sep in ['-', '–', 'vs', 'v ']):
            return cleaned
        return teams

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
            if not detail_links:
                detail_links = soup.select("a[href*='event']")
            
            matches = []
            for link in detail_links:
                href = link.get('href', '')
                if not href:
                    continue
                
                detail_url = href if href.startswith('http') else f"{self.base_origin}{href}"
                
                # Find the row/container
                row = link.find_parent('tr') or link.parent
                
                # Extract metadata
                time_elem = row.select_one("td.time, .time, [class*='time'], td:nth-child(1)")
                teams_elem = row.select_one("td.evdesc, .evdesc, .event-title, .event-desc, [class*='event'], [class*='team'], td:nth-child(3)")
                comp_elem = row.select_one("td.league > a, .league, .competition, [class*='league'], td:nth-child(2)")
                
                time_text = time_elem.get_text(strip=True) if time_elem else ""
                teams = teams_elem.get_text(strip=True) if teams_elem else ""
                competition = comp_elem.get_text(strip=True) if comp_elem else ""
                
                # Fallback: if time_text has no HH:MM pattern, scan the full row text
                if not re.search(r'\d{1,2}:\d{2}', time_text):
                    row_text = row.get_text(separator=' ', strip=True)
                    # Look for patterns like "22:30" or "14 March at 22:30"
                    date_time_match = re.search(
                        r'((?:\d{1,2}\s+\w+\s+at\s+)?\d{1,2}:\d{2})',
                        row_text
                    )
                    if date_time_match:
                        time_text = date_time_match.group(1)
                
                if not teams or len(teams) < 5:
                    teams = link.get_text(strip=True)
                
                if not teams or len(teams) < 5:
                    parent = link.parent
                    attempts = 0
                    while parent and attempts < 3 and (not teams or len(teams) < 5):
                        # Get text of parent WITHOUT its children's text
                        t = ''.join([c if isinstance(c, str) else '' for c in parent.contents]).strip()
                        if t and len(t) > 5:
                            teams = t
                            break
                        parent = parent.parent
                        attempts += 1

                # Heuristic: swap teams/competition if their content looks reversed.
                if teams and competition:
                    teams_looks_like_league = (
                        len(teams) < 10 or 
                        re.search(r'\([^)]+\)', teams) or 
                        re.search(r'\d{1,2}\s+\w+\s+at', teams) or 
                        re.search(r'\b(ncaa|nba|nfl|mlb|nhl|premier|liga|serie|bundesliga|league|cup|championship|division|conference|botola|pro|first|elite)\b', teams.lower())
                    )
                    comp_looks_like_teams = (
                        len(competition) > 15 or 
                        re.search(r'[–—-]', competition) or 
                        re.search(r'\bvs?\.?\b', competition) or 
                        re.search(r'\d+:\d+', competition) or
                        len(re.split(r'[–—-]', competition)) == 2
                    )
                    
                    if teams_looks_like_league and comp_looks_like_teams:
                        teams, competition = competition, teams
                        
                # Basic cleaning of teams string
                teams = self.clean_team_names(teams, competition)

                
                if len(teams) > 3:
                    combined_text = (teams + " " + competition).lower()
                    if any(k in combined_text for k in FOOTBALL_KEYWORDS):
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
            
            # Time-window filter: only keep matches that are live or starting within 60 min
            # Live = started up to 120 minutes ago
            # Upcoming = starts within 60 minutes
            now_utc = datetime.now(timezone.utc)
            window_start = now_utc - timedelta(minutes=120)
            window_end   = now_utc + timedelta(minutes=60)
            
            filtered_matches = []
            skipped = 0
            no_time_count = 0
            for m in unique_matches:
                match_dt = self._parse_match_time(m['time'])
                if match_dt is None:
                    # Can't parse time at all — skip it (don't include blindly)
                    no_time_count += 1
                    logger.debug(f"No time found for '{m['teams']}' (time_text='{m['time']}'): skipping")
                elif window_start <= match_dt <= window_end:
                    filtered_matches.append(m)
                else:
                    skipped += 1
                    logger.debug(f"Skipping '{m['teams']}' at {m['time']} (match_dt={match_dt.strftime('%m-%d %H:%M')}, window={window_start.strftime('%H:%M')}-{window_end.strftime('%H:%M')} UTC)")
            
            logger.info(
                f"Time filter: {len(unique_matches)} total → "
                f"{len(filtered_matches)} in window, {skipped} future/past, {no_time_count} unparseable (skipped)"
            )
            if len(filtered_matches) == 0 and no_time_count == len(unique_matches):
                # All matches had unparseable times — likely a page structure issue.
                # Fall back to returning all matches so the file isn't empty.
                logger.warning("Could not parse ANY match times — falling back to all matches (time filter disabled)")
                return unique_matches
            return filtered_matches
            
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
            
            # 2. Regex fallback on entire HTML string
            # Livetv.sx often hides stream links in javascript variables or other elements
            
            # Acestream pattern
            acestream_regex = re.compile(r"acestream://[a-zA-Z0-9]+")
            links.update(acestream_regex.findall(html))
            
            # Webplayer pattern (often protocol-relative starting with //)
            webplayer_regex = re.compile(r"(?:https?:)?//[^\s\"'<>]+webplayer[^\s\"'<]*", re.IGNORECASE)
            webplayer_links = webplayer_regex.findall(html)
            
            for wl in webplayer_links:
                if wl.startswith("//"):
                    links.add(f"https:{wl}")
                else:
                    links.add(wl)
            
            # Additional fallback: JS_URL_REGEX from original code for common stream words
            # Should only search within <script> tags to avoid catching UI links
            js_url_regex = re.compile(r"https?://[^\s\"'<>]+(?:\.m3u8|stream|live|watch|player)", re.IGNORECASE)
            for script_tag in soup.find_all('script'):
                if script_tag.string:
                    links.update(js_url_regex.findall(script_tag.string))
            
            # Filter out invalid or garbage links (flashplayer, livetv interface links)
            valid_links = set()
            for link in links:
                if "get.adobe.com" in link or "flashplayer" in link.lower():
                    continue
                if "livetv.sx" in link and "eventinfo" in link:
                    # these are language switcher links for the same event
                    continue
                if link.startswith("http://cdn.live") or link.startswith("https://cdn.live"):
                    continue
                valid_links.add(link)
            
            logger.info(f"Found {len(valid_links)} valid stream links for {detail_url}")
            return sorted(list(valid_links))
            
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
