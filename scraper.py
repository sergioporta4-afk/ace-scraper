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

logger = logging.getLogger(__name__)

TIME_WINDOW_PAST = int(os.getenv("TIME_WINDOW_PAST", "120"))
TIME_WINDOW_FUTURE = int(os.getenv("TIME_WINDOW_FUTURE", "60"))
# Fallback offset (hours) if the page does not tell us its timezone.
DEFAULT_TZ_OFFSET = float(os.getenv("LIVETV_TZ_OFFSET", "1"))
INCLUDE_WEB_LINKS = os.getenv("INCLUDE_WEB_LINKS", "0") == "1"

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
EVENT_ID_RE = re.compile(r"/eventinfo/(\d+)")
ACE_ID_RE = re.compile(r"acestream://([0-9a-fA-F]{40})(?![0-9a-zA-Z])")
TZ_RE = re.compile(r'#timezone"[^>]*>\s*UTC\s*([+-])\s*(\d{1,2})(?::(\d{2}))?', re.IGNORECASE)


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
    """Scrapes the livetv.sx football page (sport id 1).

    Page layout (verified Nov 2026):
      * ``span#upcoming``  -> multi-sport "top events" block; the icon alt is
        "<Sport>. <Competition>" (e.g. "Football. UEFA Nations League").
      * ``table.main``     -> the football listing; icon alt is just the competition.
    Each event row: <tr><td><img alt=...></td><td><a href="/enx/eventinfo/ID_slug/">Teams</a>
    ... <span class="evdesc">13 November at 20:45 (Competition)</span></td></tr>
    """

    def __init__(self, base_url="https://livetv.sx/enx/allupcomingsports/1/"):
        self.base_url = base_url
        self.session = _build_retry_session()
        parsed = urlparse(base_url)
        self.base_origin = f"{parsed.scheme}://{parsed.netloc}"
        self.page_tz = timezone(timedelta(hours=DEFAULT_TZ_OFFSET))
        self.stats = {
            "matches_found": 0,
            "matches_filtered": 0,
            "matches_skipped_time": 0,
            "matches_unparseable": 0,
            "streams_found": 0,
            "streams_valid": 0,
            "http_errors": 0,
            "page_tz": None,
        }

    # ------------------------------------------------------------------ parsing

    def _detect_timezone(self, html):
        m = TZ_RE.search(html)
        if not m:
            logger.warning("Timezone not found on page, assuming UTC%+g", DEFAULT_TZ_OFFSET)
            return
        sign = 1 if m.group(1) == "+" else -1
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0)) * sign
        self.page_tz = timezone(delta)
        logger.info("livetv page timezone: %s", self.page_tz)

    def _parse_match_time(self, text):
        """Return an aware datetime (in page timezone) or None."""
        if not text:
            return None
        tm = re.search(r"(\d{1,2}):(\d{2})", text)
        if not tm:
            return None
        hour, minute = int(tm.group(1)), int(tm.group(2))
        now = datetime.now(self.page_tz)

        dm = re.search(rf"(\d{{1,2}})\s+({MONTHS})", text, re.IGNORECASE)
        try:
            if dm:
                month = datetime.strptime(dm.group(2).capitalize(), "%B").month
                dt = datetime(now.year, month, int(dm.group(1)), hour, minute, tzinfo=self.page_tz)
                # Year rollover (e.g. listing "2 January" while we are in December)
                if dt < now - timedelta(days=180):
                    dt = dt.replace(year=now.year + 1)
                elif dt > now + timedelta(days=180):
                    dt = dt.replace(year=now.year - 1)
                return dt
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None

    @staticmethod
    def _clean_text(text):
        return re.sub(r"\s+", " ", text or "").strip()

    def _parse_row(self, link):
        row = link.find_parent("tr")
        if row is None:
            return None
        m = EVENT_ID_RE.search(link.get("href", ""))
        if not m:
            return None
        event_id = m.group(1)

        img = row.find("img", alt=True)
        alt = self._clean_text(img["alt"]) if img else ""
        icon_src = (img.get("src") or "").strip() if img else ""
        if icon_src.startswith("//"):
            icon_url = f"https:{icon_src}"
        elif icon_src.startswith("/"):
            icon_url = f"{self.base_origin}{icon_src}"
        else:
            icon_url = icon_src
        in_top_block = row.find_parent("span", id="upcoming") is not None

        if in_top_block:
            # Multi-sport block: keep only football rows.
            if not alt.lower().startswith("football."):
                return None
            competition = alt.split(".", 1)[1].strip()
        else:
            competition = alt

        teams_link = None
        for a in row.select("a[href*='/eventinfo/']"):
            if self._clean_text(a.get_text()):
                teams_link = a
                break
        teams = self._clean_text(teams_link.get_text()) if teams_link else ""
        if len(teams) < 3:
            return None

        evdesc = row.select_one("span.evdesc")
        desc = self._clean_text(evdesc.get_text(" ")) if evdesc else ""
        if not competition:
            cm = re.search(r"\(([^()]*)\)\s*$", desc)
            competition = cm.group(1).strip() if cm else ""
        time_text = re.sub(r"\([^()]*\)\s*$", "", desc).strip()

        href = link["href"]
        detail_url = href if href.startswith("http") else f"{self.base_origin}{href}"
        return {
            "event_id": event_id,
            "teams": teams,
            "competition": competition,
            "time_text": time_text,
            "detail_url": detail_url,
            "icon_url": icon_url,
            "is_live": row.find("img", src=re.compile(r"live\.gif")) is not None,
        }

    def get_matches(self):
        logger.info("Fetching matches from %s", self.base_url)
        try:
            response = self.session.get(self.base_url, timeout=30)
            response.raise_for_status()
        except Exception as e:
            logger.error("Error fetching matches: %s", e)
            self.stats["http_errors"] += 1
            return []

        html = response.text
        self._detect_timezone(html)
        self.stats["page_tz"] = str(self.page_tz)
        soup = BeautifulSoup(html, "html.parser")

        events = {}
        for link in soup.select("a[href*='/eventinfo/']"):
            info = self._parse_row(link)
            if not info:
                continue
            prev = events.get(info["event_id"])
            # Prefer the row that carries a full date ("13 November at 20:45").
            if prev is None or (not re.search(MONTHS, prev["time_text"]) and re.search(MONTHS, info["time_text"])):
                info["is_live"] = info["is_live"] or (prev or {}).get("is_live", False)
                events[info["event_id"]] = info
            elif info["is_live"]:
                prev["is_live"] = True

        self.stats["matches_found"] = len(events)

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=TIME_WINDOW_PAST)
        window_end = now + timedelta(minutes=TIME_WINDOW_FUTURE)

        filtered, skipped, unparseable = [], 0, 0
        for ev in events.values():
            dt = self._parse_match_time(ev["time_text"])
            if dt is None:
                unparseable += 1
                if ev["is_live"]:
                    ev["kickoff"] = None
                    filtered.append(ev)
                continue
            ev["kickoff"] = dt
            if ev["is_live"] or window_start <= dt <= window_end:
                filtered.append(ev)
            else:
                skipped += 1

        self.stats["matches_filtered"] = len(filtered)
        self.stats["matches_skipped_time"] = skipped
        self.stats["matches_unparseable"] = unparseable
        logger.info(
            "Football events: %d total -> %d in window (%dm past / %dm future, live always kept), "
            "%d outside window, %d unparseable",
            len(events), len(filtered), TIME_WINDOW_PAST, TIME_WINDOW_FUTURE, skipped, unparseable,
        )
        return filtered

    def get_acestream_links(self, detail_url):
        """Return a sorted list of stream URLs (acestream://<40 hex> first)."""
        try:
            response = self.session.get(detail_url, timeout=30)
            response.raise_for_status()
        except Exception as e:
            logger.error("Error fetching streams for %s: %s", detail_url, e)
            self.stats["http_errors"] += 1
            return []

        html = response.text
        ace_ids = {m.lower() for m in ACE_ID_RE.findall(html)}
        links = [f"acestream://{i}" for i in sorted(ace_ids)]

        if INCLUDE_WEB_LINKS:
            web = set()
            for wl in re.findall(r"(?:https?:)?//[^\s\"'<>]+webplayer[^\s\"'<]*", html, re.IGNORECASE):
                web.add(wl if wl.startswith("http") else f"https:{wl}")
            links += sorted(web)

        self.stats["streams_found"] += len(links)
        logger.info("Found %d stream links for %s", len(links), detail_url)
        return links


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    scraper = LiveTVScraper()
    for m in scraper.get_matches():
        print(m["kickoff"], "|", m["teams"], "|", m["competition"], "| live" if m["is_live"] else "")
