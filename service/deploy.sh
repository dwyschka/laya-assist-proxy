#!/bin/sh
# Entwickelt wird auf der Freigabe, gelaufen wird lokal.
#
# Dieses Skript kopiert den Stand von der Freigabe auf das Geraet und startet den
# Dienst neu. Warum ueberhaupt kopieren:
#   * launchd darf auf diesem Geraet keine Datei von der SMB-Freigabe ausfuehren
#     (Operation not permitted),
#   * und nach einem Neustart ist die Freigabe noch nicht gemountet -- der Dienst
#     wuerde warten, bis sich jemand anmeldet und sie erscheint.
# Lokal faellt beides weg.
#
# Konfiguration wird NICHT ueberschrieben: project.yaml und secrets.local.json
# gehoeren dem Geraet, nicht dem Arbeitsstand. Fehlen sie dort, werden sie beim
# ersten Mal aus der Quelle uebernommen.
set -e

QUELLE=$(cd "$(dirname "$0")/.." && pwd)
ZIEL=${LAYA_ASSIST_TARGET:-$HOME/Services/laya-assist-proxy}
DIENST=de.sensou.laya-webui

mkdir -p "$ZIEL"
rsync -a --delete \
      --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
      --exclude '.venv' \
      --exclude 'project.yaml' --exclude 'secrets.local.json' --exclude 'config.json' \
      "$QUELLE/" "$ZIEL/"

for datei in project.yaml secrets.local.json; do
    if [ ! -f "$ZIEL/$datei" ] && [ -f "$QUELLE/$datei" ]; then
        cp "$QUELLE/$datei" "$ZIEL/$datei"
        echo "  $datei erstmalig uebernommen"
    fi
done
chmod 600 "$ZIEL/secrets.local.json" 2>/dev/null || true

echo "kopiert: $QUELLE -> $ZIEL"

if launchctl print "gui/$(id -u)/$DIENST" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$(id -u)/$DIENST"
    echo "Dienst neu gestartet -- Modell laedt, das dauert bis zu zwei Minuten"
    i=0
    while [ $i -lt 90 ]; do
        sleep 2
        if curl -s -m 2 http://localhost:7788/api/health 2>/dev/null | grep -q '"loaded": true'; then
            echo "bereit: $(curl -s http://localhost:7788/api/health)"
            exit 0
        fi
        i=$((i + 1))
    done
    echo "Achtung: nach 180 s noch nicht bereit -- siehe ~/Library/Logs/laya-webui.err.log"
    exit 1
else
    echo "Dienst nicht installiert -- siehe service/README.md"
fi
