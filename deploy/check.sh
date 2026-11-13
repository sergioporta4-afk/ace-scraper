#!/bin/bash
# Verifica completa dello Xiaomi, da lanciare dal Mac (es. 2-3 minuti dopo un riavvio).
# Uso: ./deploy/check.sh [utente@host]
TARGET="${1:-seu16@192.168.1.58}"
HOST="${TARGET#*@}"
ok()   { printf "  \033[32m✔\033[0m %s\n" "$*"; }
ko()   { printf "  \033[31m✘\033[0m %s\n" "$*"; FAIL=1; }
FAIL=0

echo "== Rete"
ping -c1 -W2 "$HOST" >/dev/null 2>&1 && ok "$HOST risponde al ping" || ko "$HOST non risponde"

echo "== Xiaomi (via SSH)"
ssh -o BatchMode=yes -o ConnectTimeout=5 "$TARGET" 'bash -s' <<'EOF'
ok() { printf "  \033[32m✔\033[0m %s\n" "$*"; }
ko() { printf "  \033[31m✘\033[0m %s\n" "$*"; }
BOOT=$(date -d "$(uptime -s)" +%s)
echo "  acceso da: $(uptime -p | sed 's/up //')  (boot $(uptime -s) UTC)"
echo "  interfaccia in uso: $(ip route show default | awk '{print $5; exit}')"
systemctl is-active -q acestream && ok "acestream.service attivo" || ko "acestream.service NON attivo"
systemctl --user is-active -q ace-playlist-http && ok "server HTTP playlist attivo" || ko "server HTTP playlist NON attivo"
systemctl --user is-active -q ace-scraper.timer && ok "timer aggiornamento attivo (prossimo: $(systemctl --user show ace-scraper.timer -p NextElapseUSecRealtime --value))" || ko "timer NON attivo"
IP=$(getent hosts livetv.sx | awk '{print $1; exit}')
[ -n "$IP" ] && [ "$IP" != "127.0.0.1" ] && ok "DNS ok: livetv.sx -> $IP ($(resolvectl dns | grep -o '1\.1\.1\.1' | head -1 || echo '?'))" || ko "DNS: livetv.sx -> ${IP:-non risolto} (bloccato?)"
RES=$(systemctl --user show ace-scraper.service -p Result --value)
LAST=$(systemctl --user show ace-scraper.service -p ExecMainExitTimestamp --value)
[ "$RES" = success ] && ok "ultimo run scraper: OK ($LAST)" || ko "ultimo run scraper: $RES ($LAST) -> journalctl --user -u ace-scraper"
F=~/ace-scraper/www/playlist.m3u
if [ -f "$F" ]; then
  M=$(stat -c %Y "$F")
  [ "$M" -ge "$BOOT" ] && ok "playlist rigenerata dopo il boot ($(date -d @$M '+%H:%M') UTC, $(grep -c EXTINF "$F") canali)" \
                       || ko "playlist NON aggiornata dopo il boot (ultima: $(date -d @$M '+%d/%m %H:%M') UTC)"
else
  ko "playlist assente"
fi
EOF
[ $? -eq 0 ] || ko "SSH non riuscito"

echo "== Dal Mac / LAN (come la vede la TV)"
CODE=$(curl -s -m5 -o /tmp/_pl.m3u -w '%{http_code}' "http://$HOST:8080/playlist.m3u")
[ "$CODE" = 200 ] && ok "http://$HOST:8080/playlist.m3u -> 200 ($(grep -c EXTINF /tmp/_pl.m3u) canali)" || ko "playlist HTTP -> $CODE"
CODE_HLS=$(curl -s -m5 -o /tmp/_pl_hls.m3u -w '%{http_code}' "http://$HOST:8080/playlist_hls.m3u")
[ "$CODE_HLS" = 200 ] && ok "http://$HOST:8080/playlist_hls.m3u -> 200 ($(grep -c EXTINF /tmp/_pl_hls.m3u) canali)" || ko "playlist HLS HTTP -> $CODE_HLS"
CODE_EPG=$(curl -s -m5 -o /tmp/_epg.xml -w '%{http_code}' "http://$HOST:8080/epg.xml")
[ "$CODE_EPG" = 200 ] && ok "http://$HOST:8080/epg.xml -> 200 ($(grep -c '<programme ' /tmp/_epg.xml) fasce EPG)" || ko "EPG HTTP -> $CODE_EPG"
V=$(curl -s -m5 "http://$HOST:6878/webui/api/service?method=get_version" | grep -o '"version": "[^"]*"')
[ -n "$V" ] && ok "Ace Stream engine raggiungibile ($V)" || ko "Ace Stream engine non raggiungibile su :6878"

echo
[ $FAIL -eq 0 ] && echo "TUTTO OK ✅" || echo "CI SONO PROBLEMI ❌"
