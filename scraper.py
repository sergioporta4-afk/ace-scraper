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
DEFAULT_TZ_OFFSET = float(os.getenv("LIVETV_TZ_OFFSET", "2"))
INCLUDE_WEB_LINKS = os.getenv("INCLUDE_WEB_LINKS", "0") == "1"
DEFAULT_BASE_URL = os.getenv("LIVETV_BASE_URL", "https://livetv.sx/it/allupcomingsports/1/")

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}
MONTHS_PATTERN = "|".join(MONTH_MAP.keys())

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
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    return session


class LiveTVScraper:
    """Scrapes the livetv.sx football page (sport id 1, Italian or English)."""

    def __init__(self, base_url=DEFAULT_BASE_URL):
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

    def _normalize_url(self, src):
        src = (src or "").strip()
        if not src:
            return ""
        if src.startswith("//"):
            return f"https:{src}"
        if src.startswith("/"):
            return f"{self.base_origin}{src}"
        return src

    def _detect_timezone(self, html):
        # Look for "Your current time zone: HH:MM (UTC +N)" / "Fuso orario attuale: HH:MM (UTC +N)"
        # Note: livetv /it/ sometimes prints "UTC +2" in the label while its displayed clock (and
        # all match times) are actually in CET (UTC+1). Comparing the displayed HH:MM with real UTC
        # gives the exact offset used for match times on the page.
        clock_m = re.search(r"(\d{1,2}):(\d{2})\s*\(\s*<a[^>]*#timezone", html, re.IGNORECASE)
        if clock_m:
            page_h, page_m = int(clock_m.group(1)), int(clock_m.group(2))
            now_utc = datetime.now(timezone.utc)
            diff_min = (page_h * 60 + page_m) - (now_utc.hour * 60 + now_utc.minute)
            if diff_min > 720:
                diff_min -= 1440
            elif diff_min < -720:
                diff_min += 1440
            offset_hours = round(diff_min / 30) * 0.5
            self.page_tz = timezone(timedelta(hours=offset_hours))
            logger.info("livetv page timezone (from header clock %02d:%02d): %s", page_h, page_m, self.page_tz)
            return

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

        dm = re.search(rf"(\d{{1,2}})\s+({MONTHS_PATTERN})\b", text, re.IGNORECASE)
        try:
            if dm:
                month = MONTH_MAP[dm.group(2).lower()]
                dt = datetime(now.year, month, int(dm.group(1)), hour, minute, tzinfo=self.page_tz)
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
        icon_url = self._normalize_url(img.get("src")) if img else ""
        in_top_block = row.find_parent("span", id="upcoming") is not None

        if in_top_block:
            if not alt.lower().startswith(("football.", "calcio.")):
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

        detail_url = self._normalize_url(link["href"])
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
            has_month = bool(re.search(rf"\b({MONTHS_PATTERN})\b", info["time_text"], re.IGNORECASE))
            prev_has_month = bool(prev and re.search(rf"\b({MONTHS_PATTERN})\b", prev["time_text"], re.IGNORECASE))
            if prev is None or (not prev_has_month and has_month):
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

    def get_event_details(self, detail_url):
        """Fetch an event page and return streams (with bitrate/lang) + team crests."""
        try:
            response = self.session.get(detail_url, timeout=30)
            response.raise_for_status()
        except Exception as e:
            logger.error("Error fetching streams for %s: %s", detail_url, e)
            self.stats["http_errors"] += 1
            return {"streams": [], "team_logos": []}

        html = response.text
        soup = BeautifulSoup(html, "html.parser")

        # 1. Extract the two team crests (e.g. //cdn.livetv.../img/teams/fullsize/ods/10390.gif)
        team_logos = []
        seen_logos = set()
        for img in soup.select("img[src*='/img/teams/']"):
            url = self._normalize_url(img.get("src"))
            if url and url not in seen_logos:
                seen_logos.add(url)
                team_logos.append(url)
            if len(team_logos) >= 2:
                break

        # 2. Extract acestream links and any per-row metadata (bitrate, language)
        meta_by_id = {}
        for a in soup.select("a[href*='acestream://']"):
            m = ACE_ID_RE.search(a.get("href", ""))
            if not m:
                continue
            ace_id = m.group(1).lower()
            tr = a.find_parent("tr")
            row_text = self._clean_text(tr.get_text(" ")) if tr else ""
            bm = re.search(r"(\d+)\s*kbps", row_text, re.IGNORECASE)
            bitrate = int(bm.group(1)) if bm else 0
            lang = ""
            if tr:
                flag_img = tr.select_one("img[src*='linkflag']")
                if flag_img:
                    lang = self._clean_text(flag_img.get("title") or flag_img.get("alt") or "")
            prev = meta_by_id.get(ace_id)
            if prev is None or bitrate > prev["bitrate"]:
                meta_by_id[ace_id] = {"url": f"acestream://{ace_id}", "bitrate": bitrate, "lang": lang}

        # Fallback regex scan in case any acestream:// link wasn't inside an <a> tag
        for ace_id in {m.lower() for m in ACE_ID_RE.findall(html)}:
            meta_by_id.setdefault(ace_id, {"url": f"acestream://{ace_id}", "bitrate": 0, "lang": ""})

        # Sort highest bitrate first, then by URL for deterministic order
        streams = sorted(meta_by_id.values(), key=lambda s: (-s["bitrate"], s["url"]))

        if INCLUDE_WEB_LINKS:
            web = set()
            for wl in re.findall(r"(?:https?:)?//[^\s\"'<>]+webplayer[^\s\"'<]*", html, re.IGNORECASE):
                web.add(wl if wl.startswith("http") else f"https:{wl}")
            for w in sorted(web):
                streams.append({"url": w, "bitrate": 0, "lang": ""})

        self.stats["streams_found"] += len(streams)
        logger.info("Found %d stream links (%d team logos) for %s", len(streams), len(team_logos), detail_url)
        return {"streams": streams, "team_logos": team_logos}

    def get_acestream_links(self, detail_url):
        """Backwards-compatible helper returning only URL strings."""
        return [s["url"] for s in self.get_event_details(detail_url)["streams"]]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    scraper = LiveTVScraper()
    for m in scraper.get_matches():
        print(m["kickoff"], "|", m["teams"], "|", m["competition"], "| live" if m["is_live"] else "")
