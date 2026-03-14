import requests
import urllib3
urllib3.disable_warnings()

session = requests.Session()
session.verify = False
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})

url = "https://livetv.sx/enx/allupcomingsports/1/"
response = session.get(url, timeout=30)
with open("test_resp.html", "w", encoding="utf-8") as f:
    f.write(response.text)
print(f"Downloaded {len(response.text)} bytes")
