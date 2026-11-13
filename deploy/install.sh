#!/bin/bash
# Gira SULLO Xiaomi (lanciato da deploy.sh). Non richiede sudo: usa i servizi systemd dell'utente
# (con "linger" attivo partono al boot anche senza login).
set -euo pipefail
APP="$HOME/ace-scraper"
cd "$APP"

if [ ! -x venv/bin/python ]; then
  rm -rf venv
  # Senza il pacchetto python3-venv (serve sudo) ensurepip non c'e': creo il venv senza pip
  # e ci installo le dipendenze con il pip di sistema (--python).
  python3 -m venv venv 2>/dev/null || { rm -rf venv; python3 -m venv --without-pip venv; }
fi
if [ -x venv/bin/pip ]; then
  venv/bin/pip install -q -r requirements.txt
else
  python3 -m pip --python venv/bin/python install -q --disable-pip-version-check -r requirements.txt
fi
mkdir -p www

loginctl show-user "$USER" -p Linger | grep -q yes || loginctl enable-linger "$USER"

mkdir -p "$HOME/.config/systemd/user"
cp deploy/systemd/*.service deploy/systemd/*.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now ace-playlist-http.service ace-scraper.timer
systemctl --user restart ace-playlist-http.service
echo "Primo aggiornamento della playlist..."
systemctl --user start ace-scraper.service || true
systemctl --user --no-pager status ace-scraper.service ace-playlist-http.service ace-scraper.timer | grep -E '●|Active|Trigger' || true
