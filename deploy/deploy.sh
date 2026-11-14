#!/bin/bash
# Deploy dal Mac allo Xiaomi: copia il codice, prepara il venv, installa/riavvia i servizi systemd utente.
# Uso: ./deploy/deploy.sh [utente@host]
set -euo pipefail
TARGET="${1:-seu16@192.168.8.102}"
cd "$(dirname "$0")/.."

rsync -av --delete \
  --include='scraper.py' --include='m3u_generator.py' --include='requirements.txt' \
  --include='deploy/' --include='deploy/systemd/' --include='deploy/systemd/*' --include='deploy/install.sh' --include='deploy/fix-dns.sh' --include='deploy/test-ethernet.sh' --include='deploy/check.sh' --include='deploy/fix-shutdown.sh' \
  --exclude='*' ./ "$TARGET:ace-scraper/" 

ssh "$TARGET" 'bash ~/ace-scraper/deploy/install.sh'
