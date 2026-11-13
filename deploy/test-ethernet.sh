#!/bin/bash
# Test TEMPORANEO della porta Ethernet (adattatore USB Realtek RTL8153), senza toccare il Wi-Fi.
# - accende la scheda, chiede un IP al router via DHCP (route con priorita' bassa: il traffico resta sul Wi-Fi)
# - misura link, ping e velocita' di download via cavo e via Wi-Fi per confronto
# - alla fine rimette tutto com'era (la config di test sta in /run e sparisce comunque al riavvio)
#
# Uso (dal Mac):  ssh -t seu16@192.168.1.58 'sudo bash ~/ace-scraper/deploy/test-ethernet.sh'
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Lancialo con sudo"; exit 1; }

ETH=enx00e04c6960a3
WIFI=wlp2s0
GW=192.168.1.254
TESTCONF=/run/systemd/network/05-test-$ETH.network
SPEED_URL="https://speed.cloudflare.com/__down?bytes=50000000"

cleanup() {
  echo; echo "== Pulizia: ripristino lo stato iniziale"
  rm -f "$TESTCONF"
  networkctl reload
  sleep 1
  ip addr flush dev "$ETH" 2>/dev/null
  ip link set "$ETH" down
  echo "  Ethernet spenta, Wi-Fi invariato: $(ip -br addr show $WIFI | awk '{print $3}')"
}
trap cleanup EXIT

echo "== 1. Collegamento fisico"
ip link set "$ETH" up
for i in $(seq 15); do [ "$(cat /sys/class/net/$ETH/carrier 2>/dev/null)" = 1 ] && break; sleep 1; done
if [ "$(cat /sys/class/net/$ETH/carrier 2>/dev/null)" != 1 ]; then
  echo "  ✘ NESSUN SEGNALE sul cavo: controlla che il cavo sia inserito bene (lato PC e lato router)"
  exit 1
fi
ethtool "$ETH" 2>/dev/null | grep -E "Speed|Duplex|Link detected" | sed 's/^\s*/  /'

echo "== 2. Indirizzo dal router (DHCP)"
cat > "$TESTCONF" <<EOF
[Match]
Name=$ETH

[Network]
DHCP=ipv4
LinkLocalAddressing=no
IPv6AcceptRA=no

[DHCPv4]
UseDNS=false
RouteMetric=2000
EOF
networkctl reload
networkctl reconfigure "$ETH"
IP=""
for i in $(seq 20); do
  IP=$(ip -4 -br addr show "$ETH" | awk '{print $3}' | cut -d/ -f1)
  [ -n "$IP" ] && break; sleep 1
done
if [ -z "$IP" ]; then echo "  ✘ il router non ha dato un indirizzo via cavo"; exit 1; fi
echo "  ✔ IP ottenuto via cavo: $IP   (MAC $(cat /sys/class/net/$ETH/address))"

echo "== 3. Ping al router (20 pacchetti)"
for IF in $ETH $WIFI; do
  R=$(ping -I "$IF" -c 20 -i 0.2 -q "$GW" 2>/dev/null | tail -2 | tr '\n' ' ')
  LOSS=$(echo "$R" | grep -o '[0-9.]*% packet loss')
  AVG=$(echo "$R" | awk -F'/' '/rtt/ {print $5}')
  JIT=$(echo "$R" | awk -F'/' '/rtt/ {print $7}' | cut -d' ' -f1)
  printf "  %-17s perdita %-16s latenza media %6s ms  (jitter %s ms)\n" "$IF" "$LOSS" "$AVG" "$JIT"
done

echo "== 4. Internet via cavo"
CODE=$(curl -s -m 20 --interface "$ETH" -o /dev/null -w '%{http_code}' https://livetv.sx/enx/)
[ "$CODE" = 200 ] && echo "  ✔ livetv.sx raggiungibile via cavo (HTTP $CODE)" || echo "  ✘ livetv.sx via cavo: HTTP $CODE"

echo "== 5. Velocita' download (50 MB da Cloudflare)"
for IF in $ETH $WIFI; do
  BPS=$(curl -s -m 60 --interface "$IF" -o /dev/null -w '%{speed_download}' "$SPEED_URL")
  printf "  %-17s %s Mbit/s\n" "$IF" "$(awk -v b="${BPS:-0}" 'BEGIN{printf "%.0f", b*8/1000000}')"
done
