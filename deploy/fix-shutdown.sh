#!/bin/bash
# Fix spegnimento automatico sullo Xiaomi:
# 1. Imposta il fuso orario su Europe/Rome (cosi' 00:00 e' sempre mezzanotte italiana, estate e inverno)
# 2. Imposta il cron di root alle 00:00 con 'systemctl poweroff'
# 3. Corregge /etc/systemd/system/acestream.service (toglie '&' finale e mette TimeoutStopSec=5s)
# 4. Attiva ShutdownWatchdogSec=1min e scarica i driver USB (r8152/ucsi_acpi) prima del poweroff ACPI
#
# Uso (dal Mac):  ssh -t seu16@192.168.1.58 'sudo bash ~/ace-scraper/deploy/fix-shutdown.sh'
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Lancialo con sudo"; exit 1; }

echo "== 1. Fuso orario -> Europe/Rome"
timedatectl set-timezone Europe/Rome
echo "  Ora locale: $(date)"

echo "== 2. Cron di spegnimento alle 00:00 italiane"
(crontab -l 2>/dev/null | grep -v -E 'shutdown|poweroff' || true; echo "0 0 * * * /usr/bin/systemctl poweroff") | crontab -
echo "  Crontab root: $(crontab -l | grep poweroff)"

echo "== 3. Pulizia acestream.service"
cat > /etc/systemd/system/acestream.service <<'EOF'
[Unit]
Description=acestream engine
After=network-online.target

[Service]
Type=simple
User=seu16
ExecStart=/opt/acestream/start-engine --client-console
TimeoutStopSec=5s
KillMode=control-group

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload

echo "== 4. Watchdog hardware di spegnimento + hook anti-blocco USB/ACPI"
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/watchdog.conf <<'EOF'
[Manager]
DefaultTimeoutStopSec=15s
ShutdownWatchdogSec=1min
EOF

cat > /usr/lib/systemd/system-shutdown/xiaomi-usb-poweroff.sh <<'EOF'
#!/bin/sh
if [ "$1" = "poweroff" ] || [ "$1" = "halt" ]; then
  modprobe -r r8152 ucsi_acpi 2>/dev/null || true
fi
EOF
chmod +x /usr/lib/systemd/system-shutdown/xiaomi-usb-poweroff.sh

echo "TUTTO CONFIGURATO ✅ (prossimo spegnimento: stasera alle 00:00 ora italiana)"
