# ace-scraper

Estrae le partite di calcio da livetv.sx e genera una playlist M3U con i link Ace Stream
serviti dal motore sullo Xiaomi (`192.168.1.58:6878`).

## Come gira

Sullo Xiaomi (Ubuntu, utente `seu16`), con servizi **systemd utente** che partono al boot:

| Unit | Ruolo |
|---|---|
| `acestream.service` (di sistema) | Motore Ace Stream sulla porta 6878 |
| `ace-scraper.timer` → `ace-scraper.service` | Aggiorna la playlist 1 min dopo il boot, poi a :00 e :30 |
| `ace-playlist-http.service` | Serve `~/ace-scraper/www/` sulla porta 8080 |

Playlist per la TV (MPEG-TS standard): **http://192.168.1.58:8080/playlist.m3u**
Playlist parallela HLS (`.m3u8`): **http://192.168.1.58:8080/playlist_hls.m3u**
Statistiche ultimo run: http://192.168.1.58:8080/last_run.json

## Comandi utili (sullo Xiaomi)

```bash
systemctl --user start ace-scraper        # aggiornamento manuale immediato
journalctl --user -u ace-scraper -n 50    # log ultimi run
systemctl --user list-timers              # prossimo aggiornamento
```

## Deploy / aggiornamento (dal Mac)

```bash
./deploy/deploy.sh            # copia il codice, aggiorna il venv, (re)installa i servizi
```

`deploy/fix-dns.sh`: fa usare allo Xiaomi il DNS 1.1.1.1 (quello del router risponde
127.0.0.1 per livetv.sx). Da lanciare una volta con sudo.

## Variabili d'ambiente

`ACESTREAM_IP`, `ACESTREAM_PORT`, `OUTPUT_PATH`, `STATS_PATH`, `TIME_WINDOW_PAST` (120 min),
`TIME_WINDOW_FUTURE` (60 min), `MAX_WORKERS`, `REQUEST_DELAY`, `LIVETV_TZ_OFFSET`
(fallback se la pagina non indica il fuso), `INCLUDE_WEB_LINKS=1` (aggiunge anche i link webplayer).
