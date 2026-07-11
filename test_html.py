from scraper import LiveTVScraper

s = LiveTVScraper()
resp = s.session.get(s.base_url, timeout=30)
resp.raise_for_status()
with open("test_resp.html", "w", encoding="utf-8") as f:
    f.write(resp.text)
print(f"Downloaded {len(resp.text)} bytes to test_resp.html")
