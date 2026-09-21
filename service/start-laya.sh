#!/bin/sh
# Startet laya-assist-proxy unter launchd (siehe de.sensou.laya-webui.plist).
#
# Das Projekt liegt auf einer SMB-Freigabe, die Python-Umgebung lokal. Nach einem
# Neustart ist die Freigabe oft noch nicht gemountet -- dann endet dieses Skript
# mit Fehler und launchd versucht es alle ThrottleInterval Sekunden erneut.
#
# Das Arbeitsverzeichnis entscheidet, wo project.yaml und secrets.local.json
# gesucht werden (siehe settings.project_root), darum das cd vor dem Start.
PROJEKT=/Volumes/home/laya-assist-proxy
PYTHON=/Users/server/.venvs/laya/bin/python

if [ ! -f "$PROJEKT/laya_assist/server.py" ]; then
    echo "$(date '+%F %T') $PROJEKT nicht erreichbar -- warte auf die Freigabe"
    exit 1
fi
if [ ! -x "$PYTHON" ]; then
    echo "$(date '+%F %T') venv fehlt: $PYTHON"
    exit 1
fi

cd "$PROJEKT" || exit 1
exec "$PYTHON" -m laya_assist
