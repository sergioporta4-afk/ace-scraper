import os
import re
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

FOOTBALL_KEYWORDS = {
    "football", "soccer", "premier", "liga", "bundesliga", "serie a",
    "ligue", "champions league", "europa league", "uefa", "fifa", "world cup"
}

TIME_WINDOW_PAST = int(os.getenv("TIME_WINDOW_PAST", "120"))
TIME_WINDOW_FUTURE = int(os.getenv("TIME_WINDOW_FUTURE", "60"))
FALLBACK_MAX_MATCHES = 20


def _build_retry_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.verify = False
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


class LiveTVScraper:
    def __init__(self, base_url="https://livetv.sx/enx/allupcomingsports/1/"):
        self.base_url = base_url
        self.session = _build_retry_session()
        self.base_origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
        self.stats = {
            "matches_found": 0,
            "matches_filtered": 0,
            "matches_skipped_time": 0,
            "matches_unparseable": 0,
            "streams_found": 0,
            "streams_valid": 0,
            "http_errors": 0,
        }

    def _parse_match_time(self, time_text):
        if not time_text:
            return None
        now_utc = datetime.now(timezone.utc)

        time_match = re.search(r'(\d{1,2}):(\d{2})', time_text)
        if not time_match:
            return None
        hour, minute = int(time_match.group(1)), int(time_match.group(2))

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
                if dt < now_utc - timedelta(days=30):
                    dt = dt.replace(year=year + 1)
                return dt
            except ValueError:
                pass

        try:
            return now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None

    def _has_team_separator(self, text):
        if '-' in text or '\u2013' in text:
            return True
        return bool(re.search(r'\bvs?\.?\b', text, re.IGNORECASE))

    def clean_team_names(self, raw_teams, competition):
        raw_teams = re.sub(r'\d{1,2}\s+\w+\s+at\s*', '', raw_teams)
        raw_teams = re.sub(r'\w+\s+\d{1,2}\s+at\s*', '', raw_teams)
        raw_teams = re.sub(r'\d{1,2}\s+\w+\s+\d{4}\s+at\s*', '', raw_teams)

        raw_teams = re.sub(r'at\s+\d{1,2}:\d{2}', '', raw_teams)
        raw_teams = re.sub(r'\d{1,2}:\d{2}', '', raw_teams)
        raw_teams = re.sub(r'\([^)]*\)', '', raw_teams)

        if competition:
            raw_teams = re.sub(re.escape(competition), '', raw_teams, flags=re.IGNORECASE)

        for p in [r'\blive\b|\btoday\b|\btomorrow\b|\bnow\b', r'\bGMT\b|\bUTC\b|\bCET\b|\bEST\b|\bPST\b', r'\s+0:\d+\s*$']:
            raw_teams = re.sub(p, '', raw_teams, flags=re.IGNORECASE)

        raw_teams = re.sub(r'\s+', ' ', raw_teams).strip()
        raw_teams = re.sub(r'^[|:,.;\s]+|[|:,.;\s]+$', '', raw_teams)
        raw_teams = re.sub(r',\s*,', ',', raw_teams).strip(', ')

        if len(raw_teams) > 3 and self._has_team_separator(raw_teams):
            return raw_teams

        parts = [p.strip(' ,') for p in raw_teams.split(',') if p.strip(' ,')]
        team_names = [p for p in parts if self._has_team_separator(p) and len(p) > 3]
        if team_names:
            return team_names[0]

        return None

    def get_matches(self):
        logger.info(f"Fetching matches from {self.base_url}")
        try:
            response = self.session.get(self.base_url, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

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
                row = link.find_parent('tr') or link.parent

                time_elem = row.select_one("td.time, .time, [class*='time'], td:nth-child(1)")
                teams_elem = row.select_one("td.evdesc, .evdesc, .event-title, .event-desc, [class*='event'], [class*='team'], td:nth-child(3)")
                comp_elem = row.select_one("td.league > a, .league, .competition, [class*='league'], td:nth-child(2)")

                time_text = time_elem.get_text(strip=True) if time_elem else ""
                teams = teams_elem.get_text(strip=True) if teams_elem else ""
                competition = comp_elem.get_text(strip=True) if comp_elem else ""

                if not re.search(r'\d{1,2}:\d{2}', time_text):
                    row_text = row.get_text(separator=' ', strip=True)
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
                        t = ''.join([c if isinstance(c, str) else '' for c in parent.contents]).strip()
                        if t and len(t) > 5:
                            teams = t
                            break
                        parent = parent.parent
                        attempts += 1

                if teams and competition:
                    teams_looks_like_league = (
                        len(teams) < 10 or
                        re.search(r'\([^)]+\)', teams) or
                        re.search(r'\d{1,2}\s+\w+\s+at', teams) or
                        re.search(r'\b(ncaa|nba|nfl|mlb|nhl|premier|liga|serie|bundesliga|league|cup|championship|division|conference|botola|pro|first|elite)\b', teams.lower())
                    )
                    comp_looks_like_teams = (
                        len(competition) > 15 or
                        re.search(r'[\u2013\xad-]', competition) or
                        re.search(r'\bvs?\.?\b', competition) or
                        re.search(r'\d+:\d+', competition) or
                        len(re.split(r'[\u2013\xad-]', competition)) == 2
                    )
                    if teams_looks_like_league and comp_looks_like_teams:
                        teams, competition = competition, teams

                cleaned_teams = self.clean_team_names(teams, competition)
                if not cleaned_teams:
                    continue

                combined_text = (cleaned_teams + " " + competition).lower()
                if not any(k in combined_text for k in FOOTBALL_KEYWORDS):
                    continue

                matches.append({
                    "teams": cleaned_teams,
                    "time": time_text,
                    "competition": competition,
                    "detail_url": detail_url
                })

            seen = set()
            unique_matches = []
            for m in matches:
                if m['detail_url'] not in seen:
                    unique_matches.append(m)
                    seen.add(m['detail_url'])

            self.stats["matches_found"] = len(unique_matches)

            now_utc = datetime.now(timezone.utc)
            window_start = now_utc - timedelta(minutes=TIME_WINDOW_PAST)
            window_end = now_utc + timedelta(minutes=TIME_WINDOW_FUTURE)

            filtered_matches = []
            skipped = 0
            no_time_count = 0
            for m in unique_matches:
                match_dt = self._parse_match_time(m['time'])
                if match_dt is None:
                    no_time_count += 1
                    logger.debug(f"No time found for '{m['teams']}' (time_text='{m['time']}'): skipping")
                elif window_start <= match_dt <= window_end:
                    filtered_matches.append(m)
                else:
                    skipped += 1
                    logger.debug(f"Skipping '{m['teams']}' at {m['time']} (match_dt={match_dt.strftime('%m-%d %H:%M')}, window={window_start.strftime('%H:%M')}-{window_end.strftime('%H:%M')} UTC)")

            self.stats["matches_filtered"] = len(filtered_matches)
            self.stats["matches_skipped_time"] = skipped
            self.stats["matches_unparseable"] = no_time_count

            logger.info(
                f"Time filter: {len(unique_matches)} total -> "
                f"{len(filtered_matches)} in window ({TIME_WINDOW_PAST}m past / {TIME_WINDOW_FUTURE}m future), "
                f"{skipped} outside window, {no_time_count} unparseable"
            )

            if len(filtered_matches) == 0 and no_time_count == len(unique_matches):
                logger.warning("Could not parse ANY match times -- falling back to first %d matches (time filter disabled)", FALLBACK_MAX_MATCHES)
                return unique_matches[:FALLBACK_MAX_MATCHES]
            return filtered_matches

        except Exception as e:
            logger.error(f"Error fetching matches: {e}")
            self.stats["http_errors"] += 1
            return []

    def get_acestream_links(self, detail_url):
        logger.info(f"Fetching streams from {detail_url}")
        try:
            response = self.session.get(detail_url, timeout=30)
            response.raise_for_status()
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')

            links = set()

            acestream_tags = soup.select("a[href*='acestream://']")
            for tag in acestream_tags:
                links.add(tag['href'])

            acestream_regex = re.compile(r"acestream://[a-zA-Z0-9]+")
            links.update(acestream_regex.findall(html))

            webplayer_regex = re.compile(r"(?:https?:)?//[^\s\"'<>]+webplayer[^\s\"'<]*", re.IGNORECASE)
            webplayer_links = webplayer_regex.findall(html)
            for wl in webplayer_links:
                if wl.startswith("//"):
                    links.add(f"https:{wl}")
                else:
                    links.add(wl)

            js_url_regex = re.compile(r"https?://[^\s\"'<>]+(?:\.m3u8|stream|live|watch|player)", re.IGNORECASE)
            for script_tag in soup.find_all('script'):
                if script_tag.string:
                    links.update(js_url_regex.findall(script_tag.string))

            valid_links = set()
            for link in links:
                if "get.adobe.com" in link or "flashplayer" in link.lower():
                    continue
                if "livetv.sx" in link and "eventinfo" in link:
                    continue
                if link.startswith("http://cdn.live") or link.startswith("https://cdn.live"):
                    continue
                valid_links.add(link)

            self.stats["streams_found"] += len(valid_links)
            logger.info(f"Found {len(valid_links)} valid stream links for {detail_url}")
            return sorted(list(valid_links))

        except Exception as e:
            logger.error(f"Error fetching streams for {detail_url}: {e}")
            self.stats["http_errors"] += 1
            return []


if __name__ == "__main__":
    scraper = LiveTVScraper()
    matches = scraper.get_matches()
    if matches:
        test_match = matches[0]
        print(f"Testing match: {test_match['teams']}")
        streams = scraper.get_acestream_links(test_match['detail_url'])
        print(f"Streams found: {streams}")
