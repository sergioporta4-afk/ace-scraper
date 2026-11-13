#!/bin/bash
# Lo Xiaomi usa il DNS del router (192.168.1.254), che per livetv.sx risponde 127.0.0.1.
# Questo script gli fa usare Cloudflare (1.1.1.1 / 1.0.0.1), come gia' fa il Mac,
# ignorando i DNS annunciati dal router via DHCP/IPv6.
#
# Uso (dal Mac):  ssh -t seu16@192.168.1.58 'sudo bash ~/ace-scraper/deploy/fix-dns.sh'
# Per annullare: sudo rm -r /etc/systemd/network/10-netplan-wlp2s0.network.d && sudo networkctl reload && sudo networkctl reconfigure wlp2s0
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Lancialo con sudo"; exit 1; }

IFACE=wlp2s0
DIR=/etc/systemd/network/10-netplan-$IFACE.network.d
mkdir -p "$DIR"
cat > "$DIR/dns.conf" <<'EOF'
[Network]
DNS=1.1.1.1
DNS=1.0.0.1

[DHCPv4]
UseDNS=false

[DHCPv6]
UseDNS=false

[IPv6AcceptRA]
UseDNS=false
EOF

networkctl reload
networkctl reconfigure "$IFACE"
sleep 5
resolvectl flush-caches || true
echo "DNS attivi:"; resolvectl dns "$IFACE"
echo -n "livetv.sx -> "; getent hosts livetv.sx || echo "NON RISOLTO"
