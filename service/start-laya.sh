#!/bin/sh
# Startet laya-assist-proxy unter launchd (siehe de.sensou.laya-webui.plist).
#
# Laeuft auf der lokalen Kopie, nicht auf der Freigabe: launchd darf von dort
# nichts ausfuehren, und beim Booten ist sie noch nicht gemountet. Den Stand
# dorthin bringt service/deploy.sh.
#
# Das Arbeitsverzeichnis entscheidet, wo project.yaml und secrets.local.json
# gesucht werden (settings.project_root), darum das cd vor dem Start.
PROJEKT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=/Users/server/.venvs/laya/bin/python

if [ ! -f "$PROJEKT/laya_assist/server.py" ]; then
    echo "$(date '+%F %T') $PROJEKT unvollstaendig -- erst service/deploy.sh laufen lassen"
    exit 1
fi
if [ ! -x "$PYTHON" ]; then
    echo "$(date '+%F %T') venv fehlt: $PYTHON"
    exit 1
fi

cd "$PROJEKT" || exit 1
exec "$PYTHON" -m laya_assist
